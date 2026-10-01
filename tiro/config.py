"""User settings, persisted as JSON in %APPDATA%\\Tiro\\settings.json."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass, field, fields

from tiro.keys import HotkeySpec
from tiro.models import DEFAULT_MODEL, MODELS
from tiro.paths import config_dir

log = logging.getLogger(__name__)

_CHOICES = {
    "mode": ("hold", "toggle"),
    "device": ("auto", "cuda", "cpu"),
    "insertion": ("type", "paste"),
    "overlay_position": ("bottom", "top"),
    "correction_mode": ("strict", "balanced", "aggressive"),
    "ai_model": ("auto", "small", "tiny"),
    "update_channel": ("stable", "beta"),
}
_RANGES = {"correction_threshold": (0.0, 0.99), "correction_budget_ms": (40, 1000), "hands_free_pause_sec": (0.5, 10.0)}


DEFAULT_HOTKEY = "ralt" if sys.platform == "darwin" else "rctrl"  # Right Option on a Mac, Right Ctrl on Windows

# Safe mode (hold Shift while starting Tiro, or automatic after two failed starts): plain dictation only.
SAFE_MODE = {"correction": False, "ai_correction": False, "history": False, "learn_corrections": False,
             "instant_start": False, "device": "cpu"}


@dataclass
class Settings:
    hotkey: str = DEFAULT_HOTKEY
    mode: str = "hold"
    double_tap_lock: bool = True
    microphone: str | None = None  # None = the system's default input
    model: str = DEFAULT_MODEL
    device: str = "auto"
    insertion: str = "type"
    remove_fillers: bool = True
    voice_commands: bool = True
    smart_spacing: bool = True
    sounds: bool = True
    show_live_text: bool = True
    overlay_position: str = "bottom"
    hands_free_pause_sec: float = 2.0
    welcome_shown: bool = False
    setup_done: bool = False  # first-run setup wizard finished (or skipped)
    dictionary: list[str] = field(default_factory=list)  # words/names, or "heard -> written" rules
    learn_corrections: bool = True  # learn from words the user retypes right after dictation
    instant_start: bool = False  # keep the microphone open so the first syllable is never clipped
    # correction pass (tiro.correct)
    correction: bool = True  # fix likely-misheard words using your dictionary, fixes and history
    correction_mode: str = "balanced"  # strict | balanced | aggressive
    correction_threshold: float = 0.0  # word confidence below which words are examined; 0 = the mode's default
    ai_correction: bool = True  # tier 2: a small local language model for spans the lexicon can't settle
    ai_model: str = "auto"  # auto | small (Qwen2.5 1.5B) | tiny (Qwen2.5 0.5B)
    correction_budget_ms: int = 150  # never hold text back longer than this for a correction
    late_fixes: bool = True  # if a correction arrives late, swap the word in place when that's safe
    history: bool = True  # learn your vocabulary from what you dictate (stored only on this computer)
    fix_hotkey: str = "ctrl+alt+f"  # open "Fix last transcription"
    show_latency: bool = False  # debug panel in settings
    # updates (tiro.update)
    auto_update_check: bool = True  # look for updates now and then; off = no network unless you click Check now
    install_on_quit: bool = True  # a downloaded update is installed the next time Tiro starts
    update_channel: str = "stable"  # stable | beta
    update_on_metered: bool = False  # also check and download on a metered connection
    whats_new_seen: str = ""  # the version whose "What's new" was last shown

    @property
    def hotkey_spec(self) -> HotkeySpec:
        try:
            return HotkeySpec.parse(self.hotkey)
        except ValueError:
            return HotkeySpec.parse(DEFAULT_HOTKEY)

    @classmethod
    def path(cls):
        return config_dir() / "settings.json"

    @classmethod
    def load(cls) -> Settings:
        s = cls()
        try:
            raw = json.loads(cls.path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return s
        except Exception:
            log.exception("settings file unreadable; using defaults")
            return s
        if isinstance(raw, dict) and "setup_done" not in raw and raw.get("welcome_shown"):
            raw["setup_done"] = True  # upgrading from 1.x: already set up
        types = {f.name: f.type for f in fields(cls)}
        # keys this version doesn't know (e.g. Tiro Lite's settings in the same file) are kept and saved back
        s._extra = {k: v for k, v in raw.items() if k not in types} if isinstance(raw, dict) else {}
        for key, value in raw.items():
            if key not in types:
                continue
            default = getattr(s, key)
            if key in _CHOICES and value not in _CHOICES[key]:
                continue
            if key == "model" and value not in MODELS:
                continue
            if key in ("hotkey", "fix_hotkey"):
                try:
                    HotkeySpec.parse(value)
                except (ValueError, AttributeError):
                    continue
            if isinstance(default, bool) and not isinstance(value, bool):
                continue
            if isinstance(default, float) and (isinstance(value, bool) or not isinstance(value, int | float)):
                continue
            if isinstance(default, int) and not isinstance(default, bool) and (
                    isinstance(value, bool) or not isinstance(value, int)):
                continue
            if key in _RANGES:
                lo, hi = _RANGES[key]
                value = min(hi, max(lo, value))
            if isinstance(default, list):
                if not isinstance(value, list):
                    continue
                value = [str(v).strip() for v in value if str(v).strip()][:2000]
            setattr(s, key, value)
        return s

    def apply_safe_mode(self) -> None:
        """Safe mode: optional features off for this run only (your saved settings don't change)."""
        self._safe = {k: getattr(self, k) for k in SAFE_MODE}
        for k, v in SAFE_MODE.items():
            setattr(self, k, v)

    def save(self) -> None:
        try:
            tmp = self.path().with_suffix(".tmp")
            # in safe mode the saved values stay what they were, unless you changed them yourself since
            data = {**getattr(self, "_extra", {}), **asdict(self), **getattr(self, "_safe", {})}
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.path())
        except Exception:
            log.exception("could not save settings")
