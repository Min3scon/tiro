"""Where updates live on disk, and the two small state files.

Installed layout (Windows, per user, no admin rights):

    %LOCALAPPDATA%\\Programs\\Tiro\\
      Tiro.exe          the launcher: starts the version state.json names, rolls back a version that fails
      state.json        {"current": "2.0.3", "previous": "2.0.2", "pending": null, "trial": null, "bad": []}
      app-2.0.3\\        a complete version (Tiro.exe, _internal\\, .tiro\\files.json)
      app-2.0.2\\        the previous one, kept for rolling back (unchanged files are hard links: almost no disk)
      models\\           speech models, shared by every version

    %LOCALAPPDATA%\\Tiro\\updates\\   update-state.json (install id, newest manifest seen), downloads\\

A copy that isn't in this layout (run from a folder, or from source) is "portable": it can tell you about a new
version and fetch the verified installer, but it never patches itself.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path

from tiro import paths
from tiro.paths import data_dir

log = logging.getLogger(__name__)

STATE = "state.json"


def install_root() -> Path | None:
    """The versioned install this copy runs from, or None for a portable or development copy."""
    return paths.install_root()


def version_dir(root: Path, version: str) -> Path:
    return root / f"app-{version}"


def _read_json(path: Path, default: dict) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(default)
    except (OSError, ValueError):
        return dict(default)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------------------ install state (shared with the launcher)
DEFAULT_INSTALL = {"format": 1, "current": None, "previous": None, "pending": None, "trial": None, "bad": []}


def read_install(root: Path) -> dict:
    s = _read_json(root / STATE, DEFAULT_INSTALL)
    for k, v in DEFAULT_INSTALL.items():
        s.setdefault(k, v if not isinstance(v, list) else [])
    return s


def write_install(root: Path, state: dict) -> None:
    _write_json(root / STATE, state)


def set_pending(root: Path, version: str | None) -> None:
    s = read_install(root)
    s["pending"] = version
    write_install(root, s)


def mark_bad(root: Path, version: str) -> None:
    s = read_install(root)
    if version not in s["bad"]:
        s["bad"].append(version)
    write_install(root, s)


# ------------------------------------------------------------------ this user's update state
def updates_dir() -> Path:
    path = data_dir() / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def downloads_dir() -> Path:
    path = updates_dir() / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_local() -> dict:
    s = _read_json(updates_dir() / "update-state.json", {})
    if not s.get("install_id"):
        # random, created on this device, never sent anywhere: only decides staged rollouts locally
        s["install_id"] = secrets.token_hex(16)
        write_local(s)
    s.setdefault("highest_serial", 0)
    s.setdefault("revoked_keys", [])
    return s


def write_local(s: dict) -> None:
    _write_json(updates_dir() / "update-state.json", s)
