"""Download training corpora one file at a time and convert them to Tiro's uniform shard format.

    python -m training.data.prepare --source librispeech [--max-files N] [--workers 4]
    python -m training.data.prepare --source all

Output: work/data/train/<source>/<name>.parquet with columns
    id, audio (16 kHz mono FLAC bytes), text (the corpus's own human transcript), duration, speaker, source
Each source file is downloaded, converted, written atomically and its download deleted, so disk use stays small
and an interrupted run resumes at the next unconverted file. Test/dev splits are never touched here.
"""
from __future__ import annotations

import argparse
import fnmatch
import io
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import soundfile as sf

from training.common import TRAIN, log

MIN_SEC, MAX_SEC = 0.5, 30.0


@dataclass(frozen=True)
class Source:
    name: str
    repo: str
    patterns: tuple[str, ...]
    text: str
    id: str
    speaker: str | None
    licence: str
    max_files: int = 0  # 0 = all
    every_file: int = 1  # take every n-th file (spreads a subset over the whole corpus)


SOURCES = {s.name: s for s in [
    Source("librispeech", "openslr/librispeech_asr",
           ("all/train.clean.100/*.parquet", "all/train.clean.360/*.parquet", "all/train.other.500/*.parquet"),
           "text", "id", "speaker_id", "CC-BY-4.0"),
    Source("voxpopuli", "facebook/voxpopuli", ("en/train-*.parquet",), "raw_text", "audio_id", "speaker_id",
           "CC0-1.0", every_file=2),  # half of the 30 files: ~270 h
    Source("ami-ihm", "edinburghcstr/ami", ("ihm/train-*.parquet",), "text", "audio_id", "speaker_id", "CC-BY-4.0"),
    Source("ami-sdm", "edinburghcstr/ami", ("sdm/train-*.parquet",), "text", "audio_id", "speaker_id", "CC-BY-4.0"),
    Source("peoples-speech", "MLCommons/peoples_speech", ("clean/train-*.parquet",), "text", "id", None,
           "CC-BY-4.0 (the 'clean' subset; the CC-BY-SA subsets are not used)", every_file=13),
]}


def list_files(src: Source) -> list[str]:
    from huggingface_hub import HfApi

    files = HfApi().list_repo_files(src.repo, repo_type="dataset")
    out = []
    for pat in src.patterns:
        out += sorted(f for f in files if fnmatch.fnmatch(f, pat))
    out = out[:: src.every_file]
    return out[: src.max_files] if src.max_files else out


def _encode(args) -> tuple | None:
    audio_bytes, text, uid, speaker = args
    try:
        data, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=False)
    except Exception:
        return None
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        import soxr

        data = soxr.resample(data, sr, 16000, quality="HQ")
    dur = len(data) / 16000
    text = (text or "").strip()
    if not text or not (MIN_SEC <= dur <= MAX_SEC):
        return None
    peak = float(np.max(np.abs(data))) if len(data) else 0.0
    if peak < 1e-4:  # digital silence
        return None
    if peak > 1.0:
        data = data / peak
    buf = io.BytesIO()
    sf.write(buf, data, 16000, format="FLAC", subtype="PCM_16")
    return uid, buf.getvalue(), text, dur, speaker


SCHEMA = pa.schema([("id", pa.string()), ("audio", pa.binary()), ("text", pa.string()),
                    ("duration", pa.float32()), ("speaker", pa.string()), ("source", pa.string())])


def convert_file(src: Source, remote: str, pool: ProcessPoolExecutor) -> tuple[int, float]:
    from huggingface_hub import hf_hub_download

    out_dir = TRAIN / src.name
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = remote.replace("/", "__").replace(".parquet", "")
    out = out_dir / f"{stem}.parquet"
    if out.exists():
        return -1, 0.0
    for attempt in range(1, 9):
        try:
            local = hf_hub_download(src.repo, remote, repo_type="dataset")
            break
        except Exception as exc:
            wait = min(300, 10 * 2 ** attempt)
            log("prepare", f"{src.name}: download {remote} failed ({exc!r}); retry in {wait}s")
            time.sleep(wait)
    else:
        raise RuntimeError(f"cannot download {remote}")
    f = pq.ParquetFile(local)
    cols = [c for c in ("audio", src.text, src.id, src.speaker) if c and c in f.schema_arrow.names]
    tmp = out.with_suffix(".tmp")
    writer = pq.ParquetWriter(tmp, SCHEMA, compression="none")  # FLAC is already compressed
    n, hours = 0, 0.0
    for batch in f.iter_batches(batch_size=256, columns=cols):
        rows = batch.to_pylist()
        jobs = [((r["audio"] or {}).get("bytes"), r.get(src.text), str(r.get(src.id)),
                 str(r.get(src.speaker)) if src.speaker else "") for r in rows]
        jobs = [j for j in jobs if j[0]]
        res = [r for r in pool.map(_encode, jobs, chunksize=16) if r]
        if not res:
            continue
        table = pa.table({
            "id": [f"{src.name}/{r[0]}" for r in res], "audio": [r[1] for r in res], "text": [r[2] for r in res],
            "duration": [r[3] for r in res], "speaker": [r[4] for r in res], "source": [src.name] * len(res),
        }, schema=SCHEMA)
        writer.write_table(table)
        n += len(res)
        hours += sum(r[3] for r in res) / 3600
    writer.close()
    os.replace(tmp, out)
    # free the disk: delete the downloaded blob (the snapshot entry is a link to it)
    try:
        real = os.path.realpath(local)
        os.remove(local)
        if os.path.exists(real):
            os.remove(real)
    except OSError:
        pass
    return n, hours


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--max-files", type=int, default=0)
    p.add_argument("--workers", type=int, default=4)
    a = p.parse_args()
    names = list(SOURCES) if a.source == "all" else a.source.split(",")
    with ProcessPoolExecutor(a.workers) as pool:
        for name in names:
            src = SOURCES[name]
            files = list_files(src)
            if a.max_files:
                files = files[: a.max_files]
            log("prepare", f"{name}: {len(files)} files to convert ({src.licence})")
            total_h = 0.0
            for i, remote in enumerate(files, 1):
                t0 = time.time()
                n, h = convert_file(src, remote, pool)
                if n < 0:
                    continue
                total_h += h
                log("prepare", f"{name}: [{i}/{len(files)}] {remote}: {n} utts, {h:.1f} h "
                    f"in {time.time() - t0:.0f}s (this run {total_h:.0f} h)")
            log("prepare", f"{name}: done")


if __name__ == "__main__":
    main()
