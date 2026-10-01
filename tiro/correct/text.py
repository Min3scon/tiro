"""Text helpers shared by the correction pass: normalisation, a phonetic key, similarity, common words."""

from __future__ import annotations

import logging
import re
from functools import lru_cache

from tiro.paths import asset

log = logging.getLogger(__name__)

_EDGE_PUNCT = re.compile(r"^([\"'(\[¿¡]*)(.*?)([.,;:!?\"')\]…]*)$")
_WORDISH = re.compile(r"[\w'’-]+", re.UNICODE)


def letters(text: str) -> str:
    """Lowercase letters and digits only: the key used to compare spellings."""
    return re.sub(r"[^\w]|_", "", text.lower())


def split_punct(word: str) -> tuple[str, str, str]:
    """('"', 'Hello', '",') style split of a recognised word into leading punct, core, trailing punct."""
    m = _EDGE_PUNCT.match(word)
    return (m.group(1), m.group(2), m.group(3)) if m else ("", word, "")


def words_of(text: str) -> list[str]:
    return _WORDISH.findall(text)


@lru_cache(maxsize=1)
def _ranks() -> dict[str, int]:
    """Frequency rank (0 = most frequent) of the ~40k most common English words."""
    try:
        words = asset("words", "common-en.txt").read_text(encoding="utf-8").split()
    except OSError:
        log.warning("common word list missing; corrections will be stricter")
        return {}
    ranks: dict[str, int] = {}
    for i, w in enumerate(words):
        key = letters(w)
        if key:
            ranks.setdefault(key, i)
    return ranks


def common_words() -> dict[str, int]:
    """The ~40k most frequent English words (lowercase) -> rank; supports `in` like a set."""
    return _ranks()


def word_rank(word: str) -> int | None:
    """0 for "the", larger for rarer words, None if not among the common words."""
    key = letters(word)
    return 0 if key.isdigit() else _ranks().get(key)


def is_common(word: str) -> bool:
    key = letters(word)
    return bool(key) and (key in _ranks() or key.isdigit())


@lru_cache(maxsize=65536)
def sound_key(text: str) -> str:
    """Rough phonetic skeleton (Metaphone-like): consonant sounds, vowels dropped after the first letter.

    "GeoGuessr", "geo guesser" and "geoguesser" share a key; so do "Kubernetes" and "Cuba Ernets".
    """
    w = re.sub(r"[^a-z]", "", text.lower())
    if not w:
        return ""
    for a, b in (("^kn", "n"), ("^gn", "n"), ("^wr", "r"), ("^ps", "s"), ("^x", "s"), ("mb$", "m")):
        w = re.sub(a, b, w)
    for a, b in (
        ("sch", "sk"), ("tch", "ch"), ("ph", "f"), ("gh", "g"), ("ck", "k"), ("sh", "x"), ("ch", "x"),
        ("th", "0"), ("wh", "w"), ("dg", "j"), ("qu", "kw"), ("q", "k"), ("x", "ks"), ("z", "s"),
        ("c(?=[eiy])", "s"), ("c", "k"), ("g(?=[eiy])", "j"), ("v", "f"),
    ):
        w = re.sub(a, b, w)
    first, rest = w[0], w[1:]
    rest = re.sub(r"[aeiouyw]", "", rest)
    rest = re.sub(r"h", "", rest)
    return re.sub(r"(.)\1+", r"\1", first + rest)


# ARPAbet phones -> one character each. Vowels that accents swap freely share a code (cot/caught, the
# reduced "uh"/"ih"), so "Sean" and "Shaun" or US and UK readings compare as the same sounds.
PHONE_CODES = {
    "AA": "a", "AO": "a", "AH": "a", "AX": "a", "AE": "e", "EH": "e", "IH": "i", "IY": "i", "UH": "u", "UW": "u",
    "ER": "3", "AW": "4", "AY": "5", "EY": "6", "OW": "o", "OY": "7",
    "B": "b", "CH": "c", "D": "d", "DH": "D", "F": "f", "G": "g", "HH": "h", "JH": "j", "K": "k", "L": "l",
    "M": "m", "N": "n", "NG": "N", "P": "p", "R": "r", "S": "s", "SH": "S", "T": "t", "TH": "T", "V": "v",
    "W": "w", "Y": "y", "Z": "z", "ZH": "Z",
}


@lru_cache(maxsize=1)
def _pronunciations() -> dict[str, tuple[str, ...]]:
    import gzip

    try:
        with gzip.open(asset("words", "cmudict.txt.gz"), "rt", encoding="utf-8") as f:
            out = {}
            for line in f:
                word, *codes = line.split()
                out[word] = tuple(codes)
            return out
    except OSError:
        log.warning("pronunciation dictionary missing; using spelling-based sound keys only")
        return {}


def phones(text: str) -> tuple[str, ...] | None:
    """Pronunciations of a word or a run of words (one code per phone), or None if any word is unknown."""
    prons = _pronunciations()
    if not prons:
        return None
    combos = [""]
    for raw in text.split():
        w = re.sub(r"[^a-z'.-]", "", raw.lower().replace("’", "'")).strip(".-")
        p = prons.get(w) or (prons.get(w[:-2]) if w.endswith("'s") else None)
        if not p:
            return None
        if w.endswith("'s") and w not in prons:
            p = tuple(x + "z" for x in p)
        combos = [c + v for c in combos for v in p][:4]
    return tuple(combos)


def phone_similarity(a: str, b: str) -> float | None:
    """Similarity of how two spellings are pronounced (1.0 = homophones), None if either is unknown."""
    pa, pb = phones(a), phones(b)
    if not pa or not pb:
        return None
    return max(ratio(x, y) for x in pa for y in pb)


def ratio(a: str, b: str) -> float:
    """Normalised Levenshtein similarity in [0, 1]."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    try:
        from rapidfuzz.distance import Levenshtein

        return float(Levenshtein.normalized_similarity(a, b))
    except ImportError:  # pragma: no cover - tiny pure-Python fallback
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            prev = cur
        return 1.0 - prev[-1] / max(len(a), len(b))


@lru_cache(maxsize=65536)
def similarity(heard: str, written: str) -> float:
    """How alike a heard span and a written form look and sound (0..1). Sound comes from the pronunciation
    dictionary when it knows both, otherwise from a spelling-based key."""
    a, b = letters(heard), letters(written)
    if not a or not b:
        return 0.0
    char = ratio(a, b)
    phon = phone_similarity(heard, written)
    if phon is None:
        phon = ratio(sound_key(a), sound_key(b))
    return max(char, (char + phon) / 2)


def homophones(heard: str, written: str) -> bool:
    """Pronounced the same (per the pronunciation dictionary), e.g. "Sean" / "Shaun"."""
    return phone_similarity(heard, written) == 1.0
