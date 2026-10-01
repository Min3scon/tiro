"""macOS counterparts of the Windows window/process helpers.

A "window" handle here is the process id of the frontmost application: that's what Tiro needs to tell
whether focus moved between apps. Titles come from Accessibility when it's granted.
"""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger(__name__)

TERMINALS = {"com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable", "net.kovidgoyal.kitty",
             "io.alacritty", "com.github.wez.wezterm"}


def _front_app():
    from AppKit import NSWorkspace

    return NSWorkspace.sharedWorkspace().frontmostApplication()


def _app_for(pid: int):
    from AppKit import NSRunningApplication

    return NSRunningApplication.runningApplicationWithProcessIdentifier_(pid) if pid else None


def foreground_window() -> int:
    app = _front_app()
    return int(app.processIdentifier()) if app is not None else 0


def window_pid(hwnd: int) -> int:
    return int(hwnd)


def window_class(hwnd: int) -> str:
    """The app's bundle identifier (e.g. com.apple.TextEdit)."""
    app = _app_for(hwnd)
    return str(app.bundleIdentifier() or "") if app is not None else ""


def window_app(hwnd: int) -> str:
    app = _app_for(hwnd)
    if app is None:
        return ""
    name = app.localizedName() or ""
    return str(name).lower()


def window_title(hwnd: int) -> str:
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            kAXFocusedWindowAttribute,
            kAXTitleAttribute,
        )

        app = AXUIElementCreateApplication(hwnd)
        err, win = AXUIElementCopyAttributeValue(app, kAXFocusedWindowAttribute, None)
        if err or win is None:
            return ""
        err, title = AXUIElementCopyAttributeValue(win, kAXTitleAttribute, None)
        return str(title or "") if not err else ""
    except Exception:
        return ""


def process_name(pid: int) -> str:
    return window_app(pid)


def window_rect(hwnd: int):
    return None


def is_window(hwnd: int) -> bool:
    app = _app_for(hwnd)
    return app is not None and not app.isTerminated()


def activate(hwnd: int) -> bool:
    app = _app_for(hwnd)
    if app is None:
        return False
    try:
        from AppKit import NSApplicationActivateIgnoringOtherApps

        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    except Exception:
        return False
    import time

    for _ in range(20):
        if foreground_window() == hwnd:
            return True
        time.sleep(0.01)
    return foreground_window() == hwnd


def is_elevated() -> bool:
    return False


def process_elevated(pid: int) -> bool:
    return False


def foreground_blocked_by_uipi() -> bool:
    return False


def foreground_is_terminal() -> bool:
    return window_class(foreground_window()) in TERMINALS


def relaunch_as_admin(args) -> bool:
    return False


def dark_title_bar(hwnd: int) -> None:
    return


def monitor_device(hwnd: int) -> str:
    """Name of the screen the frontmost app is on (Qt screen name), or "" for the main screen."""
    return ""


def make_overlay_window(view_ptr: int) -> None:
    """Float the overlay above everything (full-screen apps and all Spaces), never take focus or clicks."""
    try:
        import objc
        from AppKit import (
            NSStatusWindowLevel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorIgnoresCycle,
            NSWindowCollectionBehaviorStationary,
        )

        view = objc.objc_object(c_void_p=view_ptr)
        win = view.window()
        if win is None:
            return
        win.setLevel_(NSStatusWindowLevel)
        win.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary
                                   | NSWindowCollectionBehaviorFullScreenAuxiliary
                                   | NSWindowCollectionBehaviorIgnoresCycle)
        win.setIgnoresMouseEvents_(True)
        win.setHidesOnDeactivate_(False)
        win.setHasShadow_(False)
    except Exception:
        log.exception("could not configure the overlay window")


# ---------------------------------------------------------------------- clipboard
def set_clipboard_text(text: str) -> bool:
    from AppKit import NSPasteboard, NSPasteboardTypeString

    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    return bool(pb.setString_forType_(text, NSPasteboardTypeString))


def clipboard_snapshot():
    from AppKit import NSPasteboard

    from tiro.platform.mac.injector import _snapshot

    return _snapshot(NSPasteboard.generalPasteboard())


def clipboard_restore(items) -> None:
    from AppKit import NSPasteboard

    from tiro.platform.mac.injector import _restore

    _restore(NSPasteboard.generalPasteboard(), items)


# ---------------------------------------------------------------------- keyboard / mouse (for context)
def key_to_char(vk: int, scan: int, shift: bool) -> str | None:
    """The character the key event being handled produced (the event tap records it)."""
    from tiro.platform.mac import hook

    return hook.last_char


def click_is_elsewhere() -> bool:
    """A click in the menu bar or on one of Tiro's windows doesn't move any app's caret."""
    from AppKit import NSEvent, NSScreen

    try:
        loc = NSEvent.mouseLocation()
        screen = NSScreen.mainScreen()
        if screen is not None and loc.y >= screen.frame().size.height - 25:
            return True  # menu bar
    except Exception:
        pass
    return foreground_window() == os.getpid()


def watch_mouse_clicks(on_click, armed: threading.Event) -> None:
    """Mouse presses are seen by the keyboard event tap; it calls us back."""
    from tiro.platform.mac import hook

    def clicked():
        if armed.is_set():
            on_click(click_is_elsewhere())

    hook.add_click_listener(clicked)
