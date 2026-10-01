"""Everything Tiro knows about the words you use, indexed for fast sound-alike lookup.

Terms come from (strongest evidence first): your dictionary, corrections you taught it, words from your
own dictation history, and a built-in starter dictionary. Lookups go through precomputed indexes, so
finding candidates for a span costs microseconds:

* a deletion index over phonetic keys (SymSpell-style): all keys within edit distance 1-2 of a query;
* a character-trigram index for spellings whose sound keys drifted further.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from tiro.correct.text import is_common, letters, ratio, sound_key

log = logging.getLogger(__name__)

# How much a term's presence counts as evidence that the speaker meant it.
WEIGHT = {"dictionary": 1.0, "learned": 1.0, "history": 0.5, "starter": 0.35}


@dataclass
class Term:
    written: str  # canonical spelling, e.g. "GeoGuessr"
    key: str  # letters only, lowercase
    sound: str
    source: str  # dictionary | learned | history | starter
    weight: float
    count: int = 0  # times seen in your history
    nwords: int = 1
    contexts: set[str] = field(default_factory=set)  # words that tend to appear near it (from history)
    fixed_case: bool = True  # its spelling is reliable enough to fix casing ("github" -> "GitHub")


def _deletions(key: str, depth: int) -> set[str]:
    out = {key}
    for d in range(1, depth + 1):
        for idx in combinations(range(len(key)), d):
            out.add("".join(c for i, c in enumerate(key) if i not in idx))
    return out


def _trigrams(key: str) -> set[str]:
    k = f"^{key}$"
    return {k[i : i + 3] for i in range(len(k) - 2)}


class Lexicon:
    def __init__(self):
        self.terms: dict[str, Term] = {}
        self.rules: dict[str, tuple[str, set[str]]] = {}  # heard key -> (written, taught-context words)
        self._sound_exact: dict[str, list[str]] = defaultdict(list)
        self._deletion: dict[str, set[str]] = defaultdict(set)
        self._tri: dict[str, set[str]] = defaultdict(set)
        self.version = 0

    def __len__(self) -> int:
        return len(self.terms)

    # ------------------------------------------------------------------ building
    def add(self, written: str, source: str, *, count: int = 0, contexts: set[str] | None = None,
            fixed_case: bool = True) -> None:
        written = written.strip()
        key = letters(written)
        if len(key) < 2:
            return
        weight = WEIGHT[source]
        if source == "history":
            weight = min(0.95, 0.35 + 0.15 * math.log2(1 + count))
        old = self.terms.get(key)
        if old is not None:
            if weight <= old.weight:  # keep the strongest source, but merge what history knows
                old.count = max(old.count, count)
                if contexts:
                    old.contexts |= contexts
                return
            count = max(count, old.count)
            contexts = (contexts or set()) | old.contexts
        self.terms[key] = Term(written, key, sound_key(written), source, weight, count,
                               len(written.split()), set(contexts or ()), fixed_case)

    def add_rule(self, heard: str, written: str, context: set[str] | None = None) -> None:
        key = letters(heard)
        if key and letters(written):
            self.rules[key] = (written.strip(), set(context or ()))
            self.add(written, "learned")

    def freeze(self) -> Lexicon:
        """Build the lookup indexes (call after adding terms)."""
        self._sound_exact.clear()
        self._deletion.clear()
        self._tri.clear()
        for key, t in self.terms.items():
            if not t.sound:
                continue
            self._sound_exact[t.sound].append(key)
            for d in _deletions(t.sound, 1 if len(t.sound) <= 4 else 2):
                self._deletion[d].add(key)
            for g in _trigrams(key):
                self._tri[g].add(key)
        self.version += 1
        return self

    # ------------------------------------------------------------------ queries
    def get(self, written_or_key: str) -> Term | None:
        return self.terms.get(letters(written_or_key))

    def rule(self, heard: str) -> tuple[str, set[str]] | None:
        return self.rules.get(letters(heard))

    def sound_matches(self, heard: str) -> list[Term]:
        """Terms that sound exactly like `heard` (one or more words joined)."""
        snd = sound_key(letters(heard))
        return [self.terms[k] for k in self._sound_exact.get(snd, ())]

    def neighbours(self, heard: str, limit: int = 12) -> list[Term]:
        """Terms that look or sound close to `heard`, best first."""
        key = letters(heard)
        if len(key) < 2:
            return []
        snd = sound_key(key)
        found: set[str] = set()
        if snd:
            for d in _deletions(snd, 1 if len(snd) <= 4 else 2):
                found |= self._deletion.get(d, set())
        grams = _trigrams(key)
        if grams:
            hits: dict[str, int] = defaultdict(int)
            for g in grams:
                for k in self._tri.get(g, ()):
                    hits[k] += 1
            need = max(2, int(0.45 * len(grams)))
            found |= {k for k, n in hits.items() if n >= need}
        scored = []
        for k in found:
            t = self.terms[k]
            if abs(len(t.key) - len(key)) > max(3, len(t.key) // 2):
                continue
            scored.append((max(ratio(key, t.key), (ratio(key, t.key) + ratio(snd, t.sound)) / 2), t))
        scored.sort(key=lambda x: -x[0])
        return [t for _, t in scored[:limit]]

    def knows(self, word: str) -> bool:
        """Is this word something the speaker uses (or a normal English word)?"""
        key = letters(word)
        return key in self.terms or is_common(word)

    def top_terms(self, n: int, sources: tuple[str, ...] = ("dictionary", "learned", "history")) -> list[Term]:
        pool = [t for t in self.terms.values() if t.source in sources]
        pool.sort(key=lambda t: (-t.weight, -t.count, t.written))
        return pool[:n]
