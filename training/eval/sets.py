"""Registry of evaluation sets. Every set is a list of parquet files with (audio bytes, text, id, duration).

TEST sets are only used for reporting; DEV sets are used for every model-selection decision (checkpoint picking,
hyperparameters, round-to-round comparisons), so the test numbers stay honest.
"""
from __future__ import annotations

import glob
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf

from training.common import SAMPLE_RATE, WORK

HUB = WORK / "hf-cache" / "hub"


def _snap(repo: str) -> Path:
    base = HUB / ("datasets--" + repo.replace("/", "--")) / "snapshots"
    snaps = sorted(base.glob("*"), key=lambda p: p.stat().st_mtime)
    if not snaps:
        raise FileNotFoundError(f"{repo} is not downloaded (run training.data.hf_fetch)")
    return snaps[-1]


@dataclass(frozen=True)
class EvalSet:
    name: str
    repo: str
    pattern: str
    text_col: str = "text"
    id_col: str = "id"
    split: str = "test"  # "test" or "dev"
    note: str = ""

    def files(self) -> list[str]:
        root = _snap(self.repo)
        files = sorted(glob.glob(str(root / self.pattern)))
        if not files:
            raise FileNotFoundError(f"no files for {self.name}: {root / self.pattern}")
        return files


ESB = "hf-audio/open-asr-leaderboard"
SETS: dict[str, EvalSet] = {s.name: s for s in [
    # --- test (report only) ---
    EvalSet("ls-clean", ESB, "librispeech/test.clean-*.parquet", note="LibriSpeech test-clean (read audiobooks)"),
    EvalSet("ls-other", ESB, "librispeech/test.other-*.parquet", note="LibriSpeech test-other (harder speakers)"),
    EvalSet("ami", ESB, "ami/test-*.parquet", note="AMI meetings, headset mics (conversational)"),
    EvalSet("earnings22", ESB, "earnings22/test-*.parquet", note="Earnings calls (accents, numbers, jargon)"),
    EvalSet("gigaspeech", ESB, "gigaspeech/test-*.parquet", note="Podcasts and YouTube"),
    EvalSet("spgispeech", ESB, "spgispeech/test-*.parquet", note="Financial calls, well recorded"),
    EvalSet("voxpopuli", ESB, "voxpopuli/test-*.parquet", note="European Parliament, many accents"),
    EvalSet("common-voice", ESB, "common_voice/test-*.parquet", note="Crowd-sourced read sentences, consumer mics"),
    EvalSet("fleurs", "google/fleurs", "parquet-data/en_us/test-*.parquet", text_col="raw_transcription", id_col="path",
            note="FLEURS en_us read Wikipedia sentences (has punctuation and case)"),
    # --- dev (model selection) ---
    EvalSet("dev-ls-clean", "openslr/librispeech_asr", "all/validation.clean/*.parquet", split="dev"),
    EvalSet("dev-ls-other", "openslr/librispeech_asr", "all/validation.other/*.parquet", split="dev"),
    EvalSet("dev-voxpopuli", "facebook/voxpopuli", "en/validation-*.parquet", text_col="raw_text",
            id_col="audio_id", split="dev"),
    EvalSet("dev-ami", "edinburghcstr/ami", "ihm/validation-*.parquet", id_col="audio_id", split="dev"),
    EvalSet("dev-fleurs", "google/fleurs", "parquet-data/en_us/validation-*.parquet", text_col="raw_transcription", id_col="path",
            split="dev"),
]}

LEADERBOARD = ["ami", "earnings22", "gigaspeech", "ls-clean", "ls-other", "spgispeech", "voxpopuli"]
DEV = ["dev-ls-clean", "dev-ls-other", "dev-voxpopuli", "dev-ami", "dev-fleurs"]


@dataclass
class Utt:
    id: str
    audio: np.ndarray  # float32 mono 16 kHz
    text: str
    duration: float


def decode(audio_field) -> np.ndarray:
    if isinstance(audio_field, dict) and audio_field.get("bytes") is not None:
        data, sr = sf.read(io.BytesIO(audio_field["bytes"]), dtype="float32", always_2d=False)
    elif isinstance(audio_field, dict) and audio_field.get("array") is not None:
        data, sr = np.asarray(audio_field["array"], dtype=np.float32), audio_field["sampling_rate"]
    else:
        raise ValueError("unsupported audio field")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        import soxr

        data = soxr.resample(data, sr, SAMPLE_RATE, quality="HQ").astype(np.float32)
    return np.ascontiguousarray(data, dtype=np.float32)


def utterances(name: str, limit: int | None = None, every: int = 1, min_words: int = 1) -> Iterator[Utt]:
    """Yield utterances of a set. `every` takes each n-th row (a fixed, reproducible subset)."""
    s = SETS[name]
    k = 0
    n = 0
    for path in s.files():
        f = pq.ParquetFile(path)
        cols = [c for c in ("audio", s.text_col, s.id_col) if c in f.schema_arrow.names]
        for batch in f.iter_batches(batch_size=64, columns=cols):
            for row in batch.to_pylist():
                text = (row.get(s.text_col) or "").strip()
                if len(text.split()) < min_words:
                    continue
                k += 1
                if (k - 1) % every:
                    continue
                audio = decode(row["audio"])
                uid = str(row.get(s.id_col) or f"{name}-{k}")
                yield Utt(uid, audio, text, len(audio) / SAMPLE_RATE)
                n += 1
                if limit and n >= limit:
                    return


def count(name: str) -> int:
    return sum(pq.ParquetFile(p).metadata.num_rows for p in SETS[name].files())

# A fixed ~2,300-utterance slice of the dev sets (about 4.5 hours) for fast model-to-model comparisons.
DEV_MINI = {"dev-ls-clean": 6, "dev-ls-other": 6, "dev-voxpopuli": 4, "dev-ami": 26, "dev-fleurs": 1}


def expand(spec: str) -> list[tuple[str, int]]:
    """'dev-mini' or 'a@4,b' -> [(set name, every), ...]"""
    out = []
    for item in spec.split(","):
        item = item.strip()
        if item == "dev-mini":
            out += list(DEV_MINI.items())
        elif item == "leaderboard":
            out += [(n, 1) for n in LEADERBOARD]
        elif "@" in item:
            name, every = item.split("@")
            out.append((name, int(every)))
        elif item:
            out.append((item, 1))
    return out
