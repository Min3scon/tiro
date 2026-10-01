"""Free disk space: delete downloaded source files that have already been converted to training shards.

    python -m training.data.clean_cache [--dry-run]

Only files whose converted shard exists in work/data/train are removed (both the snapshot link and the shared
blob it points to). Test/dev sets, models and anything not yet converted are never touched.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from training.common import TRAIN, log


def cached_file(repo: str, filename: str) -> Path | None:
    from huggingface_hub import try_to_load_from_cache

    p = try_to_load_from_cache(repo, filename, repo_type="dataset")
    return Path(p) if isinstance(p, str) else None


def remove_cached(path: Path) -> int:
    """Delete a cached snapshot entry and the blob behind it. Returns bytes freed."""
    freed = 0
    try:
        blob = path.resolve(strict=True)
    except OSError:
        blob = path
    for p in {blob, path}:
        try:
            if p.exists() or p.is_symlink():
                size = p.stat().st_size if p.exists() else 0
                p.unlink()
                freed += size if p == blob else 0
        except OSError as exc:
            log("clean_cache", f"could not delete {p}: {exc}")
    return freed


def main() -> None:
    from training.data.prepare import SOURCES, list_files

    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    total = 0
    for name, src in SOURCES.items():
        out_dir = TRAIN / name
        if not out_dir.exists():
            continue
        for remote in list_files(src):
            shard = out_dir / (remote.replace("/", "__").replace(".parquet", "") + ".parquet")
            if not shard.exists():
                continue
            cp = cached_file(src.repo, remote)
            if cp is None:
                continue
            if a.dry_run:
                try:
                    total += cp.resolve().stat().st_size
                except OSError:
                    pass
                continue
            total += remove_cached(cp)
    log("clean_cache", f"{'would free' if a.dry_run else 'freed'} {total / 2**30:.1f} GB")


if __name__ == "__main__":
    main()
