"""Network helpers for updates: is there a connection, is it metered, fetch the feed (and nothing else)."""
from __future__ import annotations

import ctypes
import logging
import platform
import sys
import urllib.error
import urllib.parse
import urllib.request

from tiro import __version__

log = logging.getLogger(__name__)

# The feed and its packs come from GitHub only (release assets redirect to its download hosts).
ALLOWED_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}


def platform_key() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    return ("win-" if sys.platform == "win32" else "mac-" if sys.platform == "darwin" else "linux-") + arch


def os_version() -> str:
    if sys.platform == "win32":
        v = sys.getwindowsversion()
        return f"{v.major}.{v.minor}.{v.build}"
    if sys.platform == "darwin":
        return platform.mac_ver()[0]
    return platform.release()


def user_agent() -> str:
    """Everything an update check tells the server: Tiro's version, the OS version and the processor type."""
    return f"Tiro/{__version__} ({platform_key()}; {os_version()})"


class _NLHint(ctypes.Structure):
    _fields_ = [("level", ctypes.c_int), ("cost", ctypes.c_int), ("approaching", ctypes.c_ubyte),
                ("over", ctypes.c_ubyte), ("roaming", ctypes.c_ubyte)]


def connection() -> tuple[bool | None, bool | None]:
    """(online, metered) as Windows sees it; None where it can't tell (older Windows, other systems)."""
    if sys.platform != "win32":
        return None, None
    try:
        fn = ctypes.WinDLL("iphlpapi").GetNetworkConnectivityHint  # Windows 10 2004 and later
    except (OSError, AttributeError):
        return None, None
    hint = _NLHint()
    if fn(ctypes.byref(hint)) != 0:
        return None, None
    online = None if hint.level == 0 else hint.level in (3, 4)  # internet access (possibly constrained)
    metered = hint.cost in (2, 3) or bool(hint.over) or bool(hint.roaming)  # fixed or per-byte cost
    return online, metered


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlsplit(newurl).hostname or ""
        if urllib.parse.urlsplit(newurl).scheme != "https" or host not in ALLOWED_HOSTS:
            raise urllib.error.HTTPError(newurl, code, f"redirect to {host} refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_feed(url: str, max_bytes: int, timeout: float = 20.0) -> bytes:
    parts = urllib.parse.urlsplit(url)
    local = parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost")  # end-to-end tests
    if not local and (parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS):
        raise ValueError(f"not an allowed update address: {url}")
    opener = urllib.request.build_opener(_Redirects)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent(), "Cache-Control": "no-cache"})
    with opener.open(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("the update feed is too large")
    return data
