"""The background update service end to end (network faked): check, verify, stage, mark pending, withdraw."""
import base64
import hashlib
import json
import time
import zipfile
from types import SimpleNamespace

import pytest

from tiro import __version__
from tiro.update import ed25519, feed, net, service, state

SEED = bytes(range(32))
NEW = "99.0.0"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    # a Mac keeps app data in ~/Library, not LOCALAPPDATA: the updater's own state goes to the test folder too
    monkeypatch.setattr(state, "data_dir", lambda: tmp_path / "local" / "Tiro")
    root = tmp_path / "Programs" / "Tiro"
    monkeypatch.setenv("TIRO_INSTALL_ROOT", str(root))
    monkeypatch.setattr(feed, "TRUSTED_KEYS", {"ci": base64.b64encode(ed25519.public_key(SEED)).decode()})
    # the running version, laid out like an install
    cur = root / f"app-{__version__}"
    files_old = {"Tiro.exe": b"old exe", "_internal/lib.dll": b"lib" * 100}
    files_new = {"Tiro.exe": b"new exe", "_internal/lib.dll": b"lib" * 100}
    packs = {"Tiro.exe": "core", "_internal/lib.dll": "runtime"}

    def inv(version, files):
        return {"version": version, "files": [{"path": p, "size": len(d), "sha256": sha(d), "pack": packs[p]}
                                              for p, d in files.items()]}

    for rel, data in files_old.items():
        (cur / rel).parent.mkdir(parents=True, exist_ok=True)
        (cur / rel).write_bytes(data)
    (cur / ".tiro").mkdir()
    (cur / ".tiro" / "files.json").write_text(json.dumps(inv(__version__, files_old)))
    state.write_install(root, {"format": 1, "current": __version__, "previous": None, "pending": None,
                               "trial": None, "bad": []})
    server = {}
    core = tmp_path / "core.zip"
    with zipfile.ZipFile(core, "w") as z:
        z.writestr("Tiro.exe", files_new["Tiro.exe"])
    server["core.zip"] = core.read_bytes()
    server["files.json"] = json.dumps(inv(NEW, files_new)).encode()

    def asset(name):
        return {"url": f"https://github.com/x/{name}", "size": len(server[name]), "sha256": sha(server[name])}

    def manifest(version=NEW, serial=10, **kw):
        m = {"product": "Tiro", "channel": "stable", "serial": serial, "issued_at": "2026-10-01T00:00:00Z",
             "expires_at": "2099-01-01T00:00:00Z", "revoked_versions": [],
             "platforms": {net.platform_key(): {"version": version, "rollout_percent": 100,
                                                "summary": "Faster.", "inventory": asset("files.json"),
                                                "packs": {"core": asset("core.zip")}}}}
        m.update(kw)
        return m

    feeds = {"raw": feed.seal(manifest(), SEED, "ci")}
    monkeypatch.setattr(net, "fetch_feed", lambda url, max_bytes: feeds["raw"])
    monkeypatch.setattr(net, "connection", lambda: (True, False))

    def fake_download(url, dest, size, sha256, progress=None, cancel=None, retries=4):
        data = server[url.rsplit("/", 1)[1]]
        assert len(data) == size and sha(data) == sha256
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    monkeypatch.setattr("tiro.models.download_file", fake_download)
    monkeypatch.setattr("tiro.update.stage.check_health", lambda folder, version: None)
    settings = SimpleNamespace(auto_update_check=True, update_channel="stable", update_on_metered=False,
                               install_on_quit=True)
    statuses = []
    svc = service.UpdateService(settings=lambda: settings, busy=lambda: False, on_status=statuses.append)
    return SimpleNamespace(root=root, svc=svc, statuses=statuses, feeds=feeds, manifest=manifest,
                           settings=settings, files_new=files_new)


def test_update_is_staged_and_queued_for_next_start(rig):
    rig.svc.check()
    assert rig.statuses[-1].state == "ready" and rig.statuses[-1].version == NEW
    new_dir = rig.root / f"app-{NEW}"
    assert (new_dir / "Tiro.exe").read_bytes() == rig.files_new["Tiro.exe"]
    assert state.read_install(rig.root)["pending"] == NEW
    assert state.read_local()["highest_serial"] == 10


def test_later_means_not_queued_when_install_on_quit_is_off(rig):
    rig.settings.install_on_quit = False
    rig.svc.check()
    assert rig.statuses[-1].state == "ready"
    assert state.read_install(rig.root)["pending"] is None
    assert rig.svc.accept(NEW) and state.read_install(rig.root)["pending"] == NEW


