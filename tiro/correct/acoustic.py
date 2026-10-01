"""Acoustic check: how well does the audio support another spelling of a span?

The recogniser's own decoder is asked to re-read the span's audio frames as the original tokens and as each
candidate. One joint-network call scores every (frame, token) pair for all of them; the best TDT alignment
of each is then found by dynamic programming. The difference in log-probability says how much the audio
prefers what was recognised over the candidate (0 = equally good, larger = the audio disagrees).
"""

from __future__ import annotations

import logging
import math
import threading

import numpy as np

from tiro.asr import ParakeetEngine, Word, segmentations

log = logging.getLogger(__name__)

NEG = -1e30
MAX_FRAMES = 90  # ~7 s of audio; longer spans aren't re-scored
DURATIONS = 5  # Parakeet TDT predicts advancing 0..4 frames


def _variants(text: str) -> list[str]:
    """Spellings the recogniser might use for a term: as written, and lowercase for camel-case brands."""
    core = text.strip()
    return [text, text.lower()] if any(c.isupper() for c in core[1:]) else [text]


def _alignment_score(lat: np.ndarray, target: list[int], blank: int, vocab: int, final: bool) -> float:
    """Best log-probability of emitting `target` over the lattice's frames (Viterbi over TDT alignments).

    lat: [frames, len(target) + 1, vocab + DURATIONS] joint outputs. A span inside an utterance must end
    exactly at its last frame (where the next word starts); a span at the end may run off the end.
    """
    n_frames = lat.shape[0]
    u_max = len(target)
    tok = lat[:, : u_max + 1, :vocab]
    top = tok.max(axis=-1, keepdims=True)
    lse = (top + np.log(np.exp(tok - top).sum(axis=-1, keepdims=True)))[..., 0]
    lp_blank = (tok[..., blank] - lse).tolist()
    if u_max:
        idx = np.asarray(target, dtype=np.int64)
        lp_tok = (np.take_along_axis(tok[:, :u_max, :], np.broadcast_to(idx[None, :, None], (n_frames, u_max, 1)),
                                     axis=-1)[..., 0] - lse[:, :u_max]).tolist()
    else:
        lp_tok = [[] for _ in range(n_frames)]
    dur = lat[:, : u_max + 1, vocab : vocab + DURATIONS]
    dtop = dur.max(axis=-1, keepdims=True)
    lp_dur = (dur - (dtop + np.log(np.exp(dur - dtop).sum(axis=-1, keepdims=True)))).tolist()

    end = n_frames if final else n_frames - 1  # position where the alignment must finish
    alpha = [[NEG] * (u_max + 1) for _ in range(end + 1)]
    alpha[0][0] = 0.0
    for t in range(min(end + 1, n_frames)):
        row = alpha[t]
        for u in range(u_max + 1):
            a = row[u]
            if a <= NEG:
                continue
            d_lp = lp_dur[t][u]
            if t < end:  # blank: move on 1..4 frames (a blank predicted to stay put moves on by one)
                b = a + lp_blank[t][u]
                hi_, lo_ = max(d_lp[0], d_lp[1]), min(d_lp[0], d_lp[1])
                stay_or_one = hi_ + math.log1p(math.exp(lo_ - hi_))
                for d in range(1, DURATIONS):
                    t2 = t + d
                    if t2 > end:
                        if not final:
                            break
                        t2 = end
                    s = b + (stay_or_one if d == 1 else d_lp[d])
                    if s > alpha[t2][u]:
                        alpha[t2][u] = s
            if u < u_max:  # the next target token, then advance 0..4 frames
                e = a + lp_tok[t][u]
                for d in range(DURATIONS):
                    t2 = t + d
                    if t2 > end:
                        if not final:
                            break
                        t2 = end
                    s = e + d_lp[d]
                    if s > alpha[t2][u + 1]:
                        alpha[t2][u + 1] = s
    return alpha[end][u_max]


