"""Parakeet TDT speech recognizer running on ONNX Runtime (CUDA when available, CPU otherwise)."""

from __future__ import annotations

import logging
import sys
import threading
import time
import weakref
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from tiro import gpu

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME_SEC = 0.08  # FastConformer emits one encoder frame per 80 ms (10 ms hop x 8 subsampling)
# cuDNN re-plans every Conv when the input length changes (~90 ms on an RTX 3070 versus ~20 ms steady state),
# so on the GPU the features are zero-padded to a few fixed lengths (in 10 ms feature frames).
GPU_BUCKETS = (1000, 2000, 3000)
# Dictionary boosting bonuses (in logit units): continuing a dictionary word's spelling vs. starting one.
BOOST_START = 0.0
BOOST_CONT = 2.5


@dataclass
class Word:
    text: str  # word with attached punctuation, e.g. "store."
    start: float  # seconds
    end: float  # seconds
    conf: float = 1.0  # the model's own probability for its least certain token in this word
    # where the word came from, so the corrector can re-score alternatives against the audio:
    tok: tuple[int, int] | None = field(default=None, compare=False, repr=False)  # token range in its decode
    ac: Any = field(default=None, compare=False, repr=False)  # weakref to that decode's AcousticContext

    def shifted(self, offset: float) -> Word:
        return Word(self.text, self.start + offset, self.end + offset, self.conf, self.tok, self.ac)

    @property
    def acoustic(self) -> AcousticContext | None:
        return self.ac() if self.ac is not None else None


@dataclass(eq=False)
class AcousticContext:
    """What the decoder saw in one decode, kept briefly so words can be re-scored against the audio."""

    enc: np.ndarray  # [frames, dim] encoder output
    ids: list[int]  # emitted token ids
    frames: list[int]  # encoder frame each token was emitted at
    durs: list[int]  # frames the decoder advanced after each token
    states: list[Any]  # prediction-network state fed in when each token was emitted
    prev: list[int]  # token fed in when each token was emitted (blank at the start)


class _Node:
    __slots__ = ("children", "ids", "terminal")

    def __init__(self):
        self.children: dict[int, _Node] = {}
        self.ids = np.zeros(0, dtype=np.int64)
        self.terminal = False  # a complete dictionary spelling ends here


class BoostTrie:
    """Token-piece trie of dictionary words, used to bias greedy decoding toward them.

    Once the decoder has started a dictionary spelling, pieces that continue it get `cont_bonus`.
    When a spelling is complete, pieces that would glue more letters onto it get the same amount as a
    penalty, so "Niamh" doesn't become "Niamhite".
    """

    def __init__(self, sequences: list[list[int]], start_bonus: float, cont_bonus: float, inword_ids: np.ndarray):
        self.root = _Node()
        self.start_bonus = start_bonus
        self.cont_bonus = cont_bonus
        self.inword_ids = inword_ids
        for seq in sequences:
            node = self.root
            for tok in seq:
                node = node.children.setdefault(tok, _Node())
            node.terminal = True
        stack = [self.root]
        while stack:
            node = stack.pop()
            node.ids = np.fromiter(node.children.keys(), dtype=np.int64, count=len(node.children))
            stack.extend(node.children.values())

    def bias(self, logits: np.ndarray, node: _Node) -> np.ndarray:
        if node is self.root and not self.start_bonus:
            return logits
        boosted = logits.copy()
        if node is not self.root:
            if node.terminal:
                boosted[self.inword_ids] -= self.cont_bonus
            if node.ids.size:
                boosted[node.ids] = logits[node.ids] + self.cont_bonus
        if self.start_bonus and self.root.ids.size:
            starts = logits[self.root.ids] + self.start_bonus
            boosted[self.root.ids] = np.maximum(boosted[self.root.ids], starts)
        return boosted

    def advance(self, node: _Node, token: int) -> _Node:
        child = node.children.get(token)
        if child is None:
            child = self.root.children.get(token)
        return child if child is not None else self.root


def segmentations(text: str, pieces: dict[str, int], max_paths: int = 12) -> list[list[int]]:
    """All ways (fewest pieces first) to spell `text` with the model's vocabulary pieces."""
    n = len(text)
    longest = max((len(p) for p in pieces), default=1)
    paths: list[list[list[int]]] = [[] for _ in range(n + 1)]
    paths[0] = [[]]
    for i in range(n):
        if not paths[i]:
            continue
        for size in range(1, min(longest, n - i) + 1):
            tok = pieces.get(text[i : i + size])
            if tok is None:
                continue
            bucket = paths[i + size]
            bucket.extend(p + [tok] for p in paths[i])
            bucket.sort(key=len)
            del bucket[max_paths:]
    return paths[n]


