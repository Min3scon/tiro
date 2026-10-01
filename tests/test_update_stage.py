"""Staging an update side by side: reuse unchanged files, fetch only changed packs, verify every file."""
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from tiro.update import stage as st
from tiro.update.feed import Asset, Offer


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write_tree(folder: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def inventory(version: str, files: dict[str, bytes], packs: dict[str, str]) -> dict:
    return {"version": version, "files": [{"path": p, "size": len(d), "sha256": sha(d), "pack": packs[p]}
                                          for p, d in files.items()]}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A running 1.0.0 install and a 1.1.0 release whose packs live in a fake 'server' folder."""
    root = tmp_path / "Programs" / "Tiro"
    server = tmp_path / "server"
    server.mkdir()
    old = {"Tiro.exe": b"exe-1.0.0", "_internal/base_library.zip": b"lib-1", "_internal/qt.dll": b"qt" * 1000,
           "_internal/cuda/cudnn.dll": b"cuda" * 2000}
    new = {"Tiro.exe": b"exe-1.1.0", "_internal/base_library.zip": b"lib-1", "_internal/qt.dll": b"qt" * 1000,
           "_internal/cuda/cudnn.dll": b"cuda" * 2000, "_internal/assets/new.txt": b"hello"}
    packs = {"Tiro.exe": "core", "_internal/base_library.zip": "core", "_internal/assets/new.txt": "core",
             "_internal/qt.dll": "runtime", "_internal/cuda/cudnn.dll": "gpu"}
    cur = root / "app-1.0.0"
    write_tree(cur, old)
    (cur / ".tiro").mkdir()
    (cur / ".tiro" / "files.json").write_text(json.dumps(inventory("1.0.0", old, packs)))
    assets = {}
    for pack in ("core", "runtime", "gpu"):
        zp = server / f"{pack}.zip"
        with zipfile.ZipFile(zp, "w") as z:
            for rel, data in new.items():
                if packs[rel] == pack:
                    z.writestr(rel, data)
        assets[pack] = Asset(f"https://example.invalid/{pack}.zip", zp.stat().st_size, sha(zp.read_bytes()))
    inv = json.dumps(inventory("1.1.0", new, packs)).encode()
    (server / "files.json").write_bytes(inv)
    offer = Offer("1.1.0", "", "", False, Asset("https://example.invalid/files.json", len(inv), sha(inv)), assets)
    fetched = []

    def fake_download(url, dest, size, sha256, progress=None, cancel=None, retries=4):
        name = url.rsplit("/", 1)[1]
        fetched.append(name)
        data = (server / name).read_bytes()
        assert len(data) == size and sha(data) == sha256
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    monkeypatch.setattr("tiro.models.download_file", fake_download)
    return root, cur, offer, new, fetched, server


def test_stages_only_what_changed(rig, tmp_path):
    root, cur, offer, new, fetched, _ = rig
    out = st.stage(offer, root, cur, tmp_path / "dl", have_gpu=True, health=False)
    assert out == root / "app-1.1.0"
    for rel, data in new.items():
        assert (out / rel).read_bytes() == data
    assert sorted(fetched) == ["core.zip", "files.json"]  # the runtime and GPU packs were not needed
    assert (cur / "Tiro.exe").read_bytes() == b"exe-1.0.0"  # the running version is untouched
    assert st.read_inventory(out)["version"] == "1.1.0"


def test_cpu_only_copy_skips_gpu_files(rig, tmp_path):
    root, cur, offer, _new, _f, _ = rig
    (cur / "_internal" / "cuda" / "cudnn.dll").unlink()
    out = st.stage(offer, root, cur, tmp_path / "dl", have_gpu=False, health=False)
    assert not (out / "_internal" / "cuda").exists()


def test_fresh_layout_without_inventory_downloads_everything(rig, tmp_path):
    root, cur, offer, new, fetched, _ = rig
    (cur / ".tiro" / "files.json").unlink()
    out = st.stage(offer, root, cur, tmp_path / "dl", have_gpu=True, health=False)
    assert sorted(fetched) == ["core.zip", "files.json", "gpu.zip", "runtime.zip"]
    assert (out / "_internal" / "qt.dll").read_bytes() == new["_internal/qt.dll"]


def test_corrupted_local_file_is_caught(rig, tmp_path):
    root, cur, offer, _new, _f, _ = rig
    (cur / "_internal" / "qt.dll").write_bytes(b"qt" * 999 + b"XX")  # same size, different content
    with pytest.raises(st.StageError):
        st.stage(offer, root, cur, tmp_path / "dl", have_gpu=True, health=False)
    assert not (root / "app-1.1.0").exists()


def test_tampered_pack_is_refused(rig, tmp_path):
    root, cur, offer, _new, _f, server = rig
    with zipfile.ZipFile(server / "core.zip", "a") as z:
        z.writestr("evil.dll", b"x")
    from tiro.models import DownloadCancelled  # noqa: F401  (the fake download checks size + SHA like the real one)

    with pytest.raises(AssertionError):
        st.stage(offer, root, cur, tmp_path / "dl", have_gpu=True, health=False)
    assert not (root / "app-1.1.0").exists()


def test_unsafe_paths_are_refused(tmp_path):
    for bad in ("../x", "/etc/passwd", "C:/Windows/x", "a//b", "a/../../b"):
        with pytest.raises(st.StageError):
            st._safe_target(tmp_path, bad)


def test_cleanup_keeps_current_previous_and_pending(tmp_path):
    for v in ("1.0.0", "1.1.0", "1.2.0", "0.9.0", "1.3.0.partial"):
        (tmp_path / f"app-{v}").mkdir()
    st.cleanup(tmp_path, {"1.1.0", "1.0.0", "1.2.0"})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["app-1.0.0", "app-1.1.0", "app-1.2.0"]
