"""'Start with Windows' via the per-user Run key (no admin rights needed)."""

from __future__ import annotations

import logging
import sys
import winreg
from pathlib import Path

from tiro import APP_NAME
from tiro.paths import FROZEN, app_root

log = logging.getLogger(__name__)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def command() -> str:
    if FROZEN:
        return f'"{sys.executable}" --autostart'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{app_root() / "run_tiro.pyw"}" --autostart'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except OSError:
        return False


def set_enabled(on: bool) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        log.exception("could not update the Run key")
        return False


def refresh_path() -> None:
    """If autostart is on but points at an old location (the app folder moved), fix it."""
    if not is_enabled():
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
        if value != command():
            set_enabled(True)
    except OSError:
        pass