class AcousticScorer:
    """Scores alternative spellings of recognised spans against the audio (see module docstring)."""

    def __init__(self, engine: ParakeetEngine):
        self.engine = engine
        self._pieces: dict[str, int] | None = None
        self._seg_cache: dict[str, list[list[int]]] = {}
        self._lock = threading.Lock()

    def tokenize(self, text: str, limit: int = 2) -> list[list[int]]:
        """Token-id spellings of `text` as a new word (fewest pieces first)."""
        with self._lock:
            hit = self._seg_cache.get(text)
            if hit is not None:
                return hit
            if self._pieces is None:
                self._pieces = self.engine.pieces
            segs = segmentations(" " + text.strip(), self._pieces, max_paths=limit)[:limit]
            if len(self._seg_cache) > 4096:
                self._seg_cache.clear()
            self._seg_cache[text] = segs
            return segs

    @staticmethod
    def span_frames(words: list[Word]):
        """(acoustic context, first token, end token, start frame, frames, is_final) or None."""
        ac = words[0].acoustic
        if ac is None or any(w.acoustic is not ac or w.tok is None for w in words):
            return None
        lo, hi = words[0].tok[0], words[-1].tok[1]
        for a, b in zip(words, words[1:], strict=False):
            if a.tok[1] != b.tok[0]:
                return None  # not consecutive in that decode
        if not (0 <= lo < hi <= len(ac.ids)):
            return None
        t0 = ac.frames[lo - 1] + ac.durs[lo - 1] if lo > 0 else 0
        t0 = min(t0, ac.frames[lo])
        final = hi >= len(ac.ids)
        t1 = len(ac.enc) if final else ac.frames[hi] + 1
        n = t1 - t0
        if n <= 0 or n > MAX_FRAMES:
            return None
        return ac, lo, hi, t0, n, final

    def deltas(self, words: list[Word], candidates: list[str], ilm_weight: float = 0.0) -> list[float] | None:
        """For each candidate spelling (with the span's punctuation), how many nats worse it explains the
        audio than what was recognised. None when the span can't be re-scored.

        The decoder's prediction network doubles as a small language model that dislikes unusual spellings;
        with `ilm_weight` > 0 that internal-LM share is estimated (encoder input zeroed) and taken out, so
        a rare name isn't penalised just for being rare (internal LM estimation)."""
        info = self.span_frames(words)
        if info is None or not candidates:
            return None
        ac, lo, hi, t0, n, final = info
        rows: list[list[int]] = [list(ac.ids[lo:hi])]
        owner = [-1]
        for ci, cand in enumerate(candidates):
            for variant in _variants(cand):
                for seq in self.tokenize(variant):
                    if seq and len(seq) <= 24:
                        rows.append(seq)
                        owner.append(ci)
        if len(rows) == 1:
            return None
        try:
            lat = self.engine.score_lattice(ac, t0, t0 + n, ac.prev[lo], ac.states[lo], rows)
            ilm = self._ilm(ac, lo, rows) if ilm_weight else None
        except Exception:
            log.exception("acoustic re-scoring failed")
            return None
        blank = self.engine.blank_id
        vocab = lat.shape[-1] - DURATIONS
        scores = [_alignment_score(lat[r], rows[r], blank, vocab, final) for r in range(len(rows))]
        if ilm is not None:
            scores = [s - ilm_weight * lm for s, lm in zip(scores, ilm, strict=True)]
        base = scores[0]
        if base <= NEG / 2:
            return None
        best = [NEG] * len(candidates)
        for r in range(1, len(rows)):
            best[owner[r]] = max(best[owner[r]], scores[r])
        return [base - b if b > NEG / 2 else math.inf for b in best]

    def _ilm(self, ac, lo: int, rows: list[list[int]]) -> list[float]:
        """Internal language-model log-probability of each row's tokens (blank excluded)."""
        zero = np.zeros((1, ac.enc.shape[1]), dtype=ac.enc.dtype)

        class _Z:  # a one-frame silent "decode" for score_lattice
            enc = zero

        lat = self.engine.score_lattice(_Z, 0, 1, ac.prev[lo], ac.states[lo], rows)
        blank = self.engine.blank_id
        vocab = lat.shape[-1] - DURATIONS
        out = []
        for r, seq in enumerate(rows):
            logits = lat[r, 0, : len(seq), :vocab].astype(np.float64)
            logits[:, blank] = -np.inf
            top = logits.max(axis=-1, keepdims=True)
            lse = top[:, 0] + np.log(np.exp(logits - top).sum(axis=-1))
            out.append(float(sum(logits[u, tok] - lse[u] for u, tok in enumerate(seq))))
        return out
