"""Build a new version next to the running one, verify every file, and check that it starts.

1. Download the new version's file inventory (path, size and SHA-256 of every file; checked against the signed
   feed).
2. Every file the running version already has (same SHA-256) is hard-linked into the new folder: no download and
   almost no disk. Only the packs holding changed files are downloaded (with resume, checked against the feed).
3. Every file of the new folder is checked against the inventory; anything extra or different stops the update.
4. The new Tiro.exe runs a health check (imports, ONNX Runtime, Qt, assets) before anything is switched.

Nothing here touches the running version. A failure leaves an "app-X.Y.Z.partial" folder that the next attempt
replaces.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path

from tiro.update.feed import Asset, Offer
from tiro.update.state import version_dir

log = logging.getLogger(__name__)

META = ".tiro"
INVENTORY = "files.json"
HEALTH_TIMEOUT = 120


class StageError(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_inventory(app_dir: Path) -> dict | None:
    try:
        inv = json.loads((app_dir / META / INVENTORY).read_text(encoding="utf-8"))
        return inv if isinstance(inv, dict) and isinstance(inv.get("files"), list) else None
    except (OSError, ValueError):
        return None


def _safe_target(root: Path, rel: str) -> Path:
    """A path inside root for an inventory/zip entry (no absolute paths, no '..')."""
    rel = rel.replace("\\", "/")
    if rel.startswith("/") or ":" in rel or any(p in ("", "..") for p in rel.split("/")):
        raise StageError(f"unsafe path in update: {rel!r}")
    return root.joinpath(*rel.split("/"))


def _fetch(asset: Asset, dest: Path, cancel: threading.Event | None,
           progress: Callable[[int], None] | None) -> Path:
    from tiro.models import download_file

    if dest.is_file() and dest.stat().st_size == asset.size and sha256_file(dest) == asset.sha256:
        return dest
    download_file(asset.url, dest, asset.size, asset.sha256, progress=progress, cancel=cancel)
    return dest


def plan(inventory: dict, current: dict | None, current_dir: Path | None, have_gpu: bool):
    """(files to take from the running version, files to extract from packs, packs needed)."""
    have: dict[str, str] = {}
    if current and current_dir:
        for f in current.get("files", []):
            have.setdefault(str(f["sha256"]).lower(), str(f["path"]))
    link, extract, packs = [], [], set()
    for f in inventory["files"]:
        if f.get("pack") == "gpu" and not have_gpu:
            continue
        src = have.get(str(f["sha256"]).lower())
        if src is not None:
            link.append((f, src))
        else:
            extract.append(f)
            packs.add(str(f["pack"]))
    return link, extract, packs


def stage(offer: Offer, root: Path, current_dir: Path | None, downloads: Path, *, have_gpu: bool,
          cancel: threading.Event | None = None, status: Callable[[str], None] = lambda s: None,
          health: bool = True) -> Path:
    """Prepare app-<version> under root and return it (or raise StageError / DownloadCancelled)."""
    version = offer.version
    if offer.inventory is None:
        raise StageError("this release has no in-place update for this computer")
    final = version_dir(root, version)
    if final.is_dir() and read_inventory(final) is not None:
        return final  # already staged earlier
    status("Checking what changed")
    inv_path = _fetch(offer.inventory, downloads / f"{offer.inventory.sha256}.json", cancel, None)
    inventory = json.loads(inv_path.read_text(encoding="utf-8"))
    if inventory.get("version") != version:
        raise StageError("the inventory is for another version")
    current = read_inventory(current_dir) if current_dir else None
    link, extract, packs = plan(inventory, current, current_dir, have_gpu)
    missing = packs - set(offer.packs)
    if missing:
        raise StageError(f"the feed lacks packs {sorted(missing)}")
    need = sum(offer.packs[p].size for p in packs) + sum(int(f["size"]) for f in extract)
    free = shutil.disk_usage(root).free
    if free < need + (500 << 20):
        raise StageError(f"not enough free disk space ({free >> 20} MB free, {need >> 20} MB needed)")

    partial = root / f"app-{version}.partial"
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    # 1. unchanged files from the running version
    for f, src in link:
        target = _safe_target(partial, f["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        source = _safe_target(current_dir, src)
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
    # 2. changed files from the packs
    wanted: dict[str, list[dict]] = {}
    for f in extract:
        wanted.setdefault(str(f["pack"]), []).append(f)
    for i, name in enumerate(sorted(wanted), 1):
        asset = offer.packs[name]
        status(f"Downloading update ({i} of {len(wanted)})")
        zpath = _fetch(asset, downloads / f"{asset.sha256}.zip", cancel, None)
        with zipfile.ZipFile(zpath) as z:
            names = set(z.namelist())
            for f in wanted[name]:
                member = str(f["path"]).replace("\\", "/")
                if member not in names:
                    raise StageError(f"{member} is missing from the {name} pack")
                target = _safe_target(partial, member)
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
    # 3. verify everything
    status("Checking the new version")
    verify(partial, inventory, have_gpu)
    (partial / META).mkdir(exist_ok=True)
    (partial / META / INVENTORY).write_text(json.dumps(inventory), encoding="utf-8")
    # 4. does it start?
    if health:
        status("Testing the new version")
        check_health(partial, version)
    if final.exists():
        shutil.rmtree(final)
    os.replace(partial, final)
    log.info("update %s staged in %s (%d files reused, %d new, packs %s)", version, final, len(link),
             len(extract), sorted(packs))
    return final


def verify(folder: Path, inventory: dict, have_gpu: bool) -> None:
    expected = {}
    for f in inventory["files"]:
        if f.get("pack") == "gpu" and not have_gpu:
            continue
        expected[str(f["path"]).replace("\\", "/")] = f
    seen = set()
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(folder).as_posix()
        if rel.startswith(META + "/"):
            continue
        f = expected.get(rel)
        if f is None:
            raise StageError(f"unexpected file in update: {rel}")
        if path.stat().st_size != int(f["size"]) or sha256_file(path) != str(f["sha256"]).lower():
            raise StageError(f"{rel} does not match the signed inventory")
        seen.add(rel)
    lost = set(expected) - seen
    if lost:
        raise StageError(f"{len(lost)} files missing, e.g. {min(lost)}")


def check_health(folder: Path, version: str) -> None:
    exe = folder / ("Tiro.exe" if sys.platform == "win32" else "Tiro")
    out = folder.parent / f"health-{version}.json"
    out.unlink(missing_ok=True)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        subprocess.run([str(exe), "--health", str(out)], timeout=HEALTH_TIMEOUT, cwd=str(folder),
                       creationflags=flags, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StageError(f"the new version did not pass its start-up check: {exc}") from exc
    try:
        result = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StageError("the new version did not report its start-up check") from exc
    finally:
        out.unlink(missing_ok=True)
    if not result.get("ok") or result.get("version") != version:
        raise StageError(f"the new version failed its start-up check: {result.get('error') or result}")


def cleanup(root: Path, keep: set[str], downloads: Path | None = None) -> None:
    """Remove versions that are neither current, previous nor pending, and leftover downloads."""
    for d in root.glob("app-*"):
        name = d.name[4:]
        if d.is_dir() and (name.endswith(".partial") or name not in keep):
            shutil.rmtree(d, ignore_errors=True)
    if downloads is not None:
        for f in downloads.glob("*"):
            if f.is_file():
                f.unlink(missing_ok=True)
