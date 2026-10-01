"""Resumable download of files from a Hugging Face repo into work/hf-cache.

    python -m training.data.hf_fetch dataset hf-audio/open-asr-leaderboard "librispeech/*" "voxpopuli/*"
    python -m training.data.hf_fetch model moonshine-ai/moonshine-streaming-small

Downloads resume after interruption (huggingface_hub keeps .incomplete files). Prints the local folder.
"""
from __future__ import annotations

import sys
import time

from training import common  # noqa: F401  (sets HF_HOME)


def fetch(kind: str, repo: str, patterns: list[str] | None, revision: str | None = None) -> str:
    from huggingface_hub import snapshot_download

    for attempt in range(1, 9):
        try:
            return snapshot_download(repo_id=repo, repo_type=kind, allow_patterns=patterns or None,
                                     revision=revision, max_workers=4)
        except Exception as exc:  # network hiccups: back off and resume
            wait = min(300, 10 * 2 ** attempt)
            print(f"download attempt {attempt} failed: {exc!r}; retrying in {wait}s", flush=True)
            time.sleep(wait)
    raise SystemExit(f"giving up on {repo}")


if __name__ == "__main__":
    kind, repo, *patterns = sys.argv[1:]
    t0 = time.time()
    path = fetch(kind, repo, patterns)
    print(f"done in {time.time() - t0:.0f}s -> {path}", flush=True)
