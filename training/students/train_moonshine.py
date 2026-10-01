"""Fine-tune / distil a Moonshine Streaming student on teacher-labelled data.

    python -m training.students.train_moonshine --run small-r1 --student moonshine-ai/moonshine-streaming-small \
        --manifest round1 [--teacher work/runs/medium-r1/best --kd-alpha 0.5 --kd-temp 2] [--steps 20000]

Loss = (1 - a) * cross-entropy on the teacher's transcript + a * T^2 * KL(teacher || student) at temperature T,
where the KL teacher must share the student's tokenizer (another Moonshine model). Checkpoints every
--save-every steps (model + optimiser + schedule + RNG) and resumes from the newest one automatically.
Every --eval-every steps the dev slice is decoded and scored; the best checkpoint is kept as <run>/best.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import random
import shutil
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf
import torch
import torch.nn.functional as F

from training.common import DATA, LOGS, RUNS, TRAIN, log, read_jsonl
from training.eval import normalize, sets, wer

BOS, EOS, PAD = 1, 2, 0
STRIDE = 80  # the frontend consumes whole 5 ms frames
MANIFESTS = DATA / "manifests"


# ------------------------------------------------------------------------------------------------ data
class ShardStream(torch.utils.data.IterableDataset):
    """Streams (audio, tokens) batches: shards in a seeded random order, rows shuffled in a buffer, batches
    built from length-sorted groups so padding stays small. Each DataLoader worker reads its own shards."""

    def __init__(self, manifests: list[tuple[str, float]], tokenizer, batch_sec: float, max_sec: float,
                 augment: bool, seed: int, epoch_offset: int = 0):
        super().__init__()
        self.groups = []  # (weight, {shard: {id: (text, dur)}})
        for name, weight in manifests:
            by_shard = defaultdict(dict)
            for r in read_jsonl(MANIFESTS / f"{name}.jsonl"):
                if r["duration"] <= max_sec:
                    by_shard[r["shard"]][r["id"]] = (r["text"], r["duration"])
            self.groups.append((weight, dict(by_shard)))
        self.tok = tokenizer
        self.batch_sec = batch_sec
        self.augment = augment
        self.seed = seed
        self.epoch_offset = epoch_offset

    def hours(self) -> float:
        return sum(d for _, g in self.groups for rows in g.values() for _, d in rows.values()) / 3600

    def shard_plan(self, epoch: int) -> list[str]:
        rng = random.Random(self.seed * 1000 + epoch)
        plan = []
        for weight, g in self.groups:
            shards = list(g)
            whole = int(weight)
            plan += shards * whole
            frac = weight - whole
            if frac > 0:
                plan += rng.sample(shards, int(round(frac * len(shards))))
        rng.shuffle(plan)
        return plan

    def __iter__(self):
        from training.students.augment import Augmenter

        info = torch.utils.data.get_worker_info()
        wid, nw = (info.id, info.num_workers) if info else (0, 1)
        aug = Augmenter() if self.augment else None
        lookup = {}
        for _, g in self.groups:
            for shard, rows in g.items():
                lookup.setdefault(shard, {}).update(rows)
        epoch = self.epoch_offset
        while True:
            rng = np.random.default_rng([self.seed, epoch, wid])
            buf = []
            for i, shard in enumerate(self.shard_plan(epoch)):
                if i % nw != wid:
                    continue
                keep = lookup[shard]
                table = pq.read_table(TRAIN / shard, columns=["id", "audio"])
                for uid, blob in zip(table.column("id").to_pylist(), table.column("audio").to_pylist()):
                    if uid not in keep:
                        continue
                    text, _ = keep[uid]
                    audio, _ = sf.read(io.BytesIO(blob), dtype="float32")
                    if aug is not None:
                        audio = aug(audio, rng)
                    ids = [BOS] + self.tok(text, add_special_tokens=False)["input_ids"] + [EOS]
                    buf.append((audio, ids))
                    if len(buf) >= 384:
                        yield from self._emit(buf, rng, keep_tail=True)
            yield from self._emit(buf, rng, keep_tail=False)
            epoch += 1

    def _emit(self, buf, rng, keep_tail):
        buf.sort(key=lambda x: len(x[0]))
        batches, cur = [], []
        for item in buf:
            longest = len(item[0]) / 16000
            if cur and longest * (len(cur) + 1) > self.batch_sec:
                batches.append(cur)
                cur = []
            cur.append(item)
        tail = cur
        if not keep_tail and tail:
            batches.append(tail)
            tail = []
        order = rng.permutation(len(batches))
        for j in order:
            yield collate(batches[j])
        buf[:] = tail


def collate(items):
    width = int(math.ceil(max(len(a) for a, _ in items) / STRIDE) * STRIDE)
    tw = max(len(t) for _, t in items)
    src = torch.zeros(len(items), width)
    mask = torch.zeros(len(items), width, dtype=torch.long)
    dst = torch.full((len(items), tw), PAD, dtype=torch.long)
    for i, (a, t) in enumerate(items):
        src[i, : len(a)] = torch.from_numpy(a)
        mask[i, : len(a)] = 1
        dst[i, : len(t)] = torch.tensor(t)
    return src, mask, dst


# ------------------------------------------------------------------------------------------------ eval
def dev_slice(every_scale: int = 2):
    """A fixed slice of the dev sets (half of dev-mini by default) for in-training evaluation."""
    out = []
    for name, every in sets.DEV_MINI.items():
        for u in sets.utterances(name, every=every * every_scale):
            out.append((name, u))
    return out


@torch.no_grad()
def evaluate(model, processor, dev, device, dtype, batch=16):
    model.eval()
    hyps = {}
    order = sorted(range(len(dev)), key=lambda i: dev[i][1].duration)
    for s in range(0, len(order), batch):
        idx = order[s: s + batch]
        waves = [dev[i][1].audio for i in idx]
        inputs = processor(waves, sampling_rate=16000, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        if "input_values" in inputs:
            inputs["input_values"] = inputs["input_values"].to(dtype)
        max_new = int(max(len(w) for w in waves) / 16000 * 6.5) + 10
        with torch.autocast("cuda", dtype=dtype, enabled=device == "cuda"):
            out = model.generate(**inputs, max_new_tokens=max_new)
        for i, text in zip(idx, processor.batch_decode(out, skip_special_tokens=True)):
            hyps[i] = text
    per_set = defaultdict(lambda: [[], [], []])
    for i, (name, u) in enumerate(dev):
        per_set[name][0].append(u.id)
        per_set[name][1].append(u.text)
        per_set[name][2].append(hyps[i])
    result, all_e, all_w = {}, 0, 0
    for name, (ids, refs, hs) in per_set.items():
        sc = wer.score(ids, refs, hs)
        result[name] = round(sc.wer * 100, 3)
        all_e += int(sc.errors.sum())
        all_w += int(sc.words.sum())
    result["mean"] = round(float(np.mean([v for k, v in result.items()])), 3)
    result["pooled"] = round(100 * all_e / max(all_w, 1), 3)
    model.train()
    return result


# ------------------------------------------------------------------------------------------------ train
def kd_loss(s_logits, t_logits, dst, temp):
    mask = (dst[:, 1:] != PAD)
    s = s_logits[:, :-1][mask].float() / temp
    t = t_logits[:, :-1][mask].float() / temp
    return F.kl_div(F.log_softmax(s, -1), F.log_softmax(t, -1), log_target=True, reduction="batchmean") * temp * temp


def latest_checkpoint(run: Path) -> Path | None:
    ckpts = sorted(run.glob("ckpt-*"), key=lambda p: int(p.name.split("-")[1]))
    return ckpts[-1] if ckpts else None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run", required=True)
    p.add_argument("--student", required=True)
    p.add_argument("--teacher", default="")
    p.add_argument("--kd-alpha", type=float, default=0.5)
    p.add_argument("--kd-temp", type=float, default=2.0)
    p.add_argument("--manifest", required=True, help="name[:weight],... from work/data/manifests")
    p.add_argument("--lr", type=float, default=2e-5)
    p.add_argument("--warmup", type=int, default=500)
    p.add_argument("--steps", type=int, default=20000)
    p.add_argument("--batch-sec", type=float, default=300.0)
    p.add_argument("--max-sec", type=float, default=30.0)
    p.add_argument("--eval-every", type=int, default=1000)
    p.add_argument("--save-every", type=int, default=1000)
    p.add_argument("--freeze-encoder", action="store_true")
    p.add_argument("--no-augment", action="store_true")
    p.add_argument("--grad-ckpt", action="store_true")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--eval-scale", type=int, default=2)
    p.add_argument("--weight-decay", type=float, default=0.01)
    a = p.parse_args()

    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

    run = RUNS / a.run
    run.mkdir(parents=True, exist_ok=True)
    (run / "args.json").write_text(json.dumps(vars(a), indent=1), encoding="utf-8")
    device = "cuda"
    dtype = torch.bfloat16
    torch.manual_seed(a.seed)
    resume = latest_checkpoint(run)
    src_model = str(resume) if resume else a.student
    processor = AutoProcessor.from_pretrained(a.student)
    model = AutoModelForSpeechSeq2Seq.from_pretrained(src_model, dtype=torch.float32).to(device)
    if a.grad_ckpt:
        model.gradient_checkpointing_enable()
    if a.freeze_encoder:
        for prm in model.model.encoder.parameters():
            prm.requires_grad_(False)
    teacher = None
    if a.teacher:
        teacher = AutoModelForSpeechSeq2Seq.from_pretrained(a.teacher, dtype=dtype).to(device).eval()
        for prm in teacher.parameters():
            prm.requires_grad_(False)
    params = [p_ for p_ in model.parameters() if p_.requires_grad]
    opt = torch.optim.AdamW(params, lr=a.lr, weight_decay=a.weight_decay, betas=(0.9, 0.98), fused=True)

    def lr_at(step):
        if step < a.warmup:
            return a.lr * step / max(a.warmup, 1)
        prog = (step - a.warmup) / max(a.steps - a.warmup, 1)
        return a.lr * (0.05 + 0.95 * 0.5 * (1 + math.cos(math.pi * min(prog, 1.0))))

    step = 0
    best = {"pooled": float("inf")}
    if resume:
        state = torch.load(resume / "trainer.pt", map_location="cpu", weights_only=False)
        opt.load_state_dict(state["opt"])
        step = state["step"]
        best = state.get("best", best)
        log(a.run, f"resumed from {resume.name} at step {step}")

    manifests = []
    for item in a.manifest.split(","):
        name, _, w = item.partition(":")
        manifests.append((name, float(w or 1.0)))
    data = ShardStream(manifests, processor.tokenizer, a.batch_sec, a.max_sec, not a.no_augment, a.seed,
                       epoch_offset=step // 1000)
    log(a.run, f"training data: {data.hours():.0f} h from {manifests}; student {a.student}; "
        f"teacher {a.teacher or '-'}; {sum(p_.numel() for p_ in params) / 1e6:.1f}M trainable params")
    loader = torch.utils.data.DataLoader(data, batch_size=None, num_workers=a.workers, persistent_workers=True,
                                         prefetch_factor=4, pin_memory=True)
    dev = dev_slice(a.eval_scale)
    log(a.run, f"dev slice: {len(dev)} utterances")
    if step == 0:
        res = evaluate(model, processor, dev, device, dtype)
        log(a.run, f"step 0 dev WER {res}")
        with open(run / "metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"step": 0, "dev": res}) + "\n")
        best = {"pooled": res["pooled"], "step": 0, "dev": res}

    model.train()
    t0, audio_sec, losses = time.time(), 0.0, defaultdict(float)
    n_loss = 0
    for src, mask, dst in loader:
        if step >= a.steps:
            break
        src, mask, dst = src.to(device, non_blocking=True), mask.to(device, non_blocking=True), dst.to(device)
        for g in opt.param_groups:
            g["lr"] = lr_at(step)
        with torch.autocast("cuda", dtype=dtype):
            out = model(input_values=src, attention_mask=mask, decoder_input_ids=dst, use_cache=False)
            ce = F.cross_entropy(out.logits[:, :-1].float().transpose(1, 2), dst[:, 1:], ignore_index=PAD)
            loss = ce
            if teacher is not None and a.kd_alpha > 0:
                with torch.no_grad():
                    t_out = teacher(input_values=src.to(dtype), attention_mask=mask, decoder_input_ids=dst,
                                    use_cache=False)
                kl = kd_loss(out.logits, t_out.logits, dst, a.kd_temp)
                loss = (1 - a.kd_alpha) * ce + a.kd_alpha * kl
                losses["kl"] += float(kl.detach())
        if not torch.isfinite(loss):
            log(a.run, f"step {step}: non-finite loss, batch skipped")
            opt.zero_grad(set_to_none=True)
            continue
        loss.backward()
        gn = torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        opt.zero_grad(set_to_none=True)
        step += 1
        losses["ce"] += float(ce.detach())
        losses["gnorm"] += float(gn)
        n_loss += 1
        audio_sec += float(mask.sum()) / 16000
        if step % 50 == 0:
            el = time.time() - t0
            msg = {k: round(v / n_loss, 4) for k, v in losses.items()}
            msg.update(step=step, lr=lr_at(step), audio_x=round(audio_sec / max(el, 1e-3), 1),
                       gpu_gb=round(torch.cuda.max_memory_allocated() / 2**30, 2))
            with open(run / "metrics.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(msg) + "\n")
            (LOGS / f"{a.run}.status").write_text(
                f"step {step}/{a.steps}, loss {msg['ce']:.3f}, {msg['audio_x']}x real time, best dev WER "
                f"{best['pooled']:.2f}% (step {best.get('step', 0)})", encoding="utf-8")
            losses.clear()
            n_loss = 0
            t0, audio_sec = time.time(), 0.0
        paused = (LOGS / f"{a.run}.stop").exists()  # the scheduler's "window is closing" signal
        if step % a.save_every == 0 or step == a.steps or paused:
            ck = run / f"ckpt-{step}"
            model.save_pretrained(ck)
            torch.save({"opt": opt.state_dict(), "step": step, "best": best}, ck / "trainer.pt")
            for old in sorted(run.glob("ckpt-*"), key=lambda q: int(q.name.split("-")[1]))[:-2]:
                shutil.rmtree(old, ignore_errors=True)
            if paused:
                log(a.run, f"paused at step {step} (checkpoint saved); resumes in the next allowed window")
                os._exit(3)
        if step % a.eval_every == 0 or step == a.steps:
            res = evaluate(model, processor, dev, device, dtype)
            improved = res["pooled"] < best["pooled"]
            log(a.run, f"step {step} dev WER {res}{' (best)' if improved else ''}")
            with open(run / "metrics.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"step": step, "dev": res}) + "\n")
            if improved:
                best = {"pooled": res["pooled"], "step": step, "dev": res}
                shutil.rmtree(run / "best", ignore_errors=True)
                model.save_pretrained(run / "best")
                processor.save_pretrained(run / "best")
                (run / "best" / "dev.json").write_text(json.dumps(best, indent=1), encoding="utf-8")
    log(a.run, f"finished at step {step}; best dev WER {best}")
    os._exit(0)  # DataLoader workers can keep a finished run alive on Windows


if __name__ == "__main__":
    main()
