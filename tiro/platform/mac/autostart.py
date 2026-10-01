"""'Launch at login' on macOS: SMAppService (macOS 13+) for the installed app, a LaunchAgent otherwise."""

from __future__ import annotations

import logging
import plistlib
import sys
from pathlib import Path

from tiro.paths import FROZEN, app_root

log = logging.getLogger(__name__)
AGENT = Path.home() / "Library" / "LaunchAgents" / "app.tiro.dictation.plist"


def _service():
    if not FROZEN:
        return None
    try:
        from ServiceManagement import SMAppService

        return SMAppService.mainAppService()
    except Exception:
        return None


def _command() -> list[str]:
    if FROZEN:
        return [sys.executable, "--autostart"]
    return [sys.executable, str(app_root() / "run_tiro.pyw"), "--autostart"]


def is_enabled() -> bool:
    svc = _service()
    if svc is not None:
        return int(svc.status()) in (1, 2)  # enabled, or waiting for approval in System Settings
    return AGENT.is_file()


def set_enabled(on: bool) -> bool:
    svc = _service()
    if svc is not None:
        try:
            ok, err = svc.registerAndReturnError_(None) if on else svc.unregisterAndReturnError_(None)
            if not ok:
                log.warning("login item change failed: %s", err)
            return bool(ok)
        except Exception:
            log.exception("SMAppService failed; using a LaunchAgent")
    try:
        if on:
            AGENT.parent.mkdir(parents=True, exist_ok=True)
            AGENT.write_bytes(plistlib.dumps({"Label": "app.tiro.dictation", "ProgramArguments": _command(),
                                              "RunAtLoad": True, "ProcessType": "Interactive"}))
        elif AGENT.exists():
            AGENT.unlink()
        return True
    except OSError:
        log.exception("could not update the LaunchAgent")
        return False


def refresh_path() -> None:
    if AGENT.is_file():
        try:
            data = plistlib.loads(AGENT.read_bytes())
            if data.get("ProgramArguments") != _command():
                set_enabled(True)
        except Exception:
            pass
