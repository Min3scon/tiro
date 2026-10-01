"""Synthesise the sentence list with many TTS voices into training shards (same format as the real corpora).

    python -m training.synth.tts [--sentences work/data/synth/sentences.jsonl] [--workers 4]

Voices (all run locally through sherpa-onnx; none of the test-set voices are used):
  Piper LibriTTS-R (904 US speakers, CC-BY-4.0 data), Piper VCTK (109 UK and other accents, CC-BY-4.0 data),
  Piper CMU ARCTIC (18 speakers, free licence), Kokoro v1.0 (Apache 2.0; American, British and accented voices).
Output: work/data/train/synth-<voice set>/shard-NNNN.parquet (id, audio FLAC, text = the written sentence,
duration, speaker = voice id, source). Resumable per shard.
"""
from __future__ import annotations

import argparse
import io
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import soundfile as sf

from training.common import DATA, LOGS, MODELS, TRAIN, log, read_jsonl

TTS = MODELS / "tts"
SHARD = 2000
SCHEMA = pa.schema([("id", pa.string()), ("audio", pa.binary()), ("text", pa.string()),
                    ("duration", pa.float32()), ("speaker", pa.string()), ("source", pa.string())])

# (name, weight, number of speakers)
VOICE_SETS = [("libritts_r", 0.55, 904), ("vctk", 0.30, 109), ("arctic", 0.07, 18), ("kokoro", 0.08, 53)]  # Kokoro is ~8x slower on CPU
# Kokoro v1.0 speaker ids 0..27 are American/British English; the rest are voices of other languages, which read
# English with an accent (kept at a lower rate, and dropped by the teacher check when unintelligible).
KOKORO_EN = 28

_tts = {}


def _engine(name: str):
    import sherpa_onnx as so

    if name in _tts:
        return _tts[name]
    if name == "kokoro":
        d = TTS / "kokoro-multi-lang-v1_0"
        model = so.OfflineTtsModelConfig(kokoro=so.OfflineTtsKokoroModelConfig(
            model=str(d / "model.onnx"), voices=str(d / "voices.bin"), tokens=str(d / "tokens.txt"),
            data_dir=str(d / "espeak-ng-data"), dict_dir=str(d / "dict"),
            lexicon=f"{d / 'lexicon-us-en.txt'},{d / 'lexicon-zh.txt'}"), num_threads=2)
    else:
        folder = {"libritts_r": "vits-piper-en_US-libritts_r-medium", "vctk": "vits-piper-en_GB-vctk-medium",
                  "arctic": "vits-piper-en_US-arctic-medium"}[name]
        d = TTS / folder
        onnx = next(d.glob("*.onnx"))
        model = so.OfflineTtsModelConfig(vits=so.OfflineTtsVitsModelConfig(
            model=str(onnx), tokens=str(d / "tokens.txt"), data_dir=str(d / "espeak-ng-data")), num_threads=2)
    _tts[name] = so.OfflineTts(so.OfflineTtsConfig(model=model, max_num_sentences=1))
    return _tts[name]


def synth_shard(rows: list[dict], out: Path, seed: int) -> tuple[int, float]:
    import soxr

    rng = np.random.default_rng(seed)
    names = [v[0] for v in VOICE_SETS]
    weights = np.array([v[1] for v in VOICE_SETS])
    weights /= weights.sum()
    ids, blobs, texts, durs, spk, srcs = [], [], [], [], [], []
    for r in rows:
        name = names[int(rng.choice(len(names), p=weights))]
        n_spk = dict((v[0], v[2]) for v in VOICE_SETS)[name]
        sid = int(rng.integers(0, KOKORO_EN)) if name == "kokoro" and rng.random() < 0.8 else int(rng.integers(0, n_spk))
        speed = float(rng.uniform(0.88, 1.15))
        try:
            audio = _engine(name).generate(r["say"], sid=sid, speed=speed)
        except Exception:
            continue
        x = np.asarray(audio.samples, dtype=np.float32)
        if len(x) < audio.sample_rate * 0.4:
            continue
        x = soxr.resample(x, audio.sample_rate, 16000).astype(np.float32)
        # natural edges: 50-400 ms of (very quiet) room tone before and after
        pre, post = (int(rng.uniform(0.05, 0.4) * 16000) for _ in range(2))
        floor = rng.standard_normal(pre + post + len(x)).astype(np.float32) * float(rng.uniform(1e-4, 2e-3))
        floor[pre: pre + len(x)] += x
        peak = float(np.max(np.abs(floor)))
        floor *= float(rng.uniform(0.3, 0.9)) / max(peak, 1e-6)
        buf = io.BytesIO()
        sf.write(buf, floor, 16000, format="FLAC", subtype="PCM_16")
        ids.append(f"synth/{r['id']}")
        blobs.append(buf.getvalue())
        texts.append(r["text"])
        durs.append(len(floor) / 16000)
        spk.append(f"{name}:{sid}")
        srcs.append(f"synth-{name}")
    table = pa.table({"id": ids, "audio": blobs, "text": texts, "duration": durs, "speaker": spk,
                      "source": srcs}, schema=SCHEMA)
    tmp = out.with_suffix(".tmp")
    pq.write_table(table, tmp, compression="none")
    os.replace(tmp, out)
    return len(ids), sum(durs) / 3600


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sentences", type=Path, default=DATA / "synth" / "sentences.jsonl")
    p.add_argument("--name", default="synth")
    p.add_argument("--workers", type=int, default=4)
    a = p.parse_args()
    rows = list(read_jsonl(a.sentences))
    out_dir = TRAIN / a.name
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for i in range(0, len(rows), SHARD):
        out = out_dir / f"shard-{i // SHARD:04d}.parquet"
        if not out.exists():
            jobs.append((rows[i: i + SHARD], out, 1000 + i // SHARD))
    log("tts", f"{len(rows)} sentences, {len(jobs)} shards to synthesise with {a.workers} workers")
    done_h = 0.0
    t0 = time.time()
    with ProcessPoolExecutor(a.workers) as pool:
        futs = {pool.submit(synth_shard, *j): j[1].name for j in jobs}
        for k, f in enumerate(as_completed(futs), 1):
            n, h = f.result()
            done_h += h
            log("tts", f"[{k}/{len(jobs)}] {futs[f]}: {n} utts, {h:.1f} h ({done_h / max((time.time() - t0) / 3600, 1e-6):.0f} h/hour)")
            (LOGS / "tts.status").write_text(f"{k}/{len(jobs)} shards, {done_h:.0f} h of speech", encoding="utf-8")
    log("tts", "done")


if __name__ == "__main__":
    main()
