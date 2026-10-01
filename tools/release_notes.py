"""Print the release notes for a version: its CHANGELOG.md section plus the standard install notes.

    python tools/release_notes.py 2.0.3 > notes.md
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def section(version: str) -> str:
    lines = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    out, inside = [], False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = version in line
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def main() -> None:
    version = sys.argv[1].lstrip("v")
    body = section(version) or "Fixes and improvements."
    extra = (ROOT / ".github" / "release-notes.md")
    print(body)
    if extra.is_file():
        print()
        print(extra.read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    main()
