"""Keeps the correction pass supplied: history, profile, knowledge rebuilds, learning, metrics.

Rebuilding (re-reading history, re-indexing the lexicon) only happens on a background thread while you
are not dictating. Nothing here touches the network.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from tiro.correct import knowledge
from tiro.correct.corrector import Corrector, CorrectionRun, Result
from tiro.correct.history import Correction, HistoryStore, Profile
from tiro.correct.metrics import Metrics
from tiro.correct.text import letters, similarity

log = logging.getLogger(__name__)

BOOST_HISTORY_MIN = 5  # history terms used at least this often are also boosted in the decoder ...
BOOST_HISTORY_MAX = 40  # ... up to this many
TARGET_MEDIAN_MS = 30.0  # correction should add no more than this to a typical commit


class CorrectionService:
    def __init__(self, db_path: Path, *, dictionary: list[str], history_enabled: bool, mode: str,
                 gate_conf: float | None, enabled: bool, on_knowledge: Callable[[], None] | None = None):
        self.store = HistoryStore(db_path)
        self.profile: Profile | None = None
        self.corrector = Corrector(mode=mode, gate_conf=gate_conf)
        self.corrector.enabled = enabled
        self.metrics = Metrics()
        self.language_status = "off"
        self.language_choice = ""
        self.history_enabled = history_enabled
        self.on_knowledge = on_knowledge  # called (on the worker thread) after the knowledge changed
        self._dictionary = list(dictionary)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._dirty = True  # knowledge needs a rebuild
        self._reload_profile = True  # history needs re-reading from disk
        self._pending_texts: list[tuple[str, str]] = []  # dictations to fold into the profile
        self._active = 0  # dictations in progress (rebuilds wait for 0)
        self._version = 0
        self._ready = threading.Event()  # first knowledge build done
        self._closed = False
        self._thread = threading.Thread(target=self._worker, name="tiro-knowledge", daemon=True)
        self._thread.start()
        self._wake.set()  # build the knowledge right away

    # ------------------------------------------------------------------ settings
    def set_dictionary(self, lines: list[str]) -> None:
        with self._lock:
            self._dictionary = list(lines)
        self._schedule()

    def set_mode(self, mode: str, gate_conf: float | None) -> None:
        self.corrector.set_mode(mode, gate_conf)

    def set_enabled(self, on: bool) -> None:
        self.corrector.enabled = bool(on)

    def set_history_enabled(self, on: bool) -> None:
        self.history_enabled = bool(on)
        with self._lock:
            self._reload_profile = True
        self._schedule()

    def attach_engine(self, engine) -> None:
        """Let tier 1 re-score candidates against the audio with this recogniser (None to detach)."""
        if engine is None:
            self.corrector.scorer = None
            return
        from tiro.correct.acoustic import AcousticScorer

        self.corrector.scorer = AcousticScorer(engine)

    # ------------------------------------------------------------------ dictation lifecycle
    def begin(self) -> CorrectionRun:
        with self._lock:
            self._active += 1
        run = self.corrector.begin()
        run.on_metrics = self.metrics.add
        return run

    def end(self, text: str, *, app: str = "", title: str = "", secure: bool = False) -> None:
        """A dictation finished. Its text joins your history unless learning is off or the field was secure."""
        with self._lock:
            self._active = max(0, self._active - 1)
            if text.strip() and self.history_enabled and not secure:
                self._pending_texts.append((text, app))
                try:
                    self.store.add(text, app, title)
                except Exception:
                    log.exception("could not save dictation history")
                self._dirty = True
        self._wake.set()

    # ------------------------------------------------------------------ learning
    def learn(self, heard: str, written: str, context: str = "") -> bool:
        """Remember a fix you made, so the same mishearing is corrected next time."""
        heard, written = heard.strip(), written.strip()
        if not heard or not written or heard == written or not letters(written):
            return False
        if len(heard.split()) > 4 or len(written.split()) > 4:
            return False
        if letters(heard) != letters(written) and similarity(heard, written) < 0.3:
            return False  # not a mishearing (a different word altogether)
        self.store.add_correction(heard, written, context)
        self._schedule()
        return True

    def corrections(self) -> list[Correction]:
        return self.store.corrections()

    def delete_correction(self, heard: str) -> None:
        self.store.delete_correction(heard)
        self._schedule()

    def update_correction(self, old_heard: str, heard: str, written: str) -> None:
        self.store.update_correction(old_heard, heard, written)
        self._schedule()

    def history_terms(self) -> list[tuple[str, int]]:
        p = self.profile
        return p.notable_terms(knowledge.HISTORY_MIN_COUNT) if p is not None else []

    def history_count(self) -> int:
        try:
            return self.store.count()
        except Exception:
            return 0

    def clear_history(self) -> None:
        """Forget every past dictation (fixes you taught and your dictionary stay)."""
        self.store.clear()
        with self._lock:
            self._pending_texts.clear()
            self._reload_profile = True
        self._schedule()

    def forget_everything(self) -> None:
        self.store.clear()
        self.store.clear_corrections()
        with self._lock:
            self._pending_texts.clear()
            self._reload_profile = True
        self._schedule()

    def export(self, path: Path) -> None:
        with self._lock:
            dictionary = list(self._dictionary)
        self.store.export(path, self.profile, dictionary, include_history=True)

    def boost_terms(self) -> list[str]:
        """Learned and frequent words to boost in the decoder, besides your dictionary (kept short)."""
        out = [c.written for c in self.store.corrections()]
        for written, count in self.history_terms():
            if count < BOOST_HISTORY_MIN or len(out) >= BOOST_HISTORY_MAX + 20:
                break
            out.append(written)
        return out

    # ------------------------------------------------------------------ tier 2: language model
    def configure_language(self, *, enabled: bool, choice: str, device: str, vram_gb: float | None, gpu_lock,
                           on_status: Callable[[str], None] | None = None, allow_download: bool = True,
                           gpu_yield: Callable[[], bool] | None = None) -> None:
        """(Re)load the tier-2 model in the background to match the settings.

        choice: auto | small | tiny. device: where the speech model runs ("cuda" or "cpu"); the language
        model goes on the same device, so it never needs a GPU the speech model isn't already using."""
        self._lm_gen = getattr(self, "_lm_gen", 0) + 1
        gen = self._lm_gen
        self.corrector.language = None
        self.language_status = "off"
        if not enabled:
            return
        import sys

        from tiro.models import LANGUAGE_MODELS, MLX_LANGUAGE_MODELS

        mlx = sys.platform == "darwin" and _has_mlx()
        if choice not in LANGUAGE_MODELS:
            choice = "small" if mlx or (device == "cuda" and (vram_gb or 0) >= 6) else "tiny"
        spec = (MLX_LANGUAGE_MODELS if mlx else LANGUAGE_MODELS)[choice]
        variant = "fp32" if device == "cuda" and not mlx else "int8"  # GPU build / CPU build (MLX: one build)
        if mlx:
            variant = "fp32"
        lm_device = "cuda" if device == "cuda" else "cpu"
        self.language_choice = choice

        def status(text: str) -> None:
            self.language_status = text
            if on_status is not None:
                on_status(text)

        def work() -> None:
            from tiro.correct.llm import LanguageScorer
            from tiro.models import DownloadCancelled, download_model, find_model

            try:
                folder = find_model(spec, variant)
                if folder is None:
                    if not allow_download:
                        status(f"{spec.title} not downloaded")
                        return
                    status(f"Downloading {spec.title}…")

                    def progress(done, total):
                        if total and gen == self._lm_gen:
                            status(f"Downloading {spec.title}… {done * 100 // total}%")

                    folder = download_model(spec, progress, variant=variant)
                if gen != self._lm_gen:
                    return
                status(f"Loading {spec.title}…")
                if mlx:
                    from tiro.correct.llm_mlx import MlxLanguageScorer

                    lm = MlxLanguageScorer(folder)
                    where = "Apple GPU"
                else:
                    onnx_file = next(f for f in spec.variant_files(variant) if f.endswith(".onnx"))
                    lm = LanguageScorer(folder, device=lm_device, gpu_lock=gpu_lock, file=onnx_file,
                                        gpu_yield=gpu_yield)
                    where = "GPU" if lm_device == "cuda" else "CPU"
                lm.load()
                if gen != self._lm_gen:
                    return
                self.corrector.language = lm
                status(f"{spec.title} on {where}")
            except DownloadCancelled:
                return
            except Exception as exc:
                log.exception("language model failed to load")
                status(f"AI check unavailable: {exc}")

        threading.Thread(target=work, name="tiro-language-model", daemon=True).start()

    def check_speed(self, budget_ms: float) -> str | None:
        """Auto-degrade: if correction is making typing late, step down and say what changed (else None).

        Steps: smaller language model -> examine fewer words -> language model off."""
        s = self.metrics.summary(last=40)
        if s["commits"] < 20:
            return None
        median_ok = s["median_ms"] <= TARGET_MEDIAN_MS
        p95_ok = s["p95_ms"] <= budget_ms
        if median_ok and p95_ok:
            return None
        self.metrics.reset()
        if self.corrector.language is not None and getattr(self, "language_choice", "") == "small":
            return "small"
        if self.corrector.mode.gate_conf > 0.6:
            return "gate"
        if self.corrector.language is not None:
            return "off"
        return None

    def close(self) -> None:
        self._closed = True
        self._wake.set()
        try:
            self.store.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ background rebuilds
    def _schedule(self) -> None:
        with self._lock:
            self._dirty = True
        self._wake.set()

    def _worker(self) -> None:
        knowledge.starter_lexicon()  # index the starter set once, up front
        while not self._closed:
            self._wake.wait(timeout=30.0)
            self._wake.clear()
            if self._closed:
                return
            while True:  # never rebuild during a dictation
                with self._lock:
                    busy = self._active > 0
                if not busy or self._closed:
                    break
                time.sleep(0.2)
            try:
                self._rebuild()
            except Exception:
                log.exception("rebuilding correction knowledge failed")

    def _rebuild(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            reload_profile, self._reload_profile = self._reload_profile, False
            pending, self._pending_texts = self._pending_texts, []
            dictionary = list(self._dictionary)
        t0 = time.perf_counter()
        if not self.history_enabled:
            self.profile = None
        elif reload_profile or self.profile is None:
            self.profile = Profile.build(self.store.recent(20000))
        else:
            for text, app in pending:
                self.profile.add(text, app)
        self._version += 1
        k = knowledge.build(dictionary, self.store.corrections(), self.profile, knowledge.starter_lexicon(),
                            self._version)
        self.corrector.knowledge = k
        self._ready.set()
        log.info("correction knowledge v%d: %d of your terms, %d taught fixes, %d starter terms (%.0f ms)",
                 k.version, len(k.user), len(k.user.rules), len(k.starter), (time.perf_counter() - t0) * 1000)
        if self.on_knowledge is not None:
            try:
                self.on_knowledge()
            except Exception:
                log.exception("on_knowledge failed")

    def wait_ready(self, timeout: float = 10.0) -> bool:
        """Block until the first knowledge build finished (for tests and the setup benchmark)."""
        return self._ready.wait(timeout)


def _has_mlx() -> bool:
    try:
        import mlx.core  # noqa: F401
        import mlx_lm  # noqa: F401

        return True
    except Exception:
        return False


def summarize(res: Result) -> str:
    """One log line about a correction result, without the words themselves."""
    return f"tier {res.tier}, {res.flagged} flagged, {len(res.changes)} changed, {res.ms:.1f} ms"
