"""Background updates: check now and then, fetch and stage a new version quietly, tell the app when it's ready.

- First check 2 minutes after start, then about every 6 hours (with jitter so copies don't all ask at once).
- Never while you're dictating: checks and downloads wait, and a download pauses when a dictation starts.
- Skipped when Windows says there's no internet, or the connection is metered (unless you allow that).
- With "Check for updates automatically" off, Tiro contacts nobody unless you click "Check now".
- The check sends only what any download sends, plus a User-Agent naming Tiro's version, the OS version and the
  processor type. No identifiers: the staged-rollout bucket is worked out on this computer.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from tiro import GITHUB_REPO, __version__
from tiro.update import feed, net, stage, state

log = logging.getLogger(__name__)

FIRST_CHECK_SEC = float(os.environ.get("TIRO_UPDATE_FIRST_CHECK", "120"))
INTERVAL_SEC = 6 * 3600.0
JITTER_SEC = 45 * 60.0
# The feed (signed; see tiro.update.feed). Tests point this at a local server; the signature check still applies.
FEED_URL = os.environ.get("TIRO_UPDATE_FEED_URL") or \
    f"https://github.com/{GITHUB_REPO}/releases/download/update-feed/{{channel}}.json"


@dataclass
class Status:
    state: str = "idle"  # idle | checking | up-to-date | downloading | ready | available | error | offline | skipped
    detail: str = ""
    version: str = ""  # the version on offer / ready
    summary: str = ""
    notes_url: str = ""
    critical: bool = False
    last_check: str = ""  # ISO time of the last completed check
    extra: dict = field(default_factory=dict)


class UpdateService:
    def __init__(self, *, settings: Callable[[], object], busy: Callable[[], bool],
                 on_status: Callable[[Status], None] = lambda s: None,
                 has_gpu_runtime: Callable[[], bool] = lambda: True):
        self.settings = settings  # -> tiro.config.Settings (read fresh each time)
        self.busy = busy  # True while dictating
        self.on_status = on_status  # called on the service thread
        self.has_gpu_runtime = has_gpu_runtime
        self.status = Status(last_check=state.read_local().get("last_check", ""))
        self._wake = threading.Event()
        self._manual = False
        self._stop = threading.Event()
        self._pause = threading.Event()  # set while dictating: downloads stop and resume later
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ control (any thread)
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="tiro-updates", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._pause.set()
        self._wake.set()

    def check_now(self) -> None:
        self._manual = True
        self._wake.set()

    def dictation_started(self) -> None:
        self._pause.set()

    def dictation_ended(self) -> None:
        self._pause.clear()

    # ------------------------------------------------------------------ worker
    def _loop(self) -> None:
        delay = FIRST_CHECK_SEC
        while not self._stop.is_set():
            self._wake.wait(delay)
            self._wake.clear()
            if self._stop.is_set():
                return
            manual, self._manual = self._manual, False
            if manual or getattr(self.settings(), "auto_update_check", True):
                try:
                    self.check(manual=manual)
                except Exception as exc:  # never let the updater take anything else down
                    log.exception("update check failed")
                    self._set(state="error", detail=str(exc))
            delay = INTERVAL_SEC + random.uniform(-JITTER_SEC, JITTER_SEC)

    def _set(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self.status, k, v)
            snapshot = Status(**{**self.status.__dict__, "extra": dict(self.status.extra)})
        try:
            self.on_status(snapshot)
        except Exception:
            log.exception("update status handler failed")

    def _wait_idle(self) -> bool:
        """Wait while a dictation is running. False if Tiro is quitting."""
        while self.busy() and not self._stop.is_set():
            time.sleep(1.0)
        return not self._stop.is_set()

    def check(self, manual: bool = False) -> None:
        s = self.settings()
        if not self._wait_idle():
            return
        online, metered = net.connection()
        if online is False:
            self._set(state="offline", detail="No internet connection")
            return
        if metered and not manual and not getattr(s, "update_on_metered", False):
            self._set(state="skipped", detail="Metered connection: Tiro will check later")
            return
        channel = getattr(s, "update_channel", "stable")
        self._set(state="checking", detail="")
        local = state.read_local()
        try:
            raw = net.fetch_feed(FEED_URL.format(channel=channel), feed.MAX_BYTES)
            manifest = feed.open_envelope(raw, revoked_keys=set(local.get("revoked_keys", [])))
        except feed.FeedError as exc:
            log.warning("update feed rejected: %s", exc)
            self._set(state="error", detail="The update information couldn't be verified, so it was ignored")
            return
        except Exception as exc:  # noqa: BLE001  (network: offline, DNS, TLS, HTTP errors)
            log.info("update check: %s", exc)
            self._set(state="offline", detail="Couldn't reach the update server")
            return
        root = state.install_root()
        inst = state.read_install(root) if root else {"bad": []}
        decision = feed.decide(manifest, channel=channel, current=__version__, platform=net.platform_key(),
                               os_version=net.os_version(), install_id=local["install_id"],
                               highest_serial=int(local.get("highest_serial", 0)),
                               bad_versions=set(inst.get("bad", [])), manual=manual)
        now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        if decision.reason not in ("malformed manifest", "wrong channel", "older than a manifest already seen"):
            local["highest_serial"] = max(int(local.get("highest_serial", 0)), decision.serial)
            local["revoked_keys"] = sorted(set(local.get("revoked_keys", [])) | set(manifest.get("revoked_keys", [])))
        local["last_check"] = now
        local["last_result"] = decision.reason
        state.write_local(local)
        log.info("update check (%s, %s): %s", channel, "manual" if manual else "automatic", decision.reason)
        if decision.revoked_current:
            self._withdrawn(root)
            return
        offer = decision.offer
        if offer is None:
            self._set(state="up-to-date" if decision.reason in ("up to date", "rollout") else "idle",
                      detail=decision.reason, last_check=now, version="")
            return
        info = {"version": offer.version, "summary": offer.summary, "notes_url": offer.notes_url,
                "critical": offer.critical, "last_check": now}
        if root is None or offer.inventory is None:
            # a portable or development copy, or a platform without in-place updates (Mac until it is signed):
            # never patched in place; point to the download instead
            self._set(state="available", detail="Download the new version from the website", **info)
            return
        self._download(offer, root, inst, info)

    def _download(self, offer: feed.Offer, root, inst: dict, info: dict) -> None:
        current_dir = state.version_dir(root, __version__)
        downloads = state.downloads_dir()
        while not self._stop.is_set():
            if not self._wait_idle():
                return
            self._pause.clear()
            try:
                stage.stage(offer, root, current_dir, downloads, have_gpu=self.has_gpu_runtime(), cancel=self._pause,
                            status=lambda d: self._set(state="downloading", detail=d, **info))
                break
            except stage.StageError as exc:
                log.warning("update %s could not be prepared: %s", offer.version, exc)
                self._set(state="error", detail=str(exc), **info)
                return
            except Exception as exc:  # noqa: BLE001
                from tiro.models import DownloadCancelled

                if isinstance(exc, DownloadCancelled):
                    log.info("update download paused (dictation)")
                    continue  # resumes from the partial download once you've finished
                log.warning("update %s download failed: %s", offer.version, exc)
                self._set(state="error", detail="The download didn't finish; Tiro will try again later", **info)
                return
        else:
            return
        if getattr(self.settings(), "install_on_quit", True):
            state.set_pending(root, offer.version)
        stage.cleanup(root, {v for v in (inst.get("current"), inst.get("previous"), offer.version) if v})
        self._set(state="ready", detail="Restart Tiro to finish updating", **info)

    def _withdrawn(self, root) -> None:
        """The running version was pulled by the publisher: go back to the previous one at the next start."""
        if root is None:
            self._set(state="error", detail=f"Tiro {__version__} was withdrawn; please reinstall from the website")
            return
        inst = state.read_install(root)
        prev = inst.get("previous")
        if prev and (state.version_dir(root, prev) / "Tiro.exe").is_file():
            state.mark_bad(root, __version__)
            state.set_pending(root, prev)
            self._set(state="ready", version=prev, critical=True,
                      detail=f"Tiro {__version__} was withdrawn. Restart to go back to {prev}.")
        else:
            self._set(state="error", detail=f"Tiro {__version__} was withdrawn; please reinstall from the website")

    # ------------------------------------------------------------------ actions from the UI
    def accept(self, version: str) -> bool:
        """'Restart to update' (or install on quit): make sure the staged version is next."""
        root = state.install_root()
        if root is None or not (state.version_dir(root, version) / "Tiro.exe").is_file():
            return False
        state.set_pending(root, version)
        return True

    def rollback_target(self) -> str | None:
        root = state.install_root()
        if root is None:
            return None
        prev = state.read_install(root).get("previous")
        return prev if prev and (state.version_dir(root, prev) / "Tiro.exe").is_file() else None

    def roll_back(self) -> str | None:
        """Settings > Updates > 'Go back to version X': runs at the next start (the only allowed downgrade)."""
        root = state.install_root()
        prev = self.rollback_target()
        if root is None or prev is None:
            return None
        state.mark_bad(root, __version__)
        state.set_pending(root, prev)
        return prev
