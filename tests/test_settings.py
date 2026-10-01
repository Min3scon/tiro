"""Settings round-trip: keys this version doesn't know (Tiro Lite's) survive a save."""
import json

from tiro.config import Settings


def test_unknown_keys_survive_save(tmp_path, monkeypatch):
    monkeypatch.setenv("TIRO_CONFIG_DIR", str(tmp_path))
    path = Settings.path()
    path.write_text(json.dumps({"hotkey": "rctrl", "lite": {"resource_use": "light"}, "sounds": False}),
                    encoding="utf-8")
    s = Settings.load()
    assert s.sounds is False
    s.sounds = True
    s.save()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["lite"] == {"resource_use": "light"}
    assert data["sounds"] is True


def test_known_keys_win_over_stale_extras(tmp_path, monkeypatch):
    monkeypatch.setenv("TIRO_CONFIG_DIR", str(tmp_path))
    Settings.path().write_text(json.dumps({"mode": "toggle"}), encoding="utf-8")
    s = Settings.load()
    s.mode = "hold"
    s.save()
    assert json.loads(Settings.path().read_text(encoding="utf-8"))["mode"] == "hold"
