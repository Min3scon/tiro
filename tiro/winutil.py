"""Window, process, clipboard and input helpers (dispatches to tiro.platform.windows / tiro.platform.mac)."""

import sys

if sys.platform == "darwin":
    from tiro.platform.mac.winutil import *  # noqa: F403
else:
    from tiro.platform.windows.winutil import *  # noqa: F403
