"""Virtual-key names, hotkey specs and human-readable labels."""

from __future__ import annotations

import sys
from dataclasses import dataclass

IS_MAC = sys.platform == "darwin"

VK_BACK, VK_TAB, VK_RETURN, VK_SHIFT, VK_CONTROL, VK_MENU, VK_PAUSE, VK_CAPITAL = 0x08, 0x09, 0x0D, 0x10, 0x11, 0x12, 0x13, 0x14
VK_ESCAPE, VK_SPACE, VK_INSERT, VK_DELETE = 0x1B, 0x20, 0x2D, 0x2E
VK_LWIN, VK_RWIN, VK_APPS = 0x5B, 0x5C, 0x5D
VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU = 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5
VK_SCROLL, VK_NUMLOCK = 0x91, 0x90
VK_MASK = 0xE8  # unassigned key used to stop a lone Alt/Win release from opening menus

MODIFIER_VKS = {
    "ctrl": frozenset({VK_LCONTROL, VK_RCONTROL, VK_CONTROL}),
    "shift": frozenset({VK_LSHIFT, VK_RSHIFT, VK_SHIFT}),
    "alt": frozenset({VK_LMENU, VK_RMENU, VK_MENU}),
    "win": frozenset({VK_LWIN, VK_RWIN}),
}
ALL_MODIFIERS = frozenset().union(*MODIFIER_VKS.values())

_NAMED: dict[str, int] = {
    "lctrl": VK_LCONTROL, "rctrl": VK_RCONTROL, "lshift": VK_LSHIFT, "rshift": VK_RSHIFT,
    "lalt": VK_LMENU, "ralt": VK_RMENU, "lwin": VK_LWIN, "rwin": VK_RWIN,
    "space": VK_SPACE, "enter": VK_RETURN, "tab": VK_TAB, "backspace": VK_BACK, "esc": VK_ESCAPE,
    "capslock": VK_CAPITAL, "scrolllock": VK_SCROLL, "pause": VK_PAUSE, "numlock": VK_NUMLOCK,
    "insert": VK_INSERT, "delete": VK_DELETE, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "menu": VK_APPS, "printscreen": 0x2C,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0, "[": 0xDB, "\\": 0xDC,
    "]": 0xDD, "'": 0xDE,
}
_NAMED["fn"] = 0x0E  # the Mac fn / Globe key (see tiro.platform.mac.keys)
_NAMED.update({f"f{i}": 0x70 + i - 1 for i in range(1, 25)})
_NAMED.update({chr(c).lower(): c for c in range(0x41, 0x5B)})
_NAMED.update({str(d): 0x30 + d for d in range(10)})
_NAMED.update({f"num{d}": 0x60 + d for d in range(10)})

_VK_TO_NAME = {vk: name for name, vk in _NAMED.items()}

_LABELS = {
    "lctrl": "Left Ctrl", "rctrl": "Right Ctrl", "lshift": "Left Shift", "rshift": "Right Shift",
    "lalt": "Left Alt", "ralt": "Right Alt", "lwin": "Left Win", "rwin": "Right Win",
    "ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "win": "Win", "esc": "Esc", "capslock": "Caps Lock",
    "scrolllock": "Scroll Lock", "numlock": "Num Lock", "pageup": "Page Up", "pagedown": "Page Down",
    "printscreen": "Print Screen", "backspace": "Backspace",
}

_SIDED_TO_GENERIC = {
    VK_LCONTROL: "ctrl", VK_RCONTROL: "ctrl", VK_LSHIFT: "shift", VK_RSHIFT: "shift",
    VK_LMENU: "alt", VK_RMENU: "alt", VK_LWIN: "win", VK_RWIN: "win",
}
_ORDER = {"ctrl": 0, "shift": 1, "alt": 2, "win": 3}


def vk_name(vk: int) -> str:
    return _VK_TO_NAME.get(vk, f"vk{vk:02x}")


def name_vks(name: str) -> frozenset[int]:
    name = name.lower()
    if name in MODIFIER_VKS:
        return MODIFIER_VKS[name]
    if name in _NAMED:
        return frozenset({_NAMED[name]})
    if name.startswith("vk"):
        return frozenset({int(name[2:], 16)})
    raise ValueError(f"unknown key {name!r}")


_MAC_LABELS = {
    "lctrl": "Left Control", "rctrl": "Right Control", "lalt": "Left Option", "ralt": "Right Option",
    "lwin": "Left Command", "rwin": "Right Command", "ctrl": "\u2303 Control", "alt": "\u2325 Option",
    "win": "\u2318 Command", "shift": "\u21e7 Shift", "fn": "fn (Globe)", "backspace": "Delete", "enter": "Return",
}


def key_label(name: str) -> str:
    if IS_MAC and name in _MAC_LABELS:
        return _MAC_LABELS[name]
    return _LABELS.get(name, name.upper() if len(name) <= 3 else name.title())


@dataclass(frozen=True)
class HotkeySpec:
    keys: tuple[str, ...]

    @classmethod
    def parse(cls, value: str | list[str] | tuple[str, ...]) -> HotkeySpec:
        parts = value.split("+") if isinstance(value, str) else list(value)
        keys = tuple(p.strip().lower() for p in parts if p.strip())
        for k in keys:
            name_vks(k)
        if not keys:
            raise ValueError("empty hotkey")
        return cls(keys)

    @classmethod
    def from_captured(cls, vks: list[int]) -> HotkeySpec:
        """Build a spec from keys pressed during capture (in press order)."""
        if len(vks) == 1:
            return cls((vk_name(vks[0]),))
        mods = sorted({_SIDED_TO_GENERIC[v] for v in vks if v in _SIDED_TO_GENERIC}, key=_ORDER.__getitem__)
        others = [vk_name(v) for v in vks if v not in _SIDED_TO_GENERIC]
        return cls(tuple(mods + others))

    def vk_sets(self) -> list[frozenset[int]]:
        return [name_vks(k) for k in self.keys]

    @property
    def label(self) -> str:
        return " + ".join(key_label(k) for k in self.keys)

    def __str__(self) -> str:
        return "+".join(self.keys)


# single keys that are safe to dedicate to dictation (everything else needs a modifier)
_SAFE_SINGLE = {f"f{i}" for i in range(1, 25)} | {
    "capslock", "scrolllock", "pause", "insert", "menu", "printscreen", "numlock",
    "lctrl", "rctrl", "lalt", "ralt", "lshift", "rshift", "lwin", "rwin", "fn",
}


def hotkey_problem(spec: HotkeySpec) -> str | None:
    if len(spec.keys) == 1:
        k = spec.keys[0]
        if k in _SAFE_SINGLE or k.startswith("vk"):
            return None
        return "Pick a key you don't type with (like Right Ctrl or F8), or add a modifier such as Ctrl + Space."
    vks = [name_vks(k) for k in spec.keys]
    if not any(v & ALL_MODIFIERS for v in vks):
        return "A key combination needs at least one of Ctrl, Alt, Shift or Win."
    return None