def _fast_tdt_class():
    from onnx_asr.models.nemo import NemoConformerTdt

    class FastTdt(NemoConformerTdt):
        """onnx-asr's Parakeet TDT with two additions:

        * the encoder input is padded to fixed buckets on the GPU (see GPU_BUCKETS);
        * optional dictionary boosting: greedy decoding with a bonus for tokens that continue a dictionary
          word (BoostTrie). With no dictionary the original decoding loop runs unchanged.
        """

        bucket_frames: tuple[int, ...] = ()
        boost: BoostTrie | None = None
        last_acoustic: AcousticContext | None = None

        def _decoding(self, encoder_out, encoder_out_lens, /, **kwargs):
            """Greedy TDT decoding (same decisions as onnx-asr's loop) that also records, for every emitted
            token, the model's own log-probability of it, so words carry a confidence score. With a
            dictionary, the BoostTrie bias steers the choice; confidence is still the unbiased model's."""
            trie = self.boost
            blank = self._blank_idx
            for encodings, encodings_len in zip(encoder_out, encoder_out_lens, strict=True):
                prev_state = self._create_state()
                tokens: list[int] = []
                timestamps: list[int] = []
                logprobs: list[float] = []
                durs: list[int] = []
                states: list = []
                prevs: list[int] = []
                node = trie.root if trie is not None else None
                t = 0
                emitted = 0
                while t < encodings_len:
                    logits, step, state = self._decode(tokens, prev_state, encodings[t])
                    scores = trie.bias(logits, node) if trie is not None else logits
                    token = int(scores.argmax())
                    if token != blank:
                        states.append(prev_state)
                        prevs.append(tokens[-1] if tokens else blank)
                        prev_state = state
                        tokens.append(token)
                        timestamps.append(t)
                        top = float(logits.max())
                        logprobs.append(float(logits[token]) - top - float(np.log(np.exp(logits - top).sum())))
                        emitted += 1
                        if trie is not None:
                            node = trie.advance(node, token)
                    if step > 0:
                        t += step
                        emitted = 0
                    elif token == blank or emitted == self._max_tokens_per_step:
                        t += 1
                        emitted = 0
                    if token != blank:
                        durs.append(t - timestamps[-1])
                self.last_acoustic = AcousticContext(
                    encodings[: int(encodings_len)], tokens, timestamps, durs, states, prevs
                )
                yield tokens, timestamps, logprobs

        def _encode(self, features, features_lens):
            if self.bucket_frames:
                length = features.shape[2]
                target = next((b for b in self.bucket_frames if b >= length), None)
                if target is None:
                    step = self.bucket_frames[0]
                    target = -(-length // step) * step
                if target > length:
                    features = np.pad(features, ((0, 0), (0, 0), (0, target - length)))
            return super()._encode(features, features_lens)

    return FastTdt


class ParakeetEngine:
    """Loads a Parakeet TDT ONNX export (onnx-asr layout) and transcribes 16 kHz mono float32 audio."""

    def __init__(self, model_dir: Path, device: str = "auto"):
        self.model_dir = Path(model_dir)
        self.requested_device = device  # "auto" | "cuda" | "cpu"
        self.device = "none"  # resolved after load(): "cuda" | "cpu"
        self.device_label = ""
        self.fallback_reason: str | None = None
        self._asr = None
        self._lock = threading.Lock()
        self.vocabulary = None  # tiro.vocab.Vocabulary whose spellings are boosted while decoding
        self._boost_args: tuple | None = None
        self._recent_ac: deque[AcousticContext] = deque(maxlen=4)  # keeps the last few decodes re-scorable
        self.variant = ""  # "fp32" | "int8" (which model files were loaded)
        self.gpu_info = None  # tiro.gpu.GpuInfo when an NVIDIA GPU was found

    # ------------------------------------------------------------------ loading
    def load(self) -> None:
        t0 = time.perf_counter()
        want_gpu = self.requested_device in ("auto", "cuda")
        info = gpu.detect_nvidia_gpu() if want_gpu else None
        self.gpu_info = info
        if want_gpu:
            if info is None:
                self.fallback_reason = "No NVIDIA GPU found"
            elif not info.usable:
                self.fallback_reason = info.problem
            elif not gpu.prepare_cuda_runtime():
                self.fallback_reason = "CUDA runtime could not be loaded"
        if want_gpu and info is not None and info.usable and self.fallback_reason is None:
            try:
                self._asr = self._build(cuda=True)
                self.device = "cuda"
                self.device_label = f"{info.name} (CUDA)"
                self._warmup()
            except Exception as exc:
                log.exception("CUDA initialisation failed, falling back to CPU")
                self.fallback_reason = f"CUDA initialisation failed: {exc}"
                self._asr = None
        if self._asr is None and sys.platform == "darwin" and self.requested_device in ("auto", "cuda"):
            # Apple Silicon: the encoder runs through Core ML (GPU / Neural Engine), the small decoder on CPU
            try:
                self._asr = self._build(coreml=True)
                self.device = "coreml"
                self.device_label = "Apple GPU / Neural Engine (Core ML)"
                self._warmup()
                self.fallback_reason = None
            except Exception as exc:
                log.exception("Core ML initialisation failed, falling back to CPU")
                self.fallback_reason = f"Core ML unavailable: {exc}"
                self._asr = None
        if self._asr is None:
            if self.requested_device == "cuda":
                log.warning("GPU requested but unavailable (%s); using CPU", self.fallback_reason)
            self._asr = self._build(cuda=False)
            self.device = "cpu"
            self.device_label = "CPU"
            self._warmup()
        self._apply_boost()
        log.info("ASR ready on %s in %.1fs (%s)", self.device_label, time.perf_counter() - t0, self.model_dir.name)

    def _build(self, *, cuda: bool = False, coreml: bool = False):
        import onnxruntime as ort
        from onnx_asr.loader import Manager
        from onnx_asr.resolver import Resolver

        so = ort.SessionOptions()
        so.log_severity_level = 3
        # ORT's CPU thread pool busy-spins between runs by default; for a background app that means
        # several cores pegged while dictating. On the GPU only a few tiny ops fall back to CPU.
        so.add_session_config_entry("session.intra_op.allow_spinning", "0")
        so.add_session_config_entry("session.inter_op.allow_spinning", "0")
        so.inter_op_num_threads = 1
        so.intra_op_num_threads = 2 if cuda else 0
        if cuda:
            providers = [
                (
                    "CUDAExecutionProvider",
                    {
                        "device_id": 0,
                        "cudnn_conv_algo_search": "EXHAUSTIVE",
                        "arena_extend_strategy": "kSameAsRequested",
                    },
                ),
                "CPUExecutionProvider",
            ]
        elif coreml:
            from tiro.paths import data_dir

            if "CoreMLExecutionProvider" not in ort.get_available_providers():
                raise RuntimeError("this onnxruntime build has no Core ML provider")
            cache = data_dir() / "coreml-cache"
            cache.mkdir(parents=True, exist_ok=True)
            so.intra_op_num_threads = 2
            providers = [
                (
                    "CoreMLExecutionProvider",
                    {
                        "ModelFormat": "MLProgram",
                        "MLComputeUnits": "ALL",
                        "RequireStaticInputShapes": "1",  # with the fixed-size buckets below
                        "ModelCacheDirectory": str(cache),
                    },
                ),
                "CPUExecutionProvider",
            ]
        else:
            providers = ["CPUExecutionProvider"]
        manager = Manager(so, providers)
        cls = _fast_tdt_class()
        full = (self.model_dir / "encoder-model.onnx").is_file() and (self.model_dir / "encoder-model.onnx.data").is_file()
        if (cuda or coreml) and not full:
            raise RuntimeError("GPU mode needs the full-precision model; this install has the compact CPU model")
        quant = None if full else "int8"
        files = Resolver(cls, None, self.model_dir, offline=True).resolve_model(quantization=quant)
        asr = cls(files, manager._create_preprocessor, manager.default_onnx_config)
        asr.bucket_frames = GPU_BUCKETS if (cuda or coreml) else ()
        self.variant = "fp32" if full else "int8"
        if coreml:
            providers = ["CPUExecutionProvider"]  # the decoder runs step by step: CPU is faster for it
        if not cuda and full:
            # The int8 joint network is ~4x faster on CPU with identical accuracy on our tests.
            dso = ort.SessionOptions()
            dso.log_severity_level = 3
            dso.intra_op_num_threads = 1
            dso.inter_op_num_threads = 1
            int8 = self.model_dir / "decoder_joint-model.int8.onnx"
            if int8.is_file():
                asr._decoder_joint = ort.InferenceSession(str(int8), sess_options=dso, providers=providers)
        return asr

    def _warmup(self) -> None:
        rng = np.random.default_rng(0)
        noise = (rng.standard_normal(SAMPLE_RATE * 2) * 0.01).astype(np.float32)
        self.transcribe(noise)
        if self.device in ("cuda", "coreml"):
            self.transcribe(np.tile(noise, 6))  # prime the 20 s bucket as well

    @property
    def ready(self) -> bool:
        return self._asr is not None

    def set_vocabulary(self, vocab, start_bonus: float = BOOST_START, cont_bonus: float = BOOST_CONT) -> int:
        """Use a personal dictionary (tiro.vocab.Vocabulary, or None to clear). Returns the number of
        token-piece spellings compiled into the decoder's boosting trie."""
        with self._lock:  # a dictation may be decoding on another thread
            self.vocabulary = vocab if vocab else None
            self._boost_args = (vocab, start_bonus, cont_bonus) if vocab else None
            return self._apply_boost()

    def _apply_boost(self) -> int:
        asr = self._asr
        if asr is None:
            return 0
        if self._boost_args is None:
            asr.boost = None
            return 0
        vocab, start_bonus, cont_bonus = self._boost_args
        pieces = {piece: tid for tid, piece in asr._vocab.items() if not piece.startswith("<")}
        seqs = []
        for form in vocab.surface_forms():
            seqs.extend(segmentations(" " + form.strip(), pieces))
        # pieces that continue a word (no leading space, not punctuation, not a possessive like "'s")
        inword = np.array(
            [
                tid
                for piece, tid in pieces.items()
                if not piece.startswith((" ", "'")) and any(c.isalnum() for c in piece)
            ],
            dtype=np.int64,
        )
        asr.boost = BoostTrie(seqs, start_bonus, cont_bonus, inword) if seqs else None
        return len(seqs)

    def ping(self) -> None:
        """Cheap inference that wakes the GPU clocks and re-primes the common bucket shape."""
        if self._asr is not None:
            self.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32))

    # ------------------------------------------------------------------ inference
    def transcribe(self, audio: np.ndarray) -> list[Word]:
        """Transcribe mono 16 kHz float32 audio. Word times are relative to the start of `audio`."""
        if self._asr is None:
            raise RuntimeError("ASR model not loaded")
        if audio.size < SAMPLE_RATE // 10:
            return []
        wav = np.ascontiguousarray(audio, dtype=np.float32)[None, :]
        lens = np.array([wav.shape[1]], dtype=np.int64)
        with self._lock:
            result = next(iter(self._asr.recognize_batch(wav, lens)))
            ac = self._asr.last_acoustic
        words = tokens_to_words(result.tokens or [], result.timestamps or [], result.logprobs)
        if ac is not None and len(ac.ids) == len(result.tokens or []):
            self._recent_ac.append(ac)
            ref = weakref.ref(ac)
            for w in words:
                w.ac = ref
        return words

    # ------------------------------------------------------------------ re-scoring
    def score_lattice(self, ac: AcousticContext, t0: int, t1: int, prev: int, state, targets: list[list[int]]):
        """Joint-network outputs over frames [t0, t1) of a decode for each target token sequence (fed after
        token `prev` from prediction-network `state`). Returns an array [rows, frames, longest + 1, outputs]."""
        asr = self._asr
        if asr is None:
            raise RuntimeError("ASR model not loaded")
        rows = len(targets)
        width = max(len(t) for t in targets) + 1
        tg = np.full((rows, width), asr._blank_idx, dtype=np.int32)
        for i, seq in enumerate(targets):
            tg[i, 0] = prev
            tg[i, 1 : len(seq) + 1] = seq
        enc = np.ascontiguousarray(ac.enc[t0:t1].T[None], dtype=np.float32)
        feed = {
            "encoder_outputs": np.repeat(enc, rows, axis=0),
            "targets": tg,
            "target_length": np.array([len(t) + 1 for t in targets], dtype=np.int32),
            "input_states_1": np.repeat(state[0], rows, axis=1),
            "input_states_2": np.repeat(state[1], rows, axis=1),
        }
        with self._lock:
            (out,) = asr._decoder_joint.run(["outputs"], feed)
        return out

    @property
    def pieces(self) -> dict[str, int]:
        """The recogniser's word pieces (a leading space starts a word) -> token id."""
        if self._asr is None:
            return {}
        return {piece: tid for tid, piece in self._asr._vocab.items() if not piece.startswith("<")}

    @property
    def blank_id(self) -> int:
        return self._asr._blank_idx if self._asr is not None else -1


def tokens_to_words(tokens: list[str], timestamps: list[float], logprobs: list[float] | None = None) -> list[Word]:
    """Group SentencePiece tokens (leading space = new word) into words with start/end times, the
    probability of each word's least certain token, and the range of tokens it came from."""
    words: list[Word] = []
    lps = logprobs if logprobs is not None and len(logprobs) == len(tokens) else [0.0] * len(tokens)
    for i, (tok, ts, lp) in enumerate(zip(tokens, timestamps, lps, strict=False)):
        p = float(np.exp(lp))
        if tok.startswith(" ") or not words:
            words.append(Word(tok.strip(), ts, ts + FRAME_SEC, p, (i, i + 1)))
        else:
            last = words[-1]
            last.text += tok
            last.end = ts + FRAME_SEC
            last.conf = min(last.conf, p)
            last.tok = (last.tok[0], i + 1) if last.tok else None
    return [w for w in words if w.text]
