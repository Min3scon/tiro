"""Filesystem locations for bundled resources, models, settings and logs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from tiro import APP_NAME

FROZEN = bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Folder that contains Tiro.exe (frozen; on a Mac, Tiro.app/Contents/MacOS) or the project checkout."""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundle_dir() -> Path:
    """Folder with read-only bundled files (PyInstaller's _internal when frozen)."""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", app_root()))
    return app_root()


def assets_dir() -> Path:
    return bundle_dir() / "assets"


def asset(*parts: str) -> Path:
    return assets_dir().joinpath(*parts)


def _env_dir(var: str, fallback: Path) -> Path:
    base = os.environ.get(var)
    return Path(base) if base else fallback


IS_MAC = sys.platform == "darwin"
MAC_SUPPORT = Path.home() / "Library" / "Application Support" / APP_NAME


def config_dir() -> Path:
    override = os.environ.get("TIRO_CONFIG_DIR")  # isolated settings for tests
    if override:
        path = Path(override)
    elif IS_MAC:
        path = MAC_SUPPORT
    else:
        path = _env_dir("APPDATA", Path.home() / "AppData" / "Roaming") / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def instance_key() -> str:
    """Distinguishes the single-instance lock of a test profile from the real one."""
    override = os.environ.get("TIRO_CONFIG_DIR")
    if not override:
        return ""
    import hashlib

    return "-" + hashlib.sha1(override.lower().encode()).hexdigest()[:8]


def data_dir() -> Path:
    path = MAC_SUPPORT if IS_MAC else _env_dir("LOCALAPPDATA", Path.home() / "AppData" / "Local") / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def history_path() -> Path:
    """Your dictation history and learned fixes (SQLite). Test profiles keep their own."""
    override = os.environ.get("TIRO_CONFIG_DIR")
    folder = Path(override) / "data" if override else data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "history.db"


def log_dir() -> Path:
    path = Path.home() / "Library" / "Logs" / APP_NAME if IS_MAC else data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def install_root() -> Path | None:
    """The versioned install this copy runs from (Programs\\Tiro with state.json and app-X.Y.Z folders), or None
    for a portable or development copy. See tiro.update.state."""
    override = os.environ.get("TIRO_INSTALL_ROOT")  # tests
    if override:
        return Path(override)
    if not FROZEN:
        return None
    here = app_root()
    if here.name.startswith("app-") and (here.parent / "state.json").is_file():
        return here.parent
    return None


def model_search_dirs() -> list[Path]:
    """Model folders in priority order: the install's shared models folder (every version uses the same models),
    next to the exe (older flat installs), then the per-user data folder."""
    dirs = [data_dir() / "models"]
    if not (IS_MAC and FROZEN):  # never inside the signed .app bundle
        dirs.insert(0, app_root() / "models")
        root = install_root()
        if root is not None:
            dirs.insert(0, root / "models")
    return dirs


def user_models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path
