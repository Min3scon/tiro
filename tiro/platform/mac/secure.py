"""Is the focused field a password (or otherwise secure) field? (macOS)

Two checks: whether secure keyboard input is on (macOS turns it on for password fields, and for Terminal's
"Secure Keyboard Entry"), and whether the focused element is an AXSecureTextField. Tiro still types into
such fields but never learns from them, saves them or corrects them.
"""

from __future__ import annotations

import ctypes
import logging

log = logging.getLogger(__name__)

try:
    _carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
    _carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool
except OSError:  # pragma: no cover
    _carbon = None


def _secure_input() -> bool:
    return bool(_carbon is not None and _carbon.IsSecureEventInputEnabled())


def _focused_is_secure_text() -> bool | None:
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateSystemWide,
            kAXFocusedUIElementAttribute,
            kAXRoleAttribute,
            kAXSubroleAttribute,
        )

        system = AXUIElementCreateSystemWide()
        err, element = AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute, None)
        if err or element is None:
            return None
        for attr in (kAXSubroleAttribute, kAXRoleAttribute):
            err, value = AXUIElementCopyAttributeValue(element, attr, None)
            if not err and value and "SecureTextField" in str(value):
                return True
        return False
    except Exception:
        return None


def is_secure_field(foreground: int) -> bool | None:
    """True for password fields, False for ordinary ones, None if it couldn't be determined."""
    if _secure_input():
        return True
    return _focused_is_secure_text()
