"""Short UI sounds (dispatches to tiro.platform.windows / tiro.platform.mac)."""

import sys

if sys.platform == "darwin":
    from tiro.platform.mac.sounds import *  # noqa: F403
else:
    from tiro.platform.windows.sounds import *  # noqa: F403
