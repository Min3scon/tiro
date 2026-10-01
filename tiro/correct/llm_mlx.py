"""Tier-2 language model on Apple Silicon: the same scorer as llm.py, running on the Mac's GPU with MLX.

The fixed instruction text is run once and kept in a prompt cache. For each check, the variable context is
appended, then each version of the sentence is scored and the cache is trimmed back, so nothing is
recomputed twice.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path

from tiro.correct.llm import ASK, FIXED, MAX_EXAMPLES, MAX_LEFT_WORDS, MAX_RIGHT_WORDS, NO_EXAMPLES

log = logging.getLogger(__name__)


class MlxLanguageScorer:
    def __init__(self, model_dir: Path):
        self.model_dir = Path(model_dir)
        self.device = "metal"
        self.name = self.model_dir.name
        self.gpu_lock = None
        self._lock = threading.Lock()
        self._memo: OrderedDict = OrderedDict()
        self.avg_ms: float | None = None
        self.calls = 0
        self._model = self._tok = self._cache = None
        self._fixed_len = 0

    def load(self) -> None:
        from mlx_lm import load as mlx_load
        from mlx_lm.models.cache import make_prompt_cache

        t0 = time.perf_counter()
        self._model, self._tok = mlx_load(str(self.model_dir))
        self._cache = make_prompt_cache(self._model)
        ids = self._encode(FIXED)
        self._forward(ids)
        self._fixed_len = len(ids)
        self.compare("I wrote it in", "Python", ["Pythons"], "last night", timeout=10.0)
        self._memo.clear()
        log.info("language model %s ready on the Apple GPU in %.1fs", self.name, time.perf_counter() - t0)

    @property
    def ready(self) -> bool:
        return self._model is not None

    def _encode(self, text: str) -> list[int]:
        return list(self._tok.encode(text, add_special_tokens=False))

    def _forward(self, ids: list[int]):
        """Run tokens through the model, extending the cache. Returns log-probabilities [len(ids), vocab]."""
        import mlx.core as mx

        logits = self._model(mx.array(ids)[None], cache=self._cache)[0]
        logp = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
        mx.eval(logp)
        return logp

    def _trim(self, n: int) -> None:
        from mlx_lm.models.cache import trim_prompt_cache

        if n > 0:
            trim_prompt_cache(self._cache, n)

    def _key(self, left, original, options, right, examples):
        return (" ".join(left.split()[-MAX_LEFT_WORDS:]), original, tuple(options),
                " ".join(right.split()[:MAX_RIGHT_WORDS]), tuple((examples or [])[:MAX_EXAMPLES]))

    def cached(self, left, original, options, right, examples=None) -> bool:
        return self._key(left, original, options, right, examples) in self._memo

    def compare(self, left: str, original: str, options: list[str], right: str, *, timeout: float = 0.15,
                examples: list[str] | None = None) -> list[float] | None:
        if self._model is None or not options:
            return None
        key = self._key(left, original, options, right, examples)
        hit = self._memo.get(key)
        if hit is not None:
            return list(hit)
        t0 = time.perf_counter()
        if not self._lock.acquire(timeout=max(0.0, timeout)):
            return None
        try:
            left_words = left.split()[-MAX_LEFT_WORDS:]
            right_words = right.split()[:MAX_RIGHT_WORDS]
            ex = (examples or [])[:MAX_EXAMPLES]
            shown = "".join(f"- {e.strip()}\n" for e in ex) if ex else NO_EXAMPLES
            ctx = self._encode(shown + ASK + " ".join(left_words))
            logp = self._forward(ctx)
            first = logp[-1]
            tail = (" " + " ".join(right_words)) if right_words else ""
            sep = " " if left_words else ""
            scores = []
            for text in [original, *options]:
                cont = self._encode(sep + text + tail)
                total = float(first[cont[0]].item())
                if len(cont) > 1:
                    lp = self._forward(cont[:-1])
                    total += float(sum(lp[i, cont[i + 1]].item() for i in range(len(cont) - 1)))
                    self._trim(len(cont) - 1)
                scores.append(total)
            self._trim(len(ctx))
            gains = [s - scores[0] for s in scores[1:]]
            self._memo[key] = tuple(gains)
            while len(self._memo) > 256:
                self._memo.popitem(last=False)
        except Exception:
            log.exception("MLX scoring failed")
            return None
        finally:
            self._lock.release()
        ms = (time.perf_counter() - t0) * 1000
        self.avg_ms = ms if self.avg_ms is None else 0.8 * self.avg_ms + 0.2 * ms
        self.calls += 1
        return gains
