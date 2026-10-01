"""Run the off-the-shelf candidate benchmark: every model in its own process, one after another.

    python -m training.eval.bench_all [--sets dev-mini] [--only tag1,tag2] [--threads 4]

Resumable: finished utterances are skipped, so re-running after an interruption continues where it stopped.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from training.common import MODELS, WORK, log

MOON = WORK / "hf-cache/hub/models--moonshine-ai--moonshine-voice-assets/snapshots"

CANDIDATES = {
    # tag: backend spec
    # sherpa-onnx 1.13.8 fails on Moonshine v2 inputs longer than ~9 s, so these get 8 s pieces
    "moonshine-v2-tiny": f"sherpa:{MODELS}/sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27?max=8",
    "moonshine-v2-base": f"sherpa:{MODELS}/sherpa-onnx-moonshine-base-en-quantized-2026-02-27?max=8",
    "parakeet-110m-int8": f"sherpa:{MODELS}/sherpa-onnx-nemo-parakeet_tdt_transducer_110m-en-36000-int8",
    "fastconformer-114m-int8": f"sherpa:{MODELS}/sherpa-onnx-nemo-fast-conformer-transducer-en-24500-int8",
    "whisper-base.en-int8": f"sherpa:{MODELS}/sherpa-onnx-whisper-base.en",
    "whisper-distil-small.en-int8": f"sherpa:{MODELS}/sherpa-onnx-whisper-distil-small.en",
    "whisper-small.en-int8": f"sherpa:{MODELS}/sherpa-onnx-whisper-small.en",
    "parakeet-0.6b-v2-int8": f"sherpa:{MODELS}/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
    "parakeet-unified-0.6b-int8": f"sherpa:{MODELS}/sherpa-onnx-nemo-parakeet-unified-en-0.6b-int8-non-streaming",
    # reference (PyTorch, GPU) accuracy of Moonshine Streaming; CPU speed comes from our own runtime later
    "hf-moonshine-stream-tiny": "hf-moonshine:moonshine-ai/moonshine-streaming-tiny",
    "hf-moonshine-stream-small": "hf-moonshine:moonshine-ai/moonshine-streaming-small",
    "hf-moonshine-stream-medium": "hf-moonshine:moonshine-ai/moonshine-streaming-medium",
}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sets", default="dev-mini")
    p.add_argument("--only", default="")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--prefix", default="")
    a = p.parse_args()
    only = [t for t in a.only.split(",") if t]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for tag, spec in CANDIDATES.items():
        if only and tag not in only:
            continue
        log("bench_all", f"start {tag}")
        r = subprocess.run([sys.executable, "-u", "-m", "training.eval.bench_one", "--tag", a.prefix + tag,
                            "--model", spec, "--sets", a.sets, "--threads", str(a.threads)], env=env)
        log("bench_all", f"end {tag} exit={r.returncode}")


if __name__ == "__main__":
    main()
