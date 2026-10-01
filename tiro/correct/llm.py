"""Tier 2: a small local language model judges, in context, which candidate you meant.

It never writes text. For a doubtful span it is shown the words before it, a few sentences from your own
history that use the candidate words, and then every version of the sentence (as heard, and with each
candidate). It returns how much more natural each candidate version is than what was heard (difference in
log-likelihood). The corrector weighs that together with the audio and history evidence, and still applies
its own rules to whatever it picks.

Speed: the fixed instruction text is run through the model once and its key/value cache kept; per span
only the variable context is added, and all versions of the sentence are scored in one batched pass.
Recent results are memoised, so work done on a partial hypothesis while you speak is reused at commit.
Runs on the GPU when there is one (sharing it politely with the speech model), otherwise on the CPU.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SYSTEM = ("You write down dictation for one person exactly as they said it, spelling names, brands and terms "
          "the way they usually do.")
FIXED = f"<|im_start|>system\n{SYSTEM}<|im_end|>\n<|im_start|>user\nSome things I wrote before:\n"
NO_EXAMPLES = "(nothing yet)\n"
ASK = "Now write down what I just said.<|im_end|>\n<|im_start|>assistant\n"
MAX_LEFT_WORDS = 40
MAX_RIGHT_WORDS = 4
MAX_EXAMPLES = 4


class LanguageScorer:
    def __init__(self, model_dir: Path, *, device: str = "cpu", gpu_lock: threading.Lock | None = None,
                 file: str | None = None, gpu_yield: Callable[[], bool] | None = None):
        self.model_dir = Path(model_dir)
        self.device = device  # "cuda" | "cpu"
        self.gpu_lock = gpu_lock if device == "cuda" else None  # shared with the speech model
        # True while the speech model wants the GPU (a decode is queued, or a dictation's final decode is due):
        # then the language model starts no new work, so it never makes you wait for your text
        self.gpu_yield = gpu_yield if device == "cuda" else None
        self.file = file or ("onnx/model_q4f16.onnx" if device == "cuda" else "onnx/model_q4.onnx")
        self.name = self.model_dir.name
        self._session = None
        self._tok = None
        self._lock = threading.Lock()  # one scoring call at a time
        self._fixed_kv: list[np.ndarray] | None = None
        self._fixed_len = 0
        self._memo: OrderedDict = OrderedDict()
        self.avg_ms: float | None = None  # moving average of a compare() call
        self.calls = 0

    # ------------------------------------------------------------------ loading
    def load(self) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        t0 = time.perf_counter()
        cfg = json.loads((self.model_dir / "config.json").read_text(encoding="utf-8"))
        self.layers = int(cfg["num_hidden_layers"])
        self.kv_heads = int(cfg["num_key_value_heads"])
        self.head_dim = int(cfg.get("head_dim") or cfg["hidden_size"] // cfg["num_attention_heads"])
        self._tok = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        so = ort.SessionOptions()
        so.log_severity_level = 3
        so.add_session_config_entry("session.intra_op.allow_spinning", "0")
        so.inter_op_num_threads = 1
        if self.device == "cuda":
            so.intra_op_num_threads = 1
            providers = [("CUDAExecutionProvider", {"device_id": 0, "arena_extend_strategy": "kSameAsRequested"}),
                         "CPUExecutionProvider"]
        else:
            so.intra_op_num_threads = 4
            providers = ["CPUExecutionProvider"]
        self._session = ort.InferenceSession(str(self.model_dir / self.file), sess_options=so, providers=providers)
        if self.device == "cuda" and "CUDAExecutionProvider" not in self._session.get_providers():
            raise RuntimeError("CUDA not available for the language model")
        self._kv_dtype = np.float16 if "float16" in self._session.get_inputs()[3].type else np.float32
        ids = self._encode(FIXED)
        _, kv = self._run(ids, self._empty_kv(1), 0)
        self._fixed_kv, self._fixed_len = kv, len(ids)
        self.compare("I wrote it in", "Python", ["Pythons"], "last night", timeout=10.0)  # warm up
        self._memo.clear()
        log.info("language model %s ready on %s in %.1fs", self.name, self.device, time.perf_counter() - t0)

    @property
    def ready(self) -> bool:
        return self._session is not None

    def _encode(self, text: str) -> list[int]:
        return self._tok.encode(text, add_special_tokens=False).ids

    def _empty_kv(self, batch: int) -> list[np.ndarray]:
        z = np.zeros((batch, self.kv_heads, 0, self.head_dim), dtype=self._kv_dtype)
        return [z] * (2 * self.layers)

    def _run(self, ids, kv: list[np.ndarray], past: int, mask: np.ndarray | None = None):
        """One forward pass. ids: [batch, seq] (or a flat list for batch 1). Returns (logits, new kv)."""
        arr = np.asarray(ids, dtype=np.int64)
        if arr.ndim == 1:
            arr = arr[None, :]
        batch, seq = arr.shape
        if mask is None:
            mask = np.ones((batch, past + seq), dtype=np.int64)
        feed = {
            "input_ids": arr,
            "attention_mask": mask,
            "position_ids": np.broadcast_to(np.arange(past, past + seq, dtype=np.int64), (batch, seq)).copy(),
        }
        for i in range(self.layers):
            feed[f"past_key_values.{i}.key"] = kv[2 * i]
            feed[f"past_key_values.{i}.value"] = kv[2 * i + 1]
        outs = self._session.run(None, feed)
        return outs[0], outs[1:]

    # ------------------------------------------------------------------ scoring
    def _key(self, left, original, options, right, examples):
        return (" ".join(left.split()[-MAX_LEFT_WORDS:]), original, tuple(options),
                " ".join(right.split()[:MAX_RIGHT_WORDS]), tuple((examples or [])[:MAX_EXAMPLES]))

    def cached(self, left, original, options, right, examples=None) -> bool:
        return self._key(left, original, options, right, examples) in self._memo

    def compare(self, left: str, original: str, options: list[str], right: str, *, timeout: float = 0.15,
                examples: list[str] | None = None) -> list[float] | None:
        """For each option: log P(sentence with the option) - log P(sentence as heard), in nats.
        None if the model isn't ready or the GPU stayed busy past the timeout."""
        if self._session is None or not options:
            return None
        left_words = left.split()[-MAX_LEFT_WORDS:]
        right_words = right.split()[:MAX_RIGHT_WORDS]
        ex = tuple((examples or [])[:MAX_EXAMPLES])
        key = self._key(left, original, options, right, examples)
        with self._lock:
            hit = self._memo.get(key)
            if hit is not None:
                self._memo.move_to_end(key)
                return list(hit)
        t0 = time.perf_counter()
        if self.gpu_yield is not None and self.gpu_yield():
            return None  # the speech model goes first
        if not self._lock.acquire(timeout=max(0.0, timeout)):
            return None
        try:
            gpu = self.gpu_lock
            remaining = timeout - (time.perf_counter() - t0)
            if gpu is not None and not gpu.acquire(timeout=max(0.0, remaining)):
                return None  # the speech model has the GPU; don't make it wait
            if self.gpu_yield is not None and self.gpu_yield():
                if gpu is not None:
                    gpu.release()
                return None
            try:
                gains = self._score(left_words, original, options, right_words, ex)
            finally:
                if gpu is not None:
                    gpu.release()
            self._memo[key] = tuple(gains)
            while len(self._memo) > 256:
                self._memo.popitem(last=False)
        finally:
            self._lock.release()
        ms = (time.perf_counter() - t0) * 1000
        self.avg_ms = ms if self.avg_ms is None else 0.8 * self.avg_ms + 0.2 * ms
        self.calls += 1
        return gains

    def _score(self, left_words, original, options, right_words, examples) -> list[float]:
        shown = "".join(f"- {e.strip()}\n" for e in examples) if examples else NO_EXAMPLES
        context = shown + ASK + " ".join(left_words)
        ctx_ids = self._encode(context)
        logits, kv = self._run(ctx_ids, self._fixed_kv, self._fixed_len)
        first = _log_softmax(logits[0, -1])  # predicts the first token of the continuation
        past = self._fixed_len + len(ctx_ids)
        tail = (" " + " ".join(right_words)) if right_words else ""
        sep = " " if left_words else ""
        conts = [self._encode(sep + text + tail) for text in [original, *options]]
        width = max(len(c) for c in conts)
        batch = len(conts)
        ids = np.zeros((batch, width), dtype=np.int64)
        mask = np.zeros((batch, past + width), dtype=np.int64)
        mask[:, :past] = 1
        for b, c in enumerate(conts):
            ids[b, : len(c)] = c
            mask[b, past : past + len(c)] = 1
        kv_b = [np.repeat(x, batch, axis=0) for x in kv]
        out, _ = self._run(ids, kv_b, past, mask)
        scores = []
        for b, c in enumerate(conts):
            total = float(first[c[0]])
            if len(c) > 1:
                lp = _log_softmax(out[b, : len(c) - 1])
                total += float(lp[np.arange(len(c) - 1), c[1:]].sum())
            scores.append(total)
        return [s - scores[0] for s in scores[1:]]


def _log_softmax(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float32)
    m = x.max(axis=-1, keepdims=True)
    return x - m - np.log(np.exp(x - m).sum(axis=-1, keepdims=True))
