"""StreamingTranscriber logic with a fake recogniser that 'hears' a scripted word timeline."""

import numpy as np

from tiro.asr import Word
from tiro.stream import StreamingTranscriber, StreamParams

SR = 16_000
FRAME = 512


class FakeAsr:
    """'Hears' scripted words. Each pushed frame is filled with its original frame number, so the fake can
    tell exactly which stretch of the original timeline a (silence-compressed, trimmed) window contains."""

    def __init__(self, words):
        self.words = words  # (text, start, end) in original seconds
        self.st: StreamingTranscriber | None = None

    def __call__(self, audio):
        frames = audio[::FRAME].astype(int) - 1  # original frame index at each frame position
        pos = {f: i for i, f in enumerate(frames)}
        out = []
        for text, s, e in self.words:
            first, last = int(s * SR) // FRAME, int(e * SR) // FRAME
            if first in pos and last in pos and all(f in pos for f in range(first, last + 1)):
                out.append(Word(text, pos[first] * FRAME / SR, (pos[last] + 1) * FRAME / SR))
        return out


def frame_audio(i):
    return np.full(FRAME, float(i + 1), dtype=np.float32)


def run(words, total_sec, speech_spans, params=None):
    asr = FakeAsr(words)
    st = StreamingTranscriber(asr, params or StreamParams())
    asr.st = st
    committed = []
    n = int(total_sec * SR) // FRAME
    for i in range(n):
        t = i * FRAME / SR
        speech = any(a <= t < b for a, b in speech_spans)
        st.push(frame_audio(i), speech)
        if st.due():
            committed += [w.text for w in st.step().committed]
    committed += [w.text for w in st.finalize().committed]
    return committed, st


def _timeline(text, start=0.5, dur=0.35):
    out, t = [], start
    for w in text.split():
        out.append((w, t, t + dur))
        t += dur + 0.05
    return out, t


def test_long_continuous_speech_commits_every_word_once():
    words, end = _timeline(" ".join(f"w{i}." if i % 12 == 11 else f"w{i}" for i in range(120)))
    committed, st = run(words, end + 1.0, [(0.4, end)])
    assert committed == [w for w, _, _ in words]
    assert st.decodes > 20


def test_window_is_trimmed_so_it_stays_bounded():
    words, end = _timeline(" ".join(f"w{i}." if i % 8 == 7 else f"w{i}" for i in range(200)))
    asr = FakeAsr(words)
    st = StreamingTranscriber(asr, StreamParams())
    asr.st = st
    longest = 0.0
    for i in range(int((end + 1) * SR) // FRAME):
        t = i * FRAME / SR
        st.push(frame_audio(i), 0.4 <= t < end)
        if st.due():
            st.step()
            longest = max(longest, st.window_sec)
    assert longest < StreamParams().max_window_sec + 1.0


def test_nothing_committed_during_a_pause_until_speech_resumes():
    first, t = _timeline("I think that", start=0.5)
    second, end = _timeline("we should go.", start=t + 3.0)
    asr = FakeAsr(first + second)
    st = StreamingTranscriber(asr, StreamParams())
    asr.st = st
    committed_at_pause_end = None
    committed = []
    for i in range(int((end + 1) * SR) // FRAME):
        tt = i * FRAME / SR
        speech = (0.4 <= tt < t) or (t + 2.9 <= tt < end)
        st.push(frame_audio(i), speech)
        if st.due():
            committed += [w.text for w in st.step().committed]
        if committed_at_pause_end is None and tt >= t + 2.8:
            committed_at_pause_end = list(committed)
    committed += [w.text for w in st.finalize().committed]
    assert "that" not in committed_at_pause_end  # the tail waits for right context
    assert committed == ["I", "think", "that", "we", "should", "go."]


def test_silence_only_produces_nothing():
    committed, st = run([], 3.0, [])
    assert committed == [] and st.decodes == 0


def test_short_utterance_is_committed_on_finalize():
    words = [("Yes.", 0.5, 0.8)]
    committed, _ = run(words, 1.0, [(0.45, 0.85)])
    assert committed == ["Yes."]


def test_repeated_word_after_finalize_is_not_deduplicated():
    asr = FakeAsr([("Yes.", 0.5, 0.8), ("Yes,", 1.9, 2.2), ("really.", 2.3, 2.6)])
    st = StreamingTranscriber(asr, StreamParams())
    asr.st = st
    out = []
    for i in range(int(3.0 * SR) // FRAME):
        t = i * FRAME / SR
        speech = 0.45 <= t < 0.85 or 1.85 <= t < 2.65
        st.push(frame_audio(i), speech)
        if 1.2 <= t < 1.2 + FRAME / SR:
            out += [w.text for w in st.finalize().committed]  # hands-free endpoint
        elif st.due():
            out += [w.text for w in st.step().committed]
    out += [w.text for w in st.finalize().committed]
    assert out == ["Yes.", "Yes,", "really."]