def test_up_to_date(rig):
    rig.feeds["raw"] = feed.seal(rig.manifest(version=__version__), SEED, "ci")
    rig.svc.check()
    assert rig.statuses[-1].state == "up-to-date"
    assert not (rig.root / f"app-{NEW}").exists()


def test_bad_signature_changes_nothing(rig):
    rig.feeds["raw"] = feed.seal(rig.manifest(), b"\x09" * 32, "ci")
    rig.svc.check()
    assert rig.statuses[-1].state == "error"
    assert not (rig.root / f"app-{NEW}").exists()
    assert state.read_local().get("highest_serial", 0) == 0


def test_replayed_old_manifest_is_ignored(rig):
    rig.svc.check()
    rig.feeds["raw"] = feed.seal(rig.manifest(serial=5), SEED, "ci")
    rig.statuses.clear()
    rig.svc.check()
    assert rig.statuses[-1].state != "ready"
    assert state.read_local()["highest_serial"] == 10


def test_metered_and_offline(rig, monkeypatch):
    monkeypatch.setattr(net, "connection", lambda: (True, True))
    rig.svc.check()
    assert rig.statuses[-1].state == "skipped"
    rig.svc.check(manual=True)  # "Check now" still works on a metered connection
    assert rig.statuses[-1].state == "ready"
    monkeypatch.setattr(net, "connection", lambda: (False, None))
    rig.svc.check(manual=True)
    assert rig.statuses[-1].state == "offline"


def test_withdrawn_version_goes_back(rig):
    (rig.root / "app-1.0.0").mkdir()
    (rig.root / "app-1.0.0" / "Tiro.exe").write_bytes(b"old")
    s = state.read_install(rig.root)
    s["previous"] = "1.0.0"
    state.write_install(rig.root, s)
    rig.feeds["raw"] = feed.seal(rig.manifest(version="1.0.0", revoked_versions=[__version__]), SEED, "ci")
    rig.svc.check()
    s = state.read_install(rig.root)
    assert s["pending"] == "1.0.0" and __version__ in s["bad"]
    assert rig.statuses[-1].critical


def test_manual_roll_back(rig):
    assert rig.svc.roll_back() is None  # nothing to go back to
    (rig.root / "app-1.0.0").mkdir()
    (rig.root / "app-1.0.0" / "Tiro.exe").write_bytes(b"old")
    s = state.read_install(rig.root)
    s["previous"] = "1.0.0"
    state.write_install(rig.root, s)
    assert rig.svc.roll_back() == "1.0.0"
    assert state.read_install(rig.root)["pending"] == "1.0.0"


def test_portable_copy_is_only_told(rig, monkeypatch):
    monkeypatch.delenv("TIRO_INSTALL_ROOT")
    rig.svc.check()
    assert rig.statuses[-1].state == "available"
    assert not (rig.root / f"app-{NEW}").exists()


def test_user_agent_has_no_identifiers():
    ua = net.user_agent()
    assert ua.startswith(f"Tiro/{__version__} (") and ua.count(";") == 1


def test_platform_without_in_place_updates_is_only_told(rig):
    m = rig.manifest()
    entry = m["platforms"][net.platform_key()]
    del entry["inventory"]
    entry["packs"] = {}
    rig.feeds["raw"] = feed.seal(m, SEED, "ci")
    rig.svc.check()
    assert rig.statuses[-1].state == "available" and rig.statuses[-1].version == NEW
    assert not (rig.root / f"app-{NEW}").exists()


def test_automatic_checks_off_never_go_online(rig, monkeypatch):
    calls = []
    monkeypatch.setattr(net, "fetch_feed", lambda url, max_bytes: calls.append(url) or rig.feeds["raw"])
    monkeypatch.setattr(service, "FIRST_CHECK_SEC", 0.01)
    monkeypatch.setattr(service, "INTERVAL_SEC", 0.02)
    monkeypatch.setattr(service, "JITTER_SEC", 0)
    rig.settings.auto_update_check = False
    rig.svc.start()
    try:
        time.sleep(0.3)  # about 15 automatic rounds
        assert calls == []
        rig.svc.check_now()  # "Check now" still checks
        deadline = time.time() + 5
        while not calls and time.time() < deadline:
            time.sleep(0.02)
        assert len(calls) == 1
    finally:
        rig.svc.stop()
