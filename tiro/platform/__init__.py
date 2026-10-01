"""Platform layer: everything that talks to the operating system lives under windows/ or mac/.

The rest of Tiro imports the dispatching modules (tiro.winutil, tiro.injector, tiro.secure, tiro.autostart,
tiro.sounds, tiro.hotkey's KeyboardHook), which pick the right implementation at import time.
"""

import sys

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
NAME = "mac" if IS_MAC else "windows"
