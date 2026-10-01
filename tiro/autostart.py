"""Start at login (dispatches to tiro.platform.windows / tiro.platform.mac)."""

import sys

if sys.platform == "darwin":
    from tiro.platform.mac.autostart import *  # noqa: F403
else:
    from tiro.platform.windows.autostart import *  # noqa: F403
