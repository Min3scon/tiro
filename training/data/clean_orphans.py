"""Delete cache blobs that no snapshot points to any more (left behind when converted sources were removed).

    python -m training.data.clean_orphans [--dry-run]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from training.common import WORK, log

HUB = WORK / "hf-cache" / "hub"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    referenced: set[str] = set()
    for repo in HUB.iterdir():
        snaps = repo / "snapshots"
        if not snaps.is_dir():
            continue
        for root, _dirs, files in os.walk(snaps):
            for f in files:
                path = Path(root) / f
                try:
                    referenced.add(os.path.normcase(str(path.resolve(strict=True))))
                except OSError:
                    pass
    blobs = HUB / "blobs"
    freed = kept = 0
    for root, _dirs, files in os.walk(blobs):
        for f in files:
            path = Path(root) / f
            if f.startswith("."):
                continue
            key = os.path.normcase(str(path.resolve()))
            size = path.stat().st_size
            if key in referenced:
                kept += size
                continue
            freed += size
            if not a.dry_run:
                path.unlink()
    log("clean_orphans", f"{'would free' if a.dry_run else 'freed'} {freed / 2**30:.1f} GB; "
        f"kept {kept / 2**30:.1f} GB still in use ({len(referenced)} linked files)")


if __name__ == "__main__":
    main()
