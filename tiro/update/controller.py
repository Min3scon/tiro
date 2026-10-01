"""The app's side of updates: runs the background service, shows "update ready", restarts into the new version,
confirms a trial start to the launcher, and remembers which "What's new" you've seen.

Start-up markers (read by the launcher, installer/TiroLauncher):
- `.update/trial-ok-<token>`: a newly installed version came up properly (tray shown, speech model loaded, the
  self-test clip transcribed). Without it the launcher tries once more, then goes back to the previous version.
- `.update/started-<version>`: a normal start completed. Two starts in a row without it mean Tiro crashed while
  starting, and the launcher starts it in safe mode.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal, Slot

from tiro import WEBSITE, __version__
from tiro.update import state
from tiro.update.service import Status, UpdateService

log = logging.getLogger(__name__)


class _Sig(QObject):
    status = Signal(object)


def launcher_path() -> Path | None:
    env = os.environ.get("TIRO_LAUNCHER")
    if env and Path(env).is_file():
        return Path(env)
    root = state.install_root()
    if root is not None and (root / "Tiro.exe").is_file():
        return root / "Tiro.exe"
    return None


def _marker(name: str) -> Path | None:
    root = state.install_root()
    if root is None:
        return None
    folder = root / ".update"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


class UpdateController(QObject):
    def __init__(self, app, *, update_trial: str | None = None):
        super().__init__()
        self.app = app
        self.update_trial = update_trial
        self._sig = _Sig()
        self._sig.status.connect(self._on_status, Qt.ConnectionType.QueuedConnection)
        from tiro import gpu

        self.service = UpdateService(settings=lambda: app.settings, busy=lambda: app.dictating,
                                     on_status=self._sig.status.emit,
                                     has_gpu_runtime=getattr(gpu, "cuda_runtime_present", lambda: True))
        self.status: Status = self.service.status
        self._notified: str | None = None

    def start(self) -> None:
        self.service.start()  # also in safe mode: a fixed version may be exactly what's needed

    def stop(self) -> None:
        self.service.stop()

    # ------------------------------------------------------------------ status (GUI thread)
    @Slot(object)
    def _on_status(self, st: Status) -> None:
        self.status = st
        if st.state in ("ready", "available") and st.version and self._notified != st.version:
            self._notified = st.version
            if st.state == "ready":
                self.app.tray.notify_update(st)
            else:
                self.app.tray.notify_available(st)
        self.app.tray.set_update(st if st.state in ("ready", "available") else None)
        w = self.app.settings_window
        if w is not None and w.isVisible():
            w.refresh_updates()

    def check_now(self) -> None:
        self.service.check_now()

    @property
    def ready_version(self) -> str | None:
        return self.status.version if self.status.state == "ready" else None

    # ------------------------------------------------------------------ actions
    def restart_to_update(self) -> bool:
        """Quit and start again through the launcher, which switches to the downloaded version."""
        if self.app.dictating:
            self.app.overlay.notify("Finish dictating first; Tiro restarts to update right after.", "warning")
            return False
        version = self.ready_version
        if version and not self.service.accept(version):
            self.app.overlay.notify("The update isn't ready any more; Tiro will try again later.", "warning")
            return False
        return self.relaunch()

    def relaunch(self, extra: list[str] | None = None) -> bool:
        exe = launcher_path()
        if exe is None:
            log.warning("no launcher: can't restart into another version")
            return False
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
        subprocess.Popen([str(exe), "--after-restart", *(extra or [])], cwd=str(exe.parent), creationflags=flags,
                         close_fds=True)
        self.app.quit()
        return True

    def roll_back(self) -> str | None:
        prev = self.service.roll_back()
        if prev:
            self.relaunch()
        return prev

    def open_download_page(self) -> None:
        import webbrowser

        webbrowser.open(self.status.notes_url or WEBSITE)

    # ------------------------------------------------------------------ start-up confirmation
    def engine_ready(self, engine) -> None:
        """The speech model is loaded: confirm this start to the launcher (and, on trial, prove it works)."""
        threading.Thread(target=self._confirm, args=(engine,), name="tiro-start-check", daemon=True).start()

    def _confirm(self, engine) -> None:
        try:
            if self.update_trial:
                from tiro.paths import asset
                from tiro.selftest import CLIP, EXPECTED, _norm, _read_wav

                audio = _read_wav(asset("sounds", CLIP))
                text = " ".join(w.text for w in engine.transcribe(audio))
                got, want = _norm(text), _norm(EXPECTED)
                if sum(1 for w in want if w in got) / len(want) < 0.85:
                    log.error("update trial: the self-test clip came out wrong: %r", text)
                    return  # no confirmation: the launcher tries again, then goes back
                path = _marker(f"trial-ok-{self.update_trial}")
                if path is not None:
                    path.write_text(__version__, encoding="utf-8")
                    log.info("update trial passed: confirmed %s to the launcher", __version__)
            path = _marker(f"started-{__version__}")
            if path is not None:
                path.write_text("ok", encoding="utf-8")
        except Exception:
            log.exception("could not confirm this start")
