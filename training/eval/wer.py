"""Word error rate with bootstrap confidence intervals and paired significance tests.

Utterance-level bootstrap (Bisani & Ney 2004): resample whole utterances with replacement, recompute the pooled
WER = sum(errors) / sum(reference words). A paired test resamples the same utterances for both systems, which is
what makes small differences detectable without overstating them.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from rapidfuzz.distance import Levenshtein

from training.eval import normalize


def edit_counts(ref: str, hyp: str) -> tuple[int, int]:
    """(word edit distance, reference word count) of already-normalised strings."""
    r = ref.split()
    h = hyp.split()
    if not r:
        return len(h), 0
    return Levenshtein.distance(r, h), len(r)


@dataclass
class Scored:
    ids: list[str]
    errors: np.ndarray  # int, per utterance
    words: np.ndarray  # int, per utterance

    @property
    def wer(self) -> float:
        total = int(self.words.sum())
        return float(self.errors.sum()) / total if total else 0.0


def score(ids, refs, hyps, mode: str = "leaderboard") -> Scored:
    norm = normalize.leaderboard if mode == "leaderboard" else normalize.formatted
    errs, words = [], []
    for ref, hyp in zip(refs, hyps):
        e, n = edit_counts(norm(ref), norm(hyp))
        errs.append(e)
        words.append(n)
    return Scored(list(ids), np.asarray(errs, dtype=np.int64), np.asarray(words, dtype=np.int64))


def _boot_indices(n: int, b: int, seed: int, chunk: int = 200):
    rng = np.random.default_rng(seed)
    done = 0
    while done < b:
        k = min(chunk, b - done)
        yield rng.integers(0, n, size=(k, n))
        done += k


def bootstrap_ci(s: Scored, b: int = 2000, seed: int = 0, alpha: float = 0.05) -> tuple[float, float]:
    n = len(s.errors)
    if n == 0:
        return 0.0, 0.0
    vals = []
    for idx in _boot_indices(n, b, seed):
        e = s.errors[idx].sum(axis=1)
        w = np.maximum(s.words[idx].sum(axis=1), 1)
        vals.append(e / w)
    v = np.concatenate(vals)
    return float(np.quantile(v, alpha / 2)), float(np.quantile(v, 1 - alpha / 2))


def paired(a: Scored, b_: Scored, b: int = 2000, seed: int = 0, alpha: float = 0.05) -> dict:
    """Compare system A against system B on the same utterances.

    Returns the absolute and relative WER difference (A - B; negative = A better), its bootstrap CI and the
    one-sided p-value that A is NOT better than B (fraction of resamples where A's WER >= B's).
    """
    pos = {u: i for i, u in enumerate(b_.ids)}
    keep = [i for i, u in enumerate(a.ids) if u in pos]
    if len(keep) != len(a.ids) or len(keep) != len(b_.ids):
        raise ValueError(f"systems scored on different utterances ({len(a.ids)} vs {len(b_.ids)}, {len(keep)} shared)")
    order = np.asarray([pos[a.ids[i]] for i in keep])
    ea, wa = a.errors, a.words
    eb, wb = b_.errors[order], b_.words[order]
    n = len(ea)
    diffs, rels = [], []
    for idx in _boot_indices(n, b, seed):
        w = np.maximum(wa[idx].sum(axis=1), 1)
        wa_ = ea[idx].sum(axis=1) / w
        wb_ = eb[idx].sum(axis=1) / w
        diffs.append(wa_ - wb_)
        rels.append((wa_ - wb_) / np.maximum(wb_, 1e-9))
    d = np.concatenate(diffs)
    r = np.concatenate(rels)
    base_a = float(ea.sum()) / max(int(wa.sum()), 1)
    base_b = float(eb.sum()) / max(int(wb.sum()), 1)
    return {
        "wer_a": base_a,
        "wer_b": base_b,
        "diff": base_a - base_b,
        "rel": (base_a - base_b) / base_b if base_b else 0.0,
        "diff_ci": (float(np.quantile(d, alpha / 2)), float(np.quantile(d, 1 - alpha / 2))),
        "rel_ci": (float(np.quantile(r, alpha / 2)), float(np.quantile(r, 1 - alpha / 2))),
        "p_not_better": float((d >= 0).mean()),
        "n": n,
    }
