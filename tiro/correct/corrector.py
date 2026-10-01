"""The correction pass: fix words the recogniser probably misheard, using what Tiro knows you say.

Tiers, cheapest first:

0. **Gate.** Words the recogniser was sure of, that are ordinary English or terms you use, pass untouched.
   Costs a few set lookups per word; most dictations stop here.
1. **Lexicon.** For the rest, sound-alike candidates come from your dictionary, fixes you taught Tiro, your
   dictation history and a built-in starter set (precomputed indexes, microseconds). Each candidate is
   weighed by how strongly you use it (and whether its usual neighbours are in this sentence) against
   how well the audio supports it: the recogniser's own decoder re-reads the span's audio as the
   candidate (see acoustic.py, a few ms).
2. **Language model** (optional, see llm.py). Spans tier 1 can't settle are scored in context by a small
   local LLM, which can only pick one of the candidates or keep what you said.

Whatever the tiers propose goes through `validate()`, which enforces the rules in code: only a span of at
most three words may be swapped, only for a term Tiro knows from your dictionary, fixes, history or the
starter set, and only if the two look or sound alike. The output is rebuilt from the recognised words
plus those swaps, so no other word, its order, punctuation or spacing can change.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tiro.asr import Word
from tiro.correct.lexicon import Lexicon, Term
from tiro.correct.text import homophones, is_common, letters, similarity, sound_key, split_punct, word_rank
from tiro.textproc import FILLERS, ends_sentence

log = logging.getLogger(__name__)

MAX_SPAN = 3  # words that may be replaced at once
_POSSESSIVE = re.compile(r"(['’][sS])$")
_WRITTEN_OK = re.compile(r"[\w'’.&+#@/-]+(?: [\w'’.&+#@/-]+){0,2}")
STOPWORDS = frozenset(
    "a an and are as at be but by for from had has have he her his i if in is it its me my no not of on or our "
    "she so that the their them then there they this to too was we were what when which who will with you your "
    "just like yeah ok okay".split()
)


# ====================================================================== tuning
@dataclass(frozen=True)
class Mode:
    gate_conf: float  # words the recogniser was less sure of than this get a closer look
    margin: float  # evidence minus acoustic penalty a change needs (nats)
    min_similarity: float  # floor on spelling/sound similarity for any candidate
    band: float  # below the margin by up to this much: ask the language model (if enabled)


MODES = {
    "strict": Mode(gate_conf=0.60, margin=2.5, min_similarity=0.60, band=1.5),
    "balanced": Mode(gate_conf=0.80, margin=1.0, min_similarity=0.50, band=2.5),
    "aggressive": Mode(gate_conf=0.90, margin=0.0, min_similarity=0.45, band=3.5),
}

# How strongly a term's source says you use it (log-odds, nats).
EVIDENCE = {"dictionary": 7.0, "learned": 7.0, "starter": 0.5, "spelling": 0.5}
LM_WEIGHT = 0.5  # how much the language model's preference counts ...
LM_CAP = 6.0  # ... capped, so it breaks ties rather than overruling the audio and your history
LM_CONFIRM = 1.0  # a change backed only by weak evidence needs the language model to prefer it this much (nats)
CONTEXT_WORD = 0.7  # each word from the term's usual company that appears around the span
CONTEXT_CAP = 2.8
PREF_WEIGHT = 0.7  # per doubling of how much more often you write the term than what was heard
PREF_MIN_USES = 3  # ... counted only once you've used the term this often
FORM_MIN_USES = 3  # your consistent spelling of a word ("Rust", "rust") counts after this many uses
APP_BONUS = 0.5  # you've used the term in this app before
GUARD_CONTEXT = 1.4  # confident ordinary words need at least this much context support (or the LM)
SIM_FLOOR = 0.5  # validator: spelling/sound similarity a swap needs ...
ACOUSTIC_FLOOR = 0.35  # ... or this much if the audio supports the new term well
ACOUSTIC_OK = 3.0  # "supports it well": at most this many nats worse than what was recognised
RULE_FLOOR = 0.3  # fixes you taught explicitly
NO_AUDIO_SCALE = 16.0  # without audio, guess the acoustic penalty from spelling similarity
SPELLING_SLACK = 2.0  # near-identical spellings sound the same: cap the penalty at the no-audio guess + this
CAP_SIMILARITY = 0.8  # ... "near-identical" means at least this similar
ACOUSTIC_VETO = 12.0  # an audio penalty this large rules a candidate out, whatever else supports it
MAX_HOLD = 2  # doubtful words kept back at a commit boundary, so a name isn't split across commits
LATE_TIMEOUT = 1.5  # seconds a late language-model check may take (after that the text stays as typed)


def history_evidence(count: int) -> float:
    return min(7.0, 2.0 + 1.2 * math.log2(1 + count))


# ====================================================================== knowledge
@dataclass
class Knowledge:
    """Everything the corrector may draw on. Immutable once built; swapped as a whole when it changes."""

    user: Lexicon = field(default_factory=Lexicon)  # dictionary, learned fixes, history
    starter: Lexicon = field(default_factory=Lexicon)  # built-in names, brands, places, jargon
    apps: dict[str, set[str]] = field(default_factory=dict)  # term key -> apps you've used it in
    counts: dict[str, int] = field(default_factory=dict)  # word/phrase key -> times in your history
    forms: dict[str, dict[str, int]] = field(default_factory=dict)  # word key -> {spelling: times mid-sentence}
    dominant: dict[str, str] = field(default_factory=dict)  # word key -> the spelling you consistently use
    confusable: dict[str, set[str]] = field(default_factory=dict)  # ordinary word -> your terms that sound like it
    profile: Any = None  # tiro.correct.history.Profile (example sentences for the language model)
    version: int = 0

    def examples(self, written: str, limit: int = 2) -> list[str]:
        """Sentences of yours that use this spelling (for the language model's context)."""
        p = self.profile
        if p is None:
            return []
        out = []
        for sent in reversed(p.examples(letters(written.split()[0]) if " " in written else letters(written))):
            if written in sent:
                out.append(sent)
                if len(out) >= limit:
                    break
        return out

    def term(self, written_or_key: str) -> Term | None:
        key = letters(written_or_key)
        return self.user.terms.get(key) or self.starter.terms.get(key)

    def used(self, written: str) -> int:
        """How often your history has exactly this: one word in this spelling, or this phrase."""
        key = letters(written)
        written = written.strip()
        forms = self.forms.get(key) if " " not in written else None
        if forms and any(f != written for f in forms):
            return forms.get(written, 0)  # you spell it more than one way: count this spelling (mid-sentence)
        if forms is not None and not forms and self.counts.get(key, 0) and written != written.lower():
            return 0  # only ever seen at the start of a sentence: its casing tells us nothing
        return self.counts.get(key, 0)

    def knows(self, word: str) -> bool:
        key = letters(word)
        return key in self.user.terms or key in self.starter.terms or is_common(word)


# ====================================================================== results
@dataclass
class Change:
    start: int  # index into the recognised words
    end: int  # exclusive
    heard: str  # what was recognised (without edge punctuation)
    written: str  # the replacement (without edge punctuation)
    source: str  # rule | dictionary | learned | history | starter
    tier: int = 1
    score: float = 0.0
    delta: float | None = None  # acoustic penalty (nats), None if not re-scored

    @property
    def acoustic_ok(self) -> bool:
        return self.delta is not None and self.delta <= ACOUSTIC_OK


@dataclass
class Result:
    words: list[Word]
    changes: list[Change] = field(default_factory=list)
    tier: int = 0  # highest tier that ran
    ms: float = 0.0  # time spent (on the caller's thread)
    flagged: int = 0  # words tier 0 sent on
    batch: int = -1  # which commit of the dictation this was (for late fixes)
    late: int = 0  # spans still being judged by the language model (may arrive as a late fix)


@dataclass
class _Slot:
    """A recognised word split for matching: '"Cuba' -> lead '"', core 'Cuba', tail ''."""

    word: Word
    lead: str
    core: str
    tail: str  # possessive and trailing punctuation, kept as they are
    key: str


def _slots(words: list[Word]) -> list[_Slot]:
    out = []
    for w in words:
        lead, core, trail = split_punct(w.text)
        m = _POSSESSIVE.search(core)
        if m and len(core) > 3:
            core, trail = core[: m.start()], m.group(1) + trail
        out.append(_Slot(w, lead, core, trail, letters(core)))
    return out


@dataclass
class _Candidate:
    span: tuple[int, int]
    term: Term
    sim: float
    prior: float
    delta: float | None = None
    total: float = -math.inf
    guarded: bool = False  # confident, ordinary words: needs context or the language model to agree
    support: float = 0.0  # context bonus
    vetoed: bool = False  # the audio rules it out

    @property
    def source(self) -> str:
        return self.term.source


def _spelling_term(written: str) -> Term:
    return Term(written, letters(written), sound_key(written), "spelling", 0.5)


# ====================================================================== validator
def validate(words: list[Word], changes: list[Change], knowledge: Knowledge) -> list[Change]:
    """The hard rules. Returns the subset of `changes` that may be applied; everything else is dropped."""
    slots = _slots(words)
    ok: list[Change] = []
    last_end = 0
    for ch in sorted(changes, key=lambda c: (c.start, c.end)):
        if ch.start < last_end or not (0 <= ch.start < ch.end <= len(words)) or ch.end - ch.start > MAX_SPAN:
            continue
        span = slots[ch.start : ch.end]
        # nothing may change inside the span except the words: no punctuation between them
        if any(s.tail for s in span[:-1]) or any(s.lead for s in span[1:]) or any(not s.core for s in span):
            continue
        heard = " ".join(s.core for s in span)
        written = ch.written.strip()
        if heard != ch.heard or not written or written == heard or not _WRITTEN_OK.fullmatch(written):
            continue
        if len(written.split()) > MAX_SPAN or len(written.split()) > (ch.end - ch.start) + 1:
            continue  # may split a run-together word in two, never add words
        if written[-1] in ".,;:!?" or written[0] in "\"'(":
            continue  # punctuation stays the recogniser's
        # (b) evidence: the new spelling is something Tiro knows you (or the starter set) use
        term = knowledge.term(written)
        uses = knowledge.used(written)
        own_spelling = letters(written) == letters(heard) and (
            uses >= FORM_MIN_USES or (ch.tier == 2 and ch.source == "spelling" and uses >= 1))
        if (term is None or term.written != written) and not own_spelling:
            continue
        if ch.source == "rule":
            rule = knowledge.user.rule(heard)
            if rule is None or rule[0] != written:
                continue
        # (a) it must look or sound like what was heard
        sim = similarity(heard, written)
        floor = RULE_FLOOR if ch.source == "rule" else (ACOUSTIC_FLOOR if ch.acoustic_ok else SIM_FLOOR)
        if sim < floor:
            continue
        ok.append(ch)
        last_end = ch.end
    return ok


def apply_changes(words: list[Word], changes: list[Change]) -> list[Word]:
    """Rebuild the word list with the (validated) swaps. Every other word is passed through untouched."""
    if not changes:
        return words
    slots = _slots(words)
    out: list[Word] = []
    i = 0
    for ch in changes:
        out.extend(words[i : ch.start])
        first, last = slots[ch.start], slots[ch.end - 1]
        src = words[ch.start : ch.end]
        tok = (src[0].tok[0], src[-1].tok[1]) if src[0].tok and src[-1].tok else None
        out.append(Word(first.lead + ch.written + last.tail, src[0].start, src[-1].end, 1.0, tok, src[0].ac))
        i = ch.end
    out.extend(words[i:])
    # integrity: outside the swapped spans the text must be exactly what was recognised, in order
    kept_in = [w.text for k, w in enumerate(words) if not any(c.start <= k < c.end for c in changes)]
    kept_out = [w.text for w in out]
    for ch in changes:
        written = slots[ch.start].lead + ch.written + slots[ch.end - 1].tail
        if written in kept_out:
            kept_out.remove(written)
    if kept_in != kept_out:
        log.error("correction integrity check failed; keeping the recognised text")
        return words
    return out


# ====================================================================== corrector
class Corrector:
    """Stateless apart from its knowledge and settings; per-dictation state lives in a CorrectionRun."""

    def __init__(self, knowledge: Knowledge | None = None, *, scorer=None, mode: str = "balanced",
                 gate_conf: float | None = None):
        self.knowledge = knowledge or Knowledge()
        self.scorer = scorer  # acoustic.AcousticScorer (None: estimate from spelling)
        self.language = None  # llm.LanguageScorer for tier 2 (None: off)
        self.enabled = True
        self.set_mode(mode, gate_conf)

    def set_mode(self, mode: str, gate_conf: float | None = None) -> None:
        base = MODES.get(mode, MODES["balanced"])
        self.mode_name = mode if mode in MODES else "balanced"
        self.mode = Mode(gate_conf if gate_conf is not None else base.gate_conf, base.margin,
                         base.min_similarity, base.band)

    def begin(self) -> CorrectionRun:
        return CorrectionRun(self)

    def correct(self, words: list[Word], before: list[str] | None = None, after: list[str] | None = None,
                app: str = "") -> Result:
        """One-off correction (no prefetch cache)."""
        return self.begin().correct(words, before=before, after=after, app=app)

    # ------------------------------------------------------------------ tier 0
    def flags(self, slots: list[_Slot]) -> list[bool]:
        k = self.knowledge
        gate = self.mode.gate_conf
        flagged = [False] * len(slots)
        for i, s in enumerate(slots):
            if not s.key or s.core.lower() in FILLERS:
                continue
            if s.word.conf < gate or not k.knows(s.core) or s.key in k.user.rules:
                flagged[i] = True
                continue
            mine = k.user.terms.get(s.key)
            if mine is not None and mine.written != s.core:  # "github" for your "GitHub"
                flagged[i] = True
                continue
            usual = k.dominant.get(s.key)
            if usual is not None and usual != s.core:  # "Rust" where you always write "rust" (or vice versa)
                flagged[i] = True
                continue
            spellings = k.forms.get(s.key)
            if spellings and s.core not in spellings and s.core.lower() not in spellings:
                flagged[i] = True  # you've only ever written it another way ("mac", "SWIFT")
                continue
            if s.key in k.confusable or self._sounds_like_user_term(s.core, s.key):
                flagged[i] = True  # sounds like one of your words ("Shawn" for your "Shaun")
        # runs of 2-3 words that spell or sound like a known term ("Geo Guesser", "Git Hub", a taught fix)
        for n in (2, 3):
            for i in range(len(slots) - n + 1):
                run = slots[i : i + n]
                if any(not s.key for s in run) or any(s.tail for s in run[:-1]):
                    continue
                joined = "".join(s.key for s in run)
                hit = joined in k.user.rules or joined in k.user.terms or joined in k.starter.terms
                if hit or self._sounds_like_user_term(" ".join(s.core for s in run), joined):
                    for j in range(i, i + n):
                        flagged[j] = True
        return flagged

    def _sounds_like_user_term(self, heard: str, key: str) -> bool:
        snd = sound_key(key)
        if len(snd) < 3:
            return False
        return any(t.key != key for t in self.knowledge.user.sound_matches(heard))

    # ------------------------------------------------------------------ tier 1
    def _keep_cost(self, s: _Slot) -> float:
        """How much evidence it takes to replace this recognised word."""
        k = self.knowledge
        if s.key in k.user.terms:
            return 4.0  # a word you use
        rank = word_rank(s.core)
        if rank is not None:  # the more common the word, the more it takes to replace it
            return 3.0 if rank < 100 else 2.0 if rank < 1000 else 1.5 if rank < 3000 else 0.8
        if s.key in k.starter.terms:
            return 1.0  # a real name or term, just not one of yours
        if s.word.conf >= self.mode.gate_conf:
            return 0.5  # not a word we know, but the recogniser is sure: slang, a new name...
        return -1.5  # not a word, and the recogniser wasn't sure: probably misheard

    def preference(self, written: str, heard: str) -> float:
        """log2 of how much more often you write `written` than `heard` (0 if you rarely write either)."""
        k = self.knowledge
        t = k.used(written)
        if t < PREF_MIN_USES:
            return 0.0
        return max(0.0, math.log2((t + 1) / (k.used(heard) + 1)))

    def _evidence(self, term: Term, context: set[str], app: str, heard: str = "") -> tuple[float, float]:
        if term.source == "history":
            ev = history_evidence(term.count)
        else:
            ev = EVIDENCE.get(term.source, 0.0)
        support = min(CONTEXT_CAP, CONTEXT_WORD * len(term.contexts & context)) if term.contexts else 0.0
        if app and app in self.knowledge.apps.get(term.key, ()):
            support += APP_BONUS
        if heard:
            support += min(CONTEXT_CAP, PREF_WEIGHT * self.preference(term.written, heard))
        return ev + support, support

    def case_fix(self, term: Term, span: list[_Slot], heard: str) -> str:
        """Same letters, different spelling: casing ("github" -> "GitHub") or joined/split words.

        Returns "yes" (apply), "ask" (only if the language model clearly agrees) or "no"."""
        if term.source == "history" and not term.fixed_case:
            return "no"
        all_common = all(is_common(s.core) for s in span)
        distinctive = any(any(c.isupper() for c in w[1:]) or any(c.isdigit() for c in w) for w in term.written.split())
        if heard.lower() == term.written.lower():  # casing only
            if len(span) == 1 and is_common(heard) and not distinctive:
                # "apple" stays "apple" unless you consistently write "Apple"
                return "yes" if self.preference(term.written, heard) >= 2.0 else "no"
            if term.source == "starter" and all_common and not distinctive:
                return "ask"  # "among us" -> "Among Us"? only if the sentence is about the game
            return "yes"
        # joined or split words: "Geo Guessr" -> "GeoGuessr"
        if term.source != "starter":
            return "yes"
        if all_common:
            return "ask"  # "drop box" -> "Dropbox", "base camp" -> "Basecamp": ordinary words, so ask
        return "yes"

    def candidates(self, slots: list[_Slot], a: int, b: int, context: set[str], app: str) -> list[_Candidate]:
        k = self.knowledge
        m = self.mode
        span = slots[a:b]
        heard = " ".join(s.core for s in span)
        key = "".join(s.key for s in span)
        if len(key) < 2:
            return []
        keep = sum(self._keep_cost(s) for s in span)
        confident = all(s.word.conf >= m.gate_conf for s in span) and all(k.knows(s.core) for s in span)
        pool: dict[str, Term] = {}
        for lex in (k.user, k.starter):
            for t in lex.sound_matches(heard) + lex.neighbours(heard, limit=8):
                pool.setdefault(t.key, t)
        out = []
        for t in pool.values():
            if t.key == key:
                continue
            if t.nwords > len(span) + 1:
                continue
            sim = similarity(heard, t.written)
            if sim < m.min_similarity or (t.nwords > len(span) and sim < 0.7):
                continue
            ev, support = self._evidence(t, context, app, heard)
            # Confident, ordinary words ("cloud" for your "Claude") need context or the language model to
            # agree; a run of words that sounds just like one of your names ("Geo Guesser") doesn't.
            run_together = len(span) > 1 and sim >= 0.85 and t.source != "starter"
            out.append(_Candidate((a, b), t, sim, ev - keep, guarded=confident and not run_together,
                                  support=support))
        return out

    def score(self, words: list[Word], cands: list[_Candidate], slots: list[_Slot], cache) -> None:
        """Fill in the acoustic penalty and total for candidates that share one span."""
        if not cands:
            return
        a, b = cands[0].span
        span_words = words[a:b]
        lead, tail = slots[a].lead, slots[b - 1].tail
        texts = [lead + c.term.written + tail for c in cands]
        deltas = None
        if self.scorer is not None:
            heard = tuple(w.text for w in span_words)
            todo = []
            deltas = []
            for t in texts:
                hit = cache.get_delta(heard, t, span_words[0].start) if cache is not None else None
                deltas.append(hit)
                if hit is None:
                    todo.append(t)
            if todo:
                fresh = self.scorer.deltas(span_words, todo)
                if fresh is not None:
                    it = iter(fresh)
                    for i, d in enumerate(deltas):
                        if d is None:
                            deltas[i] = next(it)
                            if cache is not None:
                                cache.put_delta(heard, texts[i], span_words[0].start, deltas[i])
                elif not any(d is not None for d in deltas):
                    deltas = None
        heard = " ".join(s.core for s in slots[a:b])
        for i, c in enumerate(cands):
            d = deltas[i] if deltas is not None else None
            c.delta = d
            guess = NO_AUDIO_SCALE * (1.0 - c.sim)
            # The decoder's score mixes acoustics with its own taste in spelling: "GeoGuessr" scores lower
            # than "GeoGuesser", "Shaun" lower than "Sean", though each pair sounds the same. Spellings this
            # close (or true homophones) can't be far apart in sound, so the penalty is capped.
            if homophones(heard, c.term.written):
                cap = SPELLING_SLACK
            elif c.sim >= CAP_SIMILARITY:
                cap = guess + SPELLING_SLACK
            else:
                cap = math.inf  # different enough that the audio's verdict stands
            penalty = min(d, cap) if d is not None else guess
            c.total = c.prior - penalty
            c.vetoed = d is not None and penalty >= ACOUSTIC_VETO  # the audio clearly says no: nothing overrides it


class _Cache:
    """Per-dictation memo of acoustic penalties, so prefetched work is reused at commit time."""

    def __init__(self, size: int = 512):
        self._d: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0

    def get_delta(self, heard: tuple, text: str, start: float) -> float | None:
        with self._lock:
            for t0, d in self._d.get((heard, text), ()):
                if abs(t0 - start) < 0.5:
                    self.hits += 1
                    return d
        return None

    def put_delta(self, heard: tuple, text: str, start: float, delta: float) -> None:
        with self._lock:
            self._d.setdefault((heard, text), []).append((start, delta))
            self._d.move_to_end((heard, text))
            while len(self._d) > 512:
                self._d.popitem(last=False)


class CorrectionRun:
    """Correction state for one dictation: the prefetch cache and the text so far (for context)."""

    def __init__(self, corrector: Corrector):
        self.c = corrector
        self.cache = _Cache()
        self.history: list[str] = []  # corrected words committed so far in this dictation
        self.on_metrics: Callable[[Result], None] | None = None
        self.on_late: Callable[[int, list[Change]], None] | None = None  # (commit, changes) after typing
        self.batches = 0  # commits so far
        self.trace: list | None = None  # debugging: set to a list to collect every scored candidate
        self._prefetch_lock = threading.Lock()
        self._pf_cond = threading.Condition()
        self._pf_latest: tuple[list[Word], str] | None = None
        self._pf_thread: threading.Thread | None = None
        self._closed = False

    @staticmethod
    def _context(words: list[str]) -> set[str]:
        out = set()
        for w in words:
            key = letters(w)
            if len(key) > 2 and key not in STOPWORDS:
                out.add(key)
        return out

    def correct(self, words: list[Word], before: list[str] | None = None, after: list[str] | None = None,
                app: str = "", budget_ms: float = 150.0, record: bool = True) -> Result:
        t0 = time.perf_counter()
        c = self.c
        batch = -1
        if record:
            batch, self.batches = self.batches, self.batches + 1
        if not c.enabled or not words:
            return Result(words, ms=(time.perf_counter() - t0) * 1000, batch=batch)
        slots = _slots(words)
        flagged = c.flags(slots)
        n_flagged = sum(flagged)
        if not n_flagged:
            res = Result(words, tier=0, ms=(time.perf_counter() - t0) * 1000, batch=batch)
            if record:
                self.history.extend(w.text for w in words)
                if self.on_metrics is not None:
                    self.on_metrics(res)
            return res
        before = self.history[-40:] if before is None else before
        context = self._context(before + [s.core for s in slots] + list(after or []))
        deadline = t0 + budget_ms / 1000.0

        # tier 1: candidates for every span (<= 3 words) touching a flagged word
        k = c.knowledge
        proposals: list[_Candidate] = []
        rules: list[Change] = []
        exact: list[Change] = []
        weak: list[_Candidate] = []  # casing you've used before, but not consistently: the language model decides
        spans_seen = set()
        by_span: dict[tuple[int, int], list[_Candidate]] = {}
        for i, f in enumerate(flagged):
            if not f:
                continue
            for n in range(1, MAX_SPAN + 1):
                for a in range(max(0, i - n + 1), min(i, len(slots) - n) + 1):
                    b = a + n
                    if (a, b) in spans_seen:
                        continue
                    spans_seen.add((a, b))
                    span = slots[a:b]
                    if any(s.tail for s in span[:-1]) or any(s.lead for s in span[1:]) or any(not s.key for s in span):
                        continue
                    if any(s.core.lower() in FILLERS for s in span):
                        continue
                    heard = " ".join(s.core for s in span)
                    rule = k.user.rule(heard)
                    if rule is not None:
                        written, taught = rule
                        single_common = n == 1 and is_common(heard) and span[0].word.conf >= c.mode.gate_conf
                        if written != heard and (not single_common or taught & (context - {span[0].key})):
                            rules.append(Change(a, b, heard, written, "rule", 1, 99.0))
                        continue
                    if n == 1 and not self._sentence_start(slots, a):
                        usual = k.dominant.get(span[0].key)
                        if usual is not None and usual != heard and c.preference(usual, heard) >= 2.0:
                            exact.append(Change(a, b, heard, usual, "history", 1, 50.0))
                            continue
                        for spelled in (k.forms.get(span[0].key) or {}):
                            if spelled != heard and spelled.lower() == heard.lower():
                                weak.append(_Candidate((a, b), _spelling_term(spelled), 1.0, 0.0, total=c.mode.margin,
                                                       guarded=True))
                    term = k.user.terms.get("".join(s.key for s in span)) or k.starter.terms.get(
                        "".join(s.key for s in span))
                    if term is not None:
                        if term.written != heard:
                            verdict = c.case_fix(term, span, heard)
                            if verdict == "yes":
                                exact.append(Change(a, b, heard, term.written, term.source, 1, 50.0 + n))
                            elif verdict == "ask":
                                weak.append(_Candidate((a, b), term, 1.0, 0.0, total=c.mode.margin, guarded=True))
                        continue
                    cands = c.candidates(slots, a, b, context, app)
                    if cands:
                        by_span[(a, b)] = cands

        # acoustic re-scoring, most promising spans first, while there's time
        m = c.mode
        floor = m.margin - m.band - 0.5  # a candidate whose evidence can't reach this even with perfect audio
        for key in list(by_span):
            by_span[key] = [x for x in by_span[key] if x.prior >= floor]
            if not by_span[key]:
                del by_span[key]
        order = sorted(by_span.values(), key=lambda cs: -max(x.prior + 6 * x.sim for x in cs))
        for cands in order:
            cands.sort(key=lambda x: -(x.prior + 6 * x.sim))
            del cands[3:]
            if time.perf_counter() > deadline - 0.02 and c.scorer is not None:
                break  # out of time: leave the rest as recognised
            c.score(words, cands, slots, self.cache)
            proposals.extend(x for x in cands if x.total > -math.inf)
            if self.trace is not None:  # noqa: SIM102
                for x in cands:
                    a, b = x.span
                    self.trace.append((" ".join(s.core for s in slots[a:b]), x.term.written, x.term.source,
                                       round(x.sim, 2), round(x.prior, 2), x.delta, round(x.total, 2), x.guarded,
                                       x.support, [round(s.word.conf, 2) for s in slots[a:b]]))

        accepted: list[Change] = []
        ambiguous: list[_Candidate] = list(weak)
        lm = c.language
        proposals = [x for x in proposals if not x.vetoed]
        for x in proposals:
            ok_guard = not x.guarded or x.support >= GUARD_CONTEXT
            if x.total >= m.margin and ok_guard and not (x.guarded and lm is not None):
                accepted.append(self._change(x, slots))
            elif x.total >= m.margin - m.band:
                ambiguous.append(x)  # incl. guarded ones the language model should confirm
        tier = 1
        late: list[_Candidate] = []
        if ambiguous and lm is not None and time.perf_counter() < deadline:
            tier = 2
            accepted.extend(self._tier2(words, slots, ambiguous, before, deadline,
                                        late if (record and self.on_late is not None) else None))
        elif ambiguous:  # no language model: guarded changes with enough support still go through
            accepted.extend(self._change(x, slots) for x in ambiguous
                            if x.total >= m.margin and x.guarded and x.support >= GUARD_CONTEXT
                            and x.term.source != "spelling")

        chosen = self._select(rules + exact + accepted)
        valid = validate(words, chosen, k)
        if len(valid) != len(chosen):
            log.info("validator dropped %d proposed change(s)", len(chosen) - len(valid))
        out = apply_changes(words, valid)
        res = Result(out, valid, tier=tier, ms=(time.perf_counter() - t0) * 1000, flagged=n_flagged, batch=batch,
                     late=len({x.span for x in late}))
        if late:
            taken = {i for ch in valid for i in range(ch.start, ch.end)}
            late = [x for x in late if not taken & set(range(*x.span))]
        if late:
            threading.Thread(target=self._late_job, args=(words, slots, late, list(before), batch, valid),
                             name="tiro-late-fix", daemon=True).start()
        if record:
            self.history.extend(w.text for w in out)
            if self.on_metrics is not None:
                self.on_metrics(res)
        if valid:
            log.info("corrected %d span(s) in %.1f ms: %s", len(valid), res.ms,
                     ", ".join(f"{ch.source}/t{ch.tier}" for ch in valid))
        return res

    def prefetch(self, words: list[Word], app: str = "") -> None:
        """Do the expensive part early, on a partial hypothesis, so the commit finds it cached."""
        if not self.c.enabled or not words or not self._prefetch_lock.acquire(blocking=False):
            return
        try:
            self.correct(words, app=app, budget_ms=400.0, record=False)
        except Exception:
            log.exception("prefetch failed")
        finally:
            self._prefetch_lock.release()

    def prefetch_async(self, words: list[Word], app: str = "") -> None:
        """Queue a prefetch on this dictation's worker thread (only the latest request is kept)."""
        if not self.c.enabled or not words:
            return
        with self._pf_cond:
            if self._closed:
                return
            self._pf_latest = (list(words), app)
            if self._pf_thread is None:
                self._pf_thread = threading.Thread(target=self._pf_loop, name="tiro-prefetch", daemon=True)
                self._pf_thread.start()
            self._pf_cond.notify()

    def _pf_loop(self) -> None:
        while True:
            with self._pf_cond:
                while self._pf_latest is None and not self._closed:
                    self._pf_cond.wait()
                if self._closed:
                    return
                words, app = self._pf_latest
                self._pf_latest = None
            self.prefetch(words, app)

    def close(self) -> None:
        with self._pf_cond:
            self._closed = True
            self._pf_latest = None
            self._pf_cond.notify()

    def hold_back(self, committable: list[Word], rest: list[Word]) -> int:
        """How many trailing words of a commit to keep back because they're doubtful and the word after
        them (still pending) may belong to the same name ("Geo" | "Guesser")."""
        if not self.c.enabled or not committable or not rest:
            return 0
        window = committable[-MAX_HOLD:] + rest[:1]
        flags = self.c.flags(_slots(window))
        n = 0
        for i in range(len(window) - 2, -1, -1):  # committable words, last first
            if not (flags[i] or flags[i + 1]) or split_punct(window[i].text)[2]:
                break  # confident pair, or punctuation ends the run
            n += 1
        return n

    def _lm_args(self, words, slots, a: int, b: int, xs: list[_Candidate], before: list[str]):
        """What the language model is shown for a span: (left, as heard, options, right, examples)."""
        xs = sorted(xs, key=lambda x: -x.total)[:3]
        left = " ".join(before[-30:] + [w.text for w in words[:a]])
        right = " ".join(w.text for w in words[b:])
        original = " ".join(w.text for w in words[a:b])
        options = [slots[a].lead + x.term.written + slots[b - 1].tail for x in xs]
        examples: list[str] = []
        for x in xs:
            examples += [e for e in self.c.knowledge.examples(x.term.written) if e not in examples]
        return left, original, options, right, examples[:4]

    # ------------------------------------------------------------------ helpers
    def _sentence_start(self, slots: list[_Slot], i: int) -> bool:
        if i > 0:
            return ends_sentence(slots[i - 1].word.text)
        return not self.history or ends_sentence(self.history[-1])

    def _change(self, x: _Candidate, slots: list[_Slot], tier: int = 1) -> Change:
        a, b = x.span
        heard = " ".join(s.core for s in slots[a:b])
        return Change(a, b, heard, x.term.written, x.term.source, tier, x.total, x.delta)

    @staticmethod
    def _select(changes: list[Change]) -> list[Change]:
        """Best non-overlapping set: highest score first, longer spans win ties."""
        chosen: list[Change] = []
        taken: set[int] = set()
        for ch in sorted(changes, key=lambda c: (-c.score, -(c.end - c.start))):
            idx = set(range(ch.start, ch.end))
            if idx & taken:
                continue
            chosen.append(ch)
            taken |= idx
        return sorted(chosen, key=lambda c: c.start)

    def _late_job(self, words, slots, spans: list[_Candidate], before, batch: int, applied: list[Change]) -> None:
        """Finish tier 2 after the text was typed; report a fix the session may swap in (if still safe)."""
        try:
            changes = self._tier2(words, slots, spans, before, time.perf_counter() + LATE_TIMEOUT)
            if not changes:
                return
            valid = validate(words, applied + changes, self.c.knowledge)
            new = [ch for ch in valid if ch not in applied]
            if new and self.on_late is not None and not self._closed:
                log.info("late correction ready for commit %d (%d span(s))", batch, len(new))
                self.on_late(batch, new)
        except Exception:
            log.exception("late correction failed")

    def _tier2(self, words, slots, ambiguous: list[_Candidate], before: list[str], deadline: float,
               late: list[_Candidate] | None = None) -> list[Change]:
        """Ask the language model about spans tier 1 couldn't settle (see llm.py). It only ranks the
        candidates against what was heard; its vote is capped and combined with the tier-1 evidence.
        Spans there's no time for go to `late` (if given) to be finished after the text is typed."""
        lm = self.c.language
        m = self.c.mode
        out: list[Change] = []
        by_span: dict[tuple[int, int], list[_Candidate]] = {}
        for x in ambiguous:
            by_span.setdefault(x.span, []).append(x)
        for (a, b), xs in sorted(by_span.items(), key=lambda kv: -max(x.total for x in kv[1])):
            remaining = deadline - time.perf_counter()
            expected = (lm.avg_ms or 80.0) / 1000.0 if lm is not None else 0.0
            if late is not None and remaining < expected * 1.2 and not lm.cached(*self._lm_args(words, slots, a, b, xs, before)):
                late.extend(xs)
                continue
            if remaining <= 0.005:
                break
            xs = sorted(xs, key=lambda x: -x.total)[:3]
            left, original, options, right, examples = self._lm_args(words, slots, a, b, xs, before)
            gains = lm.compare(left, original, options, right, timeout=remaining, examples=examples)
            if gains is None:
                continue
            best, best_total = None, -math.inf
            for x, gain in zip(xs, gains, strict=True):
                vote = LM_WEIGHT * max(-LM_CAP, min(LM_CAP, gain))
                total = x.total + vote
                if x.term.source == "spelling" or (x.guarded and x.support < GUARD_CONTEXT):
                    ok = gain >= LM_CONFIRM and total >= m.margin  # weak evidence: the model must agree clearly
                else:
                    ok = gain > -LM_CONFIRM and total >= m.margin
                if ok and total > best_total:
                    best, best_total = x, total
            if best is not None:
                best.total = best_total
                out.append(self._change(best, slots, tier=2))
        return out
