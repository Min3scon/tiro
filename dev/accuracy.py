"""Accuracy and speed report for the correction pass, on synthesized speech.

  phrases   72 tricky sentences (9 categories): word error rate before/after, term accuracy
  history   13 ambiguous sentences, each with two different histories: does history pick the right word?
  no-harm   270 ordinary sentences (slang, odd phrasing, names, numbers): how often does correction
            change a word that was already right?
  speed     end of speech -> text typed, correction off vs on (median / p95 overhead)

Usage: python dev/accuracy.py [--device cuda|cpu] [--voices zira,hazel,george] [--sets phrases,history,noharm,speed]
       [--limit N] [--out tests/accuracy/REPORT.md]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import jiwer
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dev"))

from eval_correct import stream_text  # noqa: E402

from tiro.asr import ParakeetEngine  # noqa: E402
from tiro.correct import knowledge  # noqa: E402
from tiro.correct.acoustic import AcousticScorer  # noqa: E402
from tiro.correct.corrector import Corrector  # noqa: E402
from tiro.correct.history import Profile  # noqa: E402
from tiro.vad import SileroVad  # noqa: E402
from tiro.vocab import Vocabulary  # noqa: E402

ACC = ROOT / "tests" / "accuracy"
AUDIO = ACC / "audio"


# ---------------------------------------------------------------------- audio
def synthesize(name: str, items: list[dict], voices: list[str]) -> Path:
    out = AUDIO / name
    out.mkdir(parents=True, exist_ok=True)
    missing = [it for it in items if any(not (out / f"{it['id']}_{v}.wav").exists() for v in voices)]
    if missing:
        spec = ROOT / "dev" / f"_synth_{name}.json"
        spec.write_text(json.dumps([{"id": it["id"], "say": it["say"]} for it in missing]), encoding="utf-8")
        print(f"synthesizing {len(missing)} x {len(voices)} clips for {name}...", flush=True)
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(ROOT / "dev" / "synth.ps1"), "-Json", str(spec), "-Out", str(out),
                        "-Voices", ",".join(voices)], check=True)
        spec.unlink()
    return out


def load(folder: Path, item_id: str, voice: str) -> np.ndarray:
    wav, sr = sf.read(folder / f"{item_id}_{voice}.wav", dtype="float32")
    assert sr == 16000, sr
    return wav


# ---------------------------------------------------------------------- text helpers
def norm(s: str) -> str:
    s = s.replace("’", "'")
    s = re.sub(r"[^\w' ]+", " ", s.lower())
    return " ".join(s.split())


def tokens(s: str) -> list[str]:
    return [t for t in (re.sub(r"^[^\w]+|[^\w]+$", "", w) for w in s.replace("’", "'").split()) if t]


def wer(refs: list[str], hyps: list[str]) -> float:
    return jiwer.wer([norm(r) for r in refs], [norm(h) for h in hyps]) if refs else 0.0


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


# ---------------------------------------------------------------------- pipeline
class Pipeline:
    def __init__(self, device: str, lm: str = "none"):
        self.engine = ParakeetEngine(ROOT / "models" / "parakeet-tdt-0.6b-v2", device=device)
        self.engine.load()
        self.vad = SileroVad(ROOT / "assets" / "vad" / "silero_vad.onnx")
        self.starter = knowledge.starter_lexicon()
        self.scorer = AcousticScorer(self.engine)
        self.lm = None
        if lm != "none":
            from tiro.correct.llm import LanguageScorer

            dev = "cuda" if self.engine.device == "cuda" else "cpu"
            self.lm = LanguageScorer(ROOT / "models" / lm, device=dev, gpu_lock=self.engine._lock)
            self.lm.load()

    def corrector(self, dictionary=(), history=()) -> Corrector:
        profile = Profile.build([(0.0, "", "", t) for t in history]) if history else None
        k = knowledge.build(list(dictionary), [], profile, self.starter)
        c = Corrector(k, scorer=self.scorer)
        c.language = self.lm
        return c

    def run(self, wav: np.ndarray, corrector: Corrector | None, boost: list[str] | None = None, prefetch=True):
        self.engine.set_vocabulary(Vocabulary.from_lines(boost) if boost else None)
        run = corrector.begin() if corrector is not None else None
        if run is not None and not prefetch:
            run.prefetch = lambda *a, **k: None
        t0 = time.perf_counter()
        text, times, raw = stream_text(self.engine, self.vad, wav, run)
        return text, times, " ".join(raw), time.perf_counter() - t0


# ---------------------------------------------------------------------- sets
def phrases_set(p: Pipeline, voices, limit, report):
    items = json.loads((ACC / "phrases.json").read_text(encoding="utf-8"))[:limit]
    folder = synthesize("phrases", items, voices)
    user_terms = sorted({t for it in items for t in it["terms"] if not any(ch.isdigit() for ch in t)
                         and it["category"] not in ("homophones", "slang")})
    configs = {
        "before (no correction)": (None, None),
        "after, out of the box (starter set only)": (p.corrector(), None),
        "after, with your words (dictionary of the test terms)": (p.corrector(user_terms), user_terms),
    }
    results = {}
    for name, (corr, boost) in configs.items():
        refs, hyps, cats, term_hits, term_total = [], [], defaultdict(lambda: ([], [])), 0, 0
        for it in items:
            for v in voices:
                text, _, _, _ = p.run(load(folder, it["id"], v), corr, boost)
                refs.append(it["expect"])
                hyps.append(text)
                cats[it["category"]][0].append(it["expect"])
                cats[it["category"]][1].append(text)
                for t in it["terms"]:
                    term_total += 1
                    term_hits += t in text
        results[name] = {
            "wer": wer(refs, hyps), "terms": (term_hits, term_total),
            "by_category": {c: wer(r, h) for c, (r, h) in sorted(cats.items())},
            "samples": list(zip(refs, hyps)),
        }
        print(f"  phrases / {name}: WER {results[name]['wer'] * 100:.1f}%, terms {term_hits}/{term_total}", flush=True)
    report["phrases"] = results
    report["phrases_user_terms"] = user_terms


def history_set(p: Pipeline, voices, limit, report):
    cases = json.loads((ACC / "history_context.json").read_text(encoding="utf-8"))[:limit]
    folder = synthesize("history", cases, voices)
    rows = []
    for c in cases:
        a_word, b_word = c["ambiguous"]
        ca, cb = p.corrector(history=c["history_a"]), p.corrector(history=c["history_b"])
        for v in voices:
            wav = load(folder, c["id"], v)
            raw, _, _, _ = p.run(wav, None)
            out_a, _, _, _ = p.run(wav, ca)
            out_b, _, _, _ = p.run(wav, cb)
            ok_a = a_word in tokens(out_a) and (a_word == b_word or b_word not in tokens(out_a))
            ok_b = b_word in tokens(out_b) and (a_word == b_word or a_word not in tokens(out_b))
            rows.append({"id": c["id"], "voice": v, "pair": f"{a_word} / {b_word}", "raw": raw,
                         "with_a": out_a, "with_b": out_b, "ok_a": ok_a, "ok_b": ok_b,
                         "raw_a": a_word in tokens(raw), "raw_b": b_word in tokens(raw)})
        print(f"  history {c['id']} ({a_word}/{b_word}): "
              + " ".join(f"{r['voice']}:{'A' if r['ok_a'] else 'a'}{'B' if r['ok_b'] else 'b'}"
                         for r in rows if r["id"] == c["id"]), flush=True)
    report["history"] = rows


def noharm_set(p: Pipeline, voices, limit, report):
    items = json.loads((ACC / "no_harm.json").read_text(encoding="utf-8"))[:limit]
    folder = synthesize("noharm", items, voices)
    # a realistic user: the phrase-test words in the dictionary, and a little history about them
    user_terms = report.get("phrases_user_terms") or []
    corr = p.corrector(user_terms)
    changed = harmful = helpful = sentences = 0
    harmful_rows, by_cat = [], defaultdict(lambda: [0, 0])
    for it in items:
        for v in voices:
            wav = load(folder, it["id"], v)
            out, _, raw, _ = p.run(wav, corr, user_terms or None)
            sentences += 1
            by_cat[it["category"]][1] += 1
            if out == raw:
                continue
            a, b, ref = tokens(raw), tokens(out), set(tokens(it["expect"]))
            import difflib

            hurt = False
            for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
                if op == "equal":
                    continue
                changed += 1
                if all(w in ref for w in a[i1:i2]) and a[i1:i2]:
                    harmful += 1
                    hurt = True
                elif all(w in ref for w in b[j1:j2]):
                    helpful += 1
            if hurt:
                by_cat[it["category"]][0] += 1
                harmful_rows.append({"id": it["id"], "voice": v, "expect": it["expect"], "raw": raw, "out": out})
    report["noharm"] = {"sentences": sentences, "edits": changed, "harmful": harmful, "helpful": helpful,
                        "harmed_sentences": len(harmful_rows), "rows": harmful_rows,
                        "by_category": {k: tuple(v) for k, v in sorted(by_cat.items())}}
    print(f"  no-harm: {len(harmful_rows)}/{sentences} sentences harmed, {helpful} helpful edits", flush=True)


def speed_set(p: Pipeline, voices, limit, report):
    """End of speech -> text: decode the final window, correct it, type it. Correction off vs on."""
    items = json.loads((ACC / "phrases.json").read_text(encoding="utf-8"))[:limit]
    folder = synthesize("phrases", items, voices)
    user_terms = report.get("phrases_user_terms") or []
    corr = p.corrector(user_terms)
    rows = defaultdict(list)
    for it in items:
        for v in voices:
            wav = load(folder, it["id"], v)
            for label, c, pf in (("off", None, True), ("on", corr, True), ("on_cold", corr, False)):
                text, times, _, _ = p.run(wav, c, user_terms, prefetch=pf)
                rows[label + "_commit_ms"].extend(times)
                rows[label + "_final_ms"].append(times[-1] if times else 0.0)
    out = {}
    for key in ("on_commit_ms", "on_final_ms", "on_cold_commit_ms", "on_cold_final_ms"):
        xs = rows[key]
        out[key] = {"median": statistics.median(xs) if xs else 0.0, "p95": pct(xs, 0.95), "max": max(xs or [0.0]),
                    "n": len(xs)}
    report["speed"] = out
    report["speed_device"] = p.engine.device_label
    print(f"  speed: final commit median {out['on_final_ms']['median']:.1f} ms, p95 {out['on_final_ms']['p95']:.1f} ms;"
          f" cold median {out['on_cold_final_ms']['median']:.1f} ms, p95 {out['on_cold_final_ms']['p95']:.1f} ms",
          flush=True)


# ---------------------------------------------------------------------- report
def write_report(report: dict, path: Path, voices: list[str]) -> None:
    L = ["# Tiro accuracy report", "",
         f"Speech model: Parakeet TDT 0.6B v2 on {report.get('device', '?')}. Audio: synthesized with Windows "
         f"voices ({', '.join(voices)}; US and UK accents). Generated by `dev/accuracy.py`.", ""]
    if "phrases" in report:
        ph = report["phrases"]
        L += ["## Tricky phrases (72 sentences x %d voices)" % len(voices), "",
              "Word error rate (lower is better) and how many of the tricky terms came out exactly right.", "",
              "| Setup | WER | Terms right |", "|---|---|---|"]
        for name, r in ph.items():
            L.append(f"| {name} | {r['wer'] * 100:.1f}% | {r['terms'][0]}/{r['terms'][1]} |")
        cats = list(next(iter(ph.values()))["by_category"])
        L += ["", "By category (WER):", "", "| Category | " + " | ".join(ph) + " |",
              "|---|" + "---|" * len(ph)]
        for c in cats:
            L.append(f"| {c} | " + " | ".join(f"{ph[n]['by_category'][c] * 100:.1f}%" for n in ph) + " |")
        L += ["", "Numbers, units and acronyms are scored against written forms (\"1.4 kg\", \"USB-C\"); the "
              "correction pass never reformats text, so that category shows the recogniser's own formatting.", ""]
    if "history" in report:
        rows = report["history"]
        ok_a = sum(r["ok_a"] for r in rows)
        ok_b = sum(r["ok_b"] for r in rows)
        raw_ok = sum(r["raw_a"] for r in rows) + sum(r["raw_b"] for r in rows)
        L += ["## Same audio, different history (13 cases x %d voices)" % len(voices), "",
              "Each sentence is ambiguous (Rust/rust, Shaun/Sean, Teams/teams...). It is run twice: once with a "
              "history that uses one reading, once with a history that uses the other.", "",
              f"- History A reading chosen: **{ok_a}/{len(rows)}**",
              f"- History B reading chosen: **{ok_b}/{len(rows)}**",
              f"- Without correction the recogniser matched the expected reading {raw_ok}/{2 * len(rows)} times.", "",
              "| Case | Voice | Heard | With history A | With history B |", "|---|---|---|---|---|"]
        for r in rows:
            L.append(f"| {r['pair']} | {r['voice']} | {r['raw']} | {'✅' if r['ok_a'] else '❌'} {r['with_a']} | "
                     f"{'✅' if r['ok_b'] else '❌'} {r['with_b']} |")
        L.append("")
    if "noharm" in report:
        nh = report["noharm"]
        rate = nh["harmed_sentences"] / max(1, nh["sentences"]) * 100
        L += ["## Do no harm (270 ordinary sentences x %d voices)" % len(voices), "",
              "Everyday speech, slang, odd phrasing, common names and numbers, with a personal dictionary loaded. "
              "An unwanted change is any edit to a word that was already right.", "",
              f"- Unwanted-change rate: **{rate:.2f}%** of sentences ({nh['harmed_sentences']}/{nh['sentences']})",
              f"- Edits made: {nh['edits']} ({nh['helpful']} fixed a misrecognition, {nh['harmful']} were unwanted)", ""]
        if nh["rows"]:
            L += ["| Expected | Recognised | After correction |", "|---|---|---|"]
            for r in nh["rows"][:30]:
                L.append(f"| {r['expect']} | {r['raw']} | {r['out']} |")
            L.append("")
    if "speed" in report:
        sp = report["speed"]
        L += [f"## Speed ({report.get('speed_device', '')})", "",
              "Time the correction pass adds before the final words are typed (end of speech -> text). "
              "\"Warm\" is how Tiro runs: candidates are prepared while you're still speaking. \"Cold\" disables "
              "that, as a worst case.", "",
              "| | median | p95 | max |", "|---|---|---|---|",
              f"| Final commit, warm | {sp['on_final_ms']['median']:.1f} ms | {sp['on_final_ms']['p95']:.1f} ms | "
              f"{sp['on_final_ms']['max']:.1f} ms |",
              f"| Final commit, cold | {sp['on_cold_final_ms']['median']:.1f} ms | {sp['on_cold_final_ms']['p95']:.1f} ms | "
              f"{sp['on_cold_final_ms']['max']:.1f} ms |",
              f"| Every commit, warm | {sp['on_commit_ms']['median']:.1f} ms | {sp['on_commit_ms']['p95']:.1f} ms | "
              f"{sp['on_commit_ms']['max']:.1f} ms |",
              "", "Targets: median under 30 ms, p95 under 150 ms.", ""]
    path.write_text("\n".join(L), encoding="utf-8")
    print(f"report written to {path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--voices", default="zira,hazel,george")
    ap.add_argument("--sets", default="phrases,history,noharm,speed")
    ap.add_argument("--limit", type=int, default=100000)
    ap.add_argument("--out", default=str(ACC / "REPORT.md"))
    ap.add_argument("--lm", default="none", help="language model folder under models/ (tier 2), or none")
    args = ap.parse_args()
    voices = [v.strip() for v in args.voices.split(",") if v.strip()]
    p = Pipeline(args.device, args.lm)
    report: dict = {"device": p.engine.device_label, "lm": args.lm}
    sets = args.sets.split(",")
    if "phrases" in sets:
        phrases_set(p, voices, args.limit, report)
    else:
        items = json.loads((ACC / "phrases.json").read_text(encoding="utf-8"))
        report["phrases_user_terms"] = sorted({t for it in items for t in it["terms"] if not any(ch.isdigit() for ch in t)
                                               and it["category"] not in ("homophones", "slang")})
    if "history" in sets:
        history_set(p, voices, args.limit, report)
    if "noharm" in sets:
        noharm_set(p, voices, args.limit, report)
    if "speed" in sets:
        speed_set(p, voices, args.limit, report)
    out = Path(args.out)
    (out.with_suffix(".json")).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    write_report(report, out, voices)


if __name__ == "__main__":
    main()
