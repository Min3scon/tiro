"""Measure the off-the-shelf Moonshine Streaming rungs in the native engine (run by the scheduler).

For each size (tiny, small, medium): export to ONNX, quantise (q4 decoder, int8 encoder), then
  * dev-mini accuracy, real-time factor and peak RAM with 4 threads (bench_one in its own process);
  * personal vocabulary (tests/accuracy/phrases.json) with and without the dictionary;
  * do-no-harm (tests/accuracy/no_harm.json, 810 clips) with and without the dictionary.
Every step is skipped when its result file already exists, so the suite resumes after a stop.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from training.common import EXPORTS, RESULTS, log

SIZES = ["tiny", "small", "medium"]
PY = sys.executable
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def run(args: list[str]) -> int:
    log("lite_suite", "run " + " ".join(args))
    return subprocess.run([PY, "-u", "-m"] + args, env=ENV).returncode


def main() -> None:
    for size in SIZES:
        base = EXPORTS / f"moonshine-{size}-base"
        fp32, q4 = base / "fp32", base / "q4"
        if not (fp32 / "decoder_kv.onnx").exists():
            tok = next((p for p in [EXPORTS.parent / "models" / f"moonshine-streaming-{size}-fp32" / "tokenizer.bin",
                                    EXPORTS.parent / "models" / "moonshine-streaming-small-fp32" / "tokenizer.bin"]
                        if p.exists()))
            run(["moonshine_voice.lora", "--export", "--model", f"moonshine-ai/moonshine-streaming-{size}",
                 "--output-dir", str(fp32), "--tokenizer-bin", str(tok)])
        if not (q4 / "decoder_kv.onnx").exists():
            run(["training.export.quantize_moonshine", str(fp32), str(q4), "--recipe", "q4"])
        tag = f"core-moonshine-{size}-q4"
        if not (RESULTS / "bench" / tag / "summary.json").exists():
            run(["training.eval.bench_one", "--tag", tag, "--model", f"core:{q4}", "--sets", "dev-mini",
                 "--threads", "4"])
        for test in ("phrases", "noharm"):
            for vocab in (True, False):
                name = f"{test}-moonshine-{size}-q4-{'vocab' if vocab else 'novocab'}"
                if (RESULTS / "vocab" / f"{name}.json").exists():
                    continue
                args = ["training.eval.vocab_eval", str(q4), "--threads", "4", "--set", test, "--tag", name]
                if not vocab:
                    args.append("--no-vocab")
                run(args)
        harm = RESULTS / "vocab" / f"noharm-moonshine-{size}-q4-vocab.json"
        clean = RESULTS / "vocab" / f"noharm-moonshine-{size}-q4-novocab.json"
        if harm.exists() and clean.exists():
            a = json.loads(clean.read_text(encoding="utf-8"))["examples"]
            b = json.loads(harm.read_text(encoding="utf-8"))["examples"]
            changed = sum(x["hyp"] != y["hyp"] for x, y in zip(a, b))
            log("lite_suite", f"{size}: the dictionary changed {changed} of {len(a)} do-no-harm transcripts")
    log("lite_suite", "done")


if __name__ == "__main__":
    main()
