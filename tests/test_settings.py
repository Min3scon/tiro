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


def test_safe_mode_is_never_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("TIRO_CONFIG_DIR", str(tmp_path))
    Settings.path().write_text(json.dumps({"device": "auto", "ai_correction": True, "hotkey": "lshift"}),
                               encoding="utf-8")
    s = Settings.load()
    s.apply_safe_mode()
    assert s.device == "cpu" and s.ai_correction is False and s.correction is False
    s.sounds = False  # an ordinary change made while in safe mode is saved
    s.save()
    data = json.loads(Settings.path().read_text(encoding="utf-8"))
    assert data["device"] == "auto" and data["ai_correction"] is True and data["hotkey"] == "lshift"
    assert data["sounds"] is False


def test_v1_settings_are_already_set_up(tmp_path, monkeypatch):
    # Tiro 1.x wrote "welcome_shown" but no "setup_done": an upgrade must not send anyone back through setup
    monkeypatch.setenv("TIRO_CONFIG_DIR", str(tmp_path))
    Settings.path().write_text(json.dumps({"hotkey": "lshift", "welcome_shown": True}), encoding="utf-8")
    s = Settings.load()
    assert s.setup_done is True and s.hotkey == "lshift" and s.auto_update_check is True
