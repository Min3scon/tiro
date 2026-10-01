"""Shared paths and small helpers for the training pipeline.

Everything big lives under WORK (D:\\dictation\\work by default, never committed). Override with TIRO_WORK.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable, Iterator

REPO = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get("TIRO_WORK", REPO / "work"))
DATA = WORK / "data"
TEST = DATA / "test"
DEV = DATA / "dev"
TRAIN = DATA / "train"
LABELS = WORK / "labels"
RUNS = WORK / "runs"
EXPORTS = WORK / "exports"
MODELS = WORK / "models"
LOGS = WORK / "logs"
RESULTS = WORK / "results"

os.environ.setdefault("HF_HOME", str(WORK / "hf-cache"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")

SAMPLE_RATE = 16000


def ensure(*dirs: Path) -> None:
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def read_jsonl(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    """Write atomically (tmp file + rename) so a crash never leaves a half-written manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    n = 0
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    os.replace(tmp, path)
    return n


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def log(name: str, msg: str) -> None:
    """Append a timestamped line to work/logs/<name>.log and echo it."""
    ensure(LOGS)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    with open(LOGS / f"{name}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")
