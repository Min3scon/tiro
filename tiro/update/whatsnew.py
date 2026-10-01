"""'What's new': this version's section of CHANGELOG.md (bundled with the app)."""
from __future__ import annotations

from tiro.paths import app_root, asset


def section(version: str) -> str:
    for path in (asset("CHANGELOG.md"), app_root() / "CHANGELOG.md"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        out: list[str] = []
        inside = False
        for line in lines:
            if line.startswith("## "):
                if inside:
                    break
                inside = version in line
                continue
            if inside:
                out.append(line)
        text = "\n".join(out).strip()
        if text:
            return text
    return ""
