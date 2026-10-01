"""Text insertion (dispatches to tiro.platform.windows / tiro.platform.mac)."""

import sys

if sys.platform == "darwin":
    from tiro.platform.mac.injector import *  # noqa: F403
else:
    from tiro.platform.windows.injector import *  # noqa: F403
