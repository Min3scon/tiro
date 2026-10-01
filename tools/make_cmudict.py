"""Build assets/words/cmudict.txt.gz from the CMU Pronouncing Dictionary (BSD-style licence).

Source: https://github.com/cmusphinx/cmudict (cmudict.dict). Each phone becomes one character (see
tiro/correct/text.py PHONE_CODES) and stress marks are dropped; up to two pronunciations per word are kept.

Usage: python tools/make_cmudict.py path/to/cmudict.dict
"""

from __future__ import annotations

import gzip
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tiro.correct.text import PHONE_CODES  # noqa: E402


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "dev" / "_cmudict.dict")
    out: dict[str, list[str]] = {}
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        word, *phones = line.split()
        word = re.sub(r"\(\d+\)$", "", word)
        if not re.fullmatch(r"[a-z][a-z'.-]*", word):
            continue
        code = "".join(PHONE_CODES[re.sub(r"\d", "", p)] for p in phones)
        variants = out.setdefault(word, [])
        if code not in variants and len(variants) < 2:
            variants.append(code)
    lines = [f"{w} {' '.join(v)}" for w, v in sorted(out.items())]
    dest = ROOT / "assets" / "words" / "cmudict.txt.gz"
    with gzip.open(dest, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write("\n".join(lines) + "\n")
    print(f"{len(lines)} words -> {dest} ({dest.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
