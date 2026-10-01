"""The three macOS permissions Tiro needs, with plain explanations and the System Settings pane for each.

* Microphone: to hear you.
* Accessibility: to type the text into the app you're using (and to keep the hotkey from reaching it).
* Input Monitoring: to notice the hotkey while another app is in front.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

PANES = {
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "input": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
}


@dataclass(frozen=True)
class Permission:
    key: str
    title: str
    why: str
    how: str


PERMISSIONS = (
    Permission("microphone", "Microphone", "So Tiro can hear you while you hold the key. Audio never leaves your Mac.",
               "Click Allow when macOS asks, or switch Tiro on under Privacy & Security → Microphone."),
    Permission("accessibility", "Accessibility", "So Tiro can type the words into the app you're using.",
               "In Privacy & Security → Accessibility, switch Tiro on (click + and pick Tiro if it isn't listed)."),
    Permission("input", "Input Monitoring", "So Tiro notices your dictation key while another app is in front.",
               "In Privacy & Security → Input Monitoring, switch Tiro on. macOS may ask you to reopen Tiro."),
)

try:
    _iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
    _iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
    _iokit.IOHIDCheckAccess.restype = ctypes.c_uint32
    _iokit.IOHIDRequestAccess.argtypes = [ctypes.c_uint32]
    _iokit.IOHIDRequestAccess.restype = ctypes.c_bool
except (OSError, AttributeError):  # pragma: no cover
    _iokit = None
LISTEN_EVENT = 1  # kIOHIDRequestTypeListenEvent


def status(key: str) -> str:
    """granted | denied | unknown"""
    try:
        if key == "microphone":
            from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

            s = int(AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio))
            return {3: "granted", 2: "denied", 1: "denied"}.get(s, "unknown")
        if key == "accessibility":
            from ApplicationServices import AXIsProcessTrusted

            return "granted" if AXIsProcessTrusted() else "denied"
        if key == "input":
            if _iokit is None:
                return "unknown"
            s = int(_iokit.IOHIDCheckAccess(LISTEN_EVENT))
            return {0: "granted", 1: "denied"}.get(s, "unknown")
    except Exception:
        log.exception("permission check failed for %s", key)
    return "unknown"


def request(key: str) -> None:
    """Ask macOS to show its own prompt (where there is one), then open the matching settings pane."""
    try:
        if key == "microphone":
            from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

            AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, lambda granted: None)
            if status(key) == "denied":
                open_pane(key)
            return
        if key == "accessibility":
            from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        elif key == "input" and _iokit is not None:
            _iokit.IOHIDRequestAccess(LISTEN_EVENT)
    except Exception:
        log.exception("permission request failed for %s", key)
    open_pane(key)


def open_pane(key: str) -> None:
    subprocess.Popen(["open", PANES[key]])


def all_granted() -> bool:
    return all(status(p.key) == "granted" for p in PERMISSIONS)


def hide_dock_icon() -> None:
    """Menu-bar app: no Dock icon, no app switcher entry (same as LSUIElement in Info.plist)."""
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception:
        log.exception("could not hide the Dock icon")
