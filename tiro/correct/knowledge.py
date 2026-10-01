"""Builds the corrector's Knowledge from its sources: your dictionary, taught fixes, history, starter set."""

from __future__ import annotations

import logging
import time
from functools import lru_cache

from tiro.correct.corrector import STOPWORDS, Knowledge
from tiro.correct.history import Correction, Profile
from tiro.correct.lexicon import Lexicon
from tiro.correct.text import common_words, letters, similarity, sound_key, words_of
from tiro.paths import asset
from tiro.vocab import Vocabulary

log = logging.getLogger(__name__)

HISTORY_MIN_COUNT = 2  # a word must turn up this often in your history before it counts as yours
HISTORY_TERMS = 3000  # cap on history terms in the lexicon (most frequent first)
FIXED_CASE_SHARE = 0.7  # a history spelling may fix casing only if you use it this consistently
FORM_MIN_USES = 3  # your usual spelling of a word counts once you've used it this often mid-sentence ...
DOMINANT_SHARE = 0.8  # ... and this consistently


def parse_starter(text: str) -> list[str]:
    """One term per line; '#' starts a comment; blank lines ignored."""
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and letters(line):
            out.append(line)
    return out


@lru_cache(maxsize=1)
def starter_lexicon() -> Lexicon:
    """The built-in starter dictionary (names, brands, places, tech, games...), indexed once."""
    t0 = time.perf_counter()
    lex = Lexicon()
    try:
        terms = parse_starter(asset("words", "starter-dictionary.txt").read_text(encoding="utf-8"))
    except OSError:
        log.warning("starter dictionary missing")
        terms = []
    for term in terms:
        lex.add(term, "starter")
    lex.freeze()
    log.info("starter dictionary: %d terms indexed in %.0f ms", len(lex), (time.perf_counter() - t0) * 1000)
    return lex


def build(dictionary: list[str], corrections: list[Correction], profile: Profile | None,
          starter: Lexicon | None = None, version: int = 0) -> Knowledge:
    """Assemble Knowledge. Runs in the background whenever one of the sources changes."""
    user = Lexicon()
    vocab = Vocabulary.from_lines(dictionary)
    for word in vocab.words:
        user.add(word, "dictionary")
    for heard, written in vocab.rules:
        user.add_rule(heard, written)
        user.add(written, "dictionary")
    for c in corrections:
        own = {letters(w) for w in words_of(c.heard + " " + c.written)}
        taught = {letters(w) for w in words_of(c.context)} - own - STOPWORDS
        user.add_rule(c.heard, c.written, {w for w in taught if len(w) > 2})
    apps: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    forms: dict[str, dict[str, int]] = {}
    dominant: dict[str, str] = {}
    if profile is not None:
        counts = dict(profile.counts)
        for phrase, n in profile.phrases.items():
            counts[letters(phrase)] = max(counts.get(letters(phrase), 0), n)
        forms = {k: dict(v) for k, v in profile.forms.items()}
        for key, f in forms.items():
            total = sum(f.values())
            spelled, n = max(f.items(), key=lambda kv: kv[1])
            if n >= FORM_MIN_USES and n >= DOMINANT_SHARE * total:
                dominant[key] = spelled
        for written, count in profile.notable_terms(HISTORY_MIN_COUNT)[:HISTORY_TERMS]:
            key = letters(written)
            spellings = profile.forms.get(key)
            share = (spellings[written] / max(1, sum(spellings.values()))) if spellings else 1.0
            user.add(written, "history", count=count, contexts=profile.context_words(key),
                     fixed_case=share >= FIXED_CASE_SHARE)
            if key in profile.apps:
                apps[key] = set(profile.apps[key])
    user.freeze()
    return Knowledge(user=user, starter=starter if starter is not None else Lexicon(), apps=apps, counts=counts,
                     forms=forms, dominant=dominant, confusable=confusables(user), profile=profile, version=version)


_confusable_memo: dict[tuple[str, str], frozenset[str]] = {}


def _common_buckets() -> dict[str, list[str]]:
    global _buckets
    if _buckets is None:
        b: dict[str, list[str]] = {}
        for w in common_words():
            b.setdefault(w[0], []).append(w)
            snd = sound_key(w)
            if snd and snd[0] != w[0]:
                b.setdefault("~" + snd[0], []).append(w)
        _buckets = b
    return _buckets


_buckets: dict[str, list[str]] | None = None


def confusables(user: Lexicon) -> dict[str, set[str]]:
    """Ordinary words that look or sound like one of your terms ("shawn" -> {"shaun"}), so tier 0 can
    send them on for a closer look. Memoised per term: rebuilding after a dictation is cheap."""
    try:
        from rapidfuzz import process
        from rapidfuzz.distance import Levenshtein
    except ImportError:  # pragma: no cover
        return {}
    out: dict[str, set[str]] = {}
    buckets = _common_buckets()
    for term in user.terms.values():
        if term.nwords > 1 or len(term.key) < 3 or (term.source == "history" and term.count < 2):
            continue
        memo_key = (term.key, term.written)
        hits = _confusable_memo.get(memo_key)
        if hits is None:
            pool = buckets.get(term.key[0], []) + buckets.get("~" + (term.sound or term.key)[0], [])
            found = set()
            for word, _score, _i in process.extract(term.key, pool, scorer=Levenshtein.normalized_similarity,
                                                     score_cutoff=0.59, limit=None):
                if word != term.key and similarity(word, term.written) >= 0.6:
                    found.add(word)
            hits = frozenset(found)
            _confusable_memo[memo_key] = hits
        for word in hits:
            out.setdefault(word, set()).add(term.key)
    return out
