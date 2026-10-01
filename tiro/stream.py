"""Streaming transcription on top of an offline ASR model.

The window of audio since the last trim point is re-decoded every few hundred milliseconds. A word is
committed (typed) only when two consecutive decodes agree on it (LocalAgreement-2) *and* enough speech
follows it that the model had right context for its punctuation and casing. Committed audio is trimmed at
sentence boundaries (keeping a little left context), so the cost per decode stays bounded no matter how
long the user talks. Long pauses are compressed so silence never piles up in the window.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from tiro.asr import Word
from tiro.textproc import ends_sentence, norm_word


@dataclass
class StreamParams:
    sample_rate: int = 16_000
    step_sec: float = 0.4  # speech to accumulate between partial decodes
    min_decode_speech_sec: float = 0.35  # speech needed before the first partial decode
    right_speech_sec: float = 0.9  # speech that must follow a word before it is committed
    min_tail_gap_sec: float = 0.25  # never commit words this close to the end of the window
    keep_silence_sec: float = 0.45  # pause kept verbatim inside the window
    preroll_sec: float = 0.3  # audio kept before speech (re)starts
    trim_after_sec: float = 5.0  # trim at a committed sentence end once the window is this long
    max_window_sec: float = 18.0  # trim at any committed word beyond this
    left_context_sec: float = 1.0  # committed audio kept in the window after a trim
    force_commit_age_sec: float = 3.0  # at max window, words older than this are committed regardless
    dedupe_window_sec: float = 1.0


@dataclass
class StreamUpdate:
    committed: list[Word] = field(default_factory=list)
    pending: list[Word] = field(default_factory=list)
    final: bool = False


class StreamingTranscriber:
    def __init__(
        self,
        transcribe: Callable[[np.ndarray], list[Word]],
        params: StreamParams | None = None,
        hold_back: Callable[[list[Word], list[Word]], int] | None = None,
    ):
        self.transcribe = transcribe
        self.p = params or StreamParams()
        self.hold_back = hold_back or (lambda words, rest: 0)  # (committable, rest) -> words to keep back
        self.sr = self.p.sample_rate
        self.t = 0  # samples appended to the (silence-compressed) timeline
        self.committed: list[Word] = []
        self.last_commit_end = float("-inf")
        self.decodes = 0
        self._ring: list[np.ndarray] = []
        self._ring_len = 0
        self.silence_run = 0  # samples of consecutive non-speech input
        self._reset_window()

    # ------------------------------------------------------------------ audio input
    def _reset_window(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._frames: list[tuple[int, int, bool]] = []  # (timeline start, length, is speech)
        self.win_start = self.t
        self._prev_hyp: list[Word] = []
        self._speech_since_decode = 0
        self._window_speech = 0

    def _append(self, frame: np.ndarray, speech: bool) -> None:
        self._chunks.append(frame)
        self._frames.append((self.t, len(frame), speech))
        self.t += len(frame)
        if speech:
            self._speech_since_decode += len(frame)
            self._window_speech += len(frame)

    def push(self, frame: np.ndarray, speech: bool) -> None:
        """Feed one VAD frame of 16 kHz audio together with its speech decision."""
        if speech:
            if not self._chunks:
                self.win_start = self.t
            for f in self._ring:
                self._append(f, False)
            self._ring.clear()
            self._ring_len = 0
            self._append(frame, True)
            self.silence_run = 0
            return
        self.silence_run += len(frame)
        if self._window_speech and self.silence_run <= self.p.keep_silence_sec * self.sr:
            self._append(frame, False)
            return
        self._ring.append(frame)
        self._ring_len += len(frame)
        while self._ring and self._ring_len - len(self._ring[0]) >= self.p.preroll_sec * self.sr:
            self._ring_len -= len(self._ring.pop(0))

    # ------------------------------------------------------------------ state
    @property
    def has_speech(self) -> bool:
        return self._window_speech > 0

    @property
    def pending(self) -> list[Word]:
        return list(self._prev_hyp)

    @property
    def window_sec(self) -> float:
        return (self.t - self.win_start) / self.sr

    def due(self) -> bool:
        if self._window_speech < self.p.min_decode_speech_sec * self.sr or self._speech_since_decode == 0:
            return False
        return self._speech_since_decode >= self.p.step_sec * self.sr or self.silence_run >= 0.2 * self.sr

    # ------------------------------------------------------------------ decoding
    def _decode(self) -> list[Word]:
        audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
        offset = self.win_start / self.sr
        self.decodes += 1
        self._speech_since_decode = 0
        return [w.shifted(offset) for w in self.transcribe(audio)]

    def _fresh(self, words: list[Word]) -> list[Word]:
        """Drop words that belong to already-committed audio (by time, then by n-gram overlap)."""
        if not self.committed or self.win_start / self.sr >= self.last_commit_end:
            return words  # window holds no committed audio (fresh utterance)
        new = [w for w in words if w.start > self.last_commit_end - 0.1]
        if new and abs(new[0].start - self.last_commit_end) < self.p.dedupe_window_sec:
            tail = [norm_word(w.text) for w in self.committed[-5:]]
            head = [norm_word(w.text) for w in new[:5]]
            for n in range(min(len(tail), len(head)), 0, -1):
                if tail[-n:] == head[:n]:
                    new = new[n:]
                    break
        return new

    def _speech_after(self, t_sec: float) -> float:
        start = t_sec * self.sr
        total = 0
        for t0, length, speech in reversed(self._frames):
            if t0 < start:
                break
            if speech:
                total += length
        return total / self.sr

    def _committable(self, word: Word) -> bool:
        if word.end > self.t / self.sr - self.p.min_tail_gap_sec:
            return False
        return self._speech_after(word.end) >= self.p.right_speech_sec

    def _commit(self, words: list[Word]) -> None:
        if words:
            self.committed.extend(words)
            self.last_commit_end = words[-1].end

    def step(self) -> StreamUpdate:
        """Partial decode: commit what is stable, return the rest as pending."""
        new = self._fresh(self._decode())
        k = 0
        for a, b in zip(self._prev_hyp, new, strict=False):
            if a.text != b.text:
                break
            k += 1
        while k > 0 and not self._committable(new[k - 1]):
            k -= 1
        if k:
            k -= min(k, self.hold_back(new[:k], new[k:]))
        commit = new[:k]
        self._commit(commit)
        self._prev_hyp = new[k:]
        commit += self._maybe_trim()
        return StreamUpdate(commit, list(self._prev_hyp))

    def finalize(self) -> StreamUpdate:
        """Decode everything left in the window and commit all of it (end of utterance)."""
        commit: list[Word] = []
        if self._window_speech:
            commit = self._fresh(self._decode())
            self._commit(commit)
        self._reset_window()
        return StreamUpdate(commit, [], final=True)

    # ------------------------------------------------------------------ trimming
    def _maybe_trim(self) -> list[Word]:
        win = self.window_sec
        if win < self.p.trim_after_sec:
            return []
        forced: list[Word] = []
        committed_here = self.committed and self.last_commit_end > self.win_start / self.sr
        if win > self.p.max_window_sec and not committed_here:
            horizon = self.t / self.sr - self.p.force_commit_age_sec
            k = 0
            while k < len(self._prev_hyp) and self._prev_hyp[k].end <= horizon:
                k += 1
            forced = self._prev_hyp[:k]
            self._commit(forced)
            self._prev_hyp = self._prev_hyp[k:]
            committed_here = bool(forced)
        if not committed_here:
            return forced
        if ends_sentence(self.committed[-1].text) or win > self.p.max_window_sec:
            self._cut(self.last_commit_end - self.p.left_context_sec)
        return forced

    def _cut(self, t_sec: float) -> None:
        cut = int(t_sec * self.sr)
        drop = 0
        while drop < len(self._frames) and self._frames[drop][0] + self._frames[drop][1] <= cut:
            drop += 1
        if not drop:
            return
        del self._chunks[:drop]
        del self._frames[:drop]
        self.win_start = self._frames[0][0] if self._frames else self.t
        self._window_speech = sum(length for _, length, speech in self._frames if speech)
