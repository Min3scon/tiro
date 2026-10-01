"""Set Tiro's version everywhere it is written down (app, package, installer, launcher, Windows file version).

    python tools/bump_version.py 2.0.3
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    v = sys.argv[1].lstrip("v")
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-(?:alpha|beta|rc)\.\d+)?", v)
    if not m:
        sys.exit(f"not a version: {v}")
    nums = ", ".join(m.groups()[:3]) + ", 0"
    edits = {
        "tiro/__init__.py": [(r'^__version__ = "[^"]+"', f'__version__ = "{v}"')],
        "pyproject.toml": [(r'^version = "[^"]+"', f'version = "{v}"')],
        "installer/TiroSetup/TiroSetup.csproj": [(r"<Version>[^<]+</Version>", f"<Version>{v.split('-')[0]}</Version>")],
        "installer/TiroLauncher/TiroLauncher.csproj": [(r"<Version>[^<]+</Version>",
                                                        f"<Version>{v.split('-')[0]}</Version>")],
        "tools/version_info.txt": [(r"filevers=\([^)]*\)", f"filevers=({nums})"),
                                   (r"prodvers=\([^)]*\)", f"prodvers=({nums})"),
                                   (r"StringStruct\('FileVersion', '[^']+'\)", f"StringStruct('FileVersion', '{v}')"),
                                   (r"StringStruct\('ProductVersion', '[^']+'\)",
                                    f"StringStruct('ProductVersion', '{v}')")],
    }
    for rel, subs in edits.items():
        p = ROOT / rel
        s = p.read_text(encoding="utf-8")
        for pattern, repl in subs:
            s, n = re.subn(pattern, repl, s, count=1, flags=re.M)
            if n != 1:
                sys.exit(f"{rel}: {pattern} not found")
        p.write_text(s, encoding="utf-8")
        print(f"{rel}: {v}")


if __name__ == "__main__":
    main()
