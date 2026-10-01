"""Resumable URL download (+ optional .tar.bz2/.tar.gz/.zip extraction) into work/models.

    python -m training.tools.fetch_url URL [URL ...] [--dest work/models] [--keep-archive]
"""
from __future__ import annotations

import argparse
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

from training.common import MODELS


def download(url: str, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    name = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
    out = dest / name
    if out.exists():
        return out
    part = out.with_name(out.name + ".part")
    for attempt in range(1, 9):
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers={"User-Agent": "tiro-training/1.0", "Range": f"bytes={have}-"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status != 206:  # server ignored the range: start over
                    have = 0
                    part.unlink(missing_ok=True)
                total = have + int(r.headers.get("Content-Length") or 0)
                t0, last = time.time(), 0.0
                with open(part, "ab") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                        have += len(chunk)
                        if time.time() - last > 10:
                            last = time.time()
                            rate = (have / 1e6) / max(time.time() - t0, 1e-3)
                            print(f"  {name}: {have / 1e6:.0f}/{total / 1e6:.0f} MB ({rate:.1f} MB/s)", flush=True)
            part.rename(out)
            return out
        except Exception as exc:
            wait = min(300, 5 * 2 ** attempt)
            print(f"  {name}: attempt {attempt} failed ({exc!r}); retrying in {wait}s", flush=True)
            time.sleep(wait)
    raise SystemExit(f"giving up on {url}")


def extract(archive: Path, dest: Path) -> None:
    if archive.name.endswith((".tar.bz2", ".tar.gz", ".tgz", ".tar")):
        with tarfile.open(archive) as t:
            t.extractall(dest, filter="data")
    elif archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("urls", nargs="+")
    p.add_argument("--dest", type=Path, default=MODELS)
    p.add_argument("--keep-archive", action="store_true")
    a = p.parse_args()
    for url in a.urls:
        t0 = time.time()
        f = download(url, a.dest)
        if f.name.endswith((".tar.bz2", ".tar.gz", ".tgz", ".tar", ".zip")):
            extract(f, a.dest)
            if not a.keep_archive:
                f.unlink()
        print(f"done {f.name} in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
