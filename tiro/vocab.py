"""Personal dictionary: words the recogniser should know (names, brands, jargon).

Entries are words or names spelled the way they should be typed, or explicit rules "heard -> written"
(e.g. "super bass -> Supabase"). They are used twice:

* **Decoder boosting** (see asr.FastTdt): each entry is compiled into the model's own token pieces, and
  while decoding, paths that spell a dictionary word get a bonus ("GeoGuessr" instead of "Geo Guesser").
* **Correction** (see tiro.correct): entries are the strongest evidence the corrector has that you meant a
  word, and rules are applied as plain lookups.
"""

from __future__ import annotations

import re

from tiro.correct.text import letters

_RULE_SPLIT = re.compile(r"\s*(?:->|=>|→)\s*")
MAX_BOOSTED = 400  # decoder boosting slows down and gets less precise with very long lists


class Vocabulary:
    def __init__(self, words: list[str], rules: list[tuple[str, str]], extra: list[str] | None = None):
        self.words = words  # spellings, dictionary first
        self.rules = rules  # (heard, written)
        self.extra = extra or []  # boosted too, but not part of your dictionary (learned / frequent terms)

    @classmethod
    def from_lines(cls, lines: list[str] | str, extra: list[str] | None = None) -> Vocabulary:
        if isinstance(lines, str):
            lines = lines.splitlines()
        words, rules = [], []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = _RULE_SPLIT.split(line, maxsplit=1)
            if len(parts) == 2 and parts[0] and parts[1]:
                rules.append((parts[0], parts[1]))
                words.append(parts[1])
            else:
                words.append(line)
        seen = set()
        words = [w for w in words if letters(w) and not (w.lower() in seen or seen.add(w.lower()))]
        extra = [w for w in (extra or []) if letters(w) and not (w.lower() in seen or seen.add(w.lower()))]
        return cls(words, rules, extra)

    def __bool__(self) -> bool:
        return bool(self.words or self.rules or self.extra)

    def surface_forms(self) -> list[str]:
        """Spellings to compile into the decoder's boosting trie (as written, plus a lowercase-initial variant)."""
        forms = []
        for w in (self.words + self.extra)[:MAX_BOOSTED]:
            forms.append(w)
            if w.lower() != w:
                forms.append(w[0].lower() + w[1:])
        return forms
