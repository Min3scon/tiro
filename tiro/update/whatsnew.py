"""'What's new': this version's section of CHANGELOG.md (bundled with the app)."""
from __future__ import annotations

import re

from tiro.paths import app_root, asset


def section(version: str) -> str:
    this = re.compile(rf"(?<![\d.]){re.escape(version)}(?![\d.])")  # 2.0.3, not 2.0.30
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
                inside = this.search(line) is not None
                continue
            if inside:
                out.append(line)
        text = "\n".join(out).strip()
        if text:
            return text
    return ""
