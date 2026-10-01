"""Password / secure field detection (dispatches to tiro.platform.windows / tiro.platform.mac)."""

import sys

if sys.platform == "darwin":
    from tiro.platform.mac.secure import *  # noqa: F403
else:
    from tiro.platform.windows.secure import *  # noqa: F403
