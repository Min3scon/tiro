"""Watch a screen region and record when its pixels change (for apps whose text can't be read directly,
e.g. a terminal inside VS Code). A blinking caret is ignored: changes no wider than a few pixels don't count.

    from dev.latency.pixel_watch import PixelWatch
    w = PixelWatch(hwnd); w.start(); ...; changes = w.stop()   # [(time.time(), changed_pixels, bbox_width)]
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC,
                         ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, wintypes.UINT]
SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def client_rect_on_screen(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, r.right - r.left, r.bottom - r.top


class PixelWatch:
    def __init__(self, hwnd: int, interval: float = 0.004, caret_px: int = 4, region=None):
        self.hwnd = hwnd
        self.interval = interval
        self.caret_px = caret_px  # changes narrower than this are the caret blinking
        self.region = region  # (x, y, w, h) on screen; default: the window's client area
        self.changes: list[tuple[float, int, int]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _grab_loop(self) -> None:
        x, y, w, h = self.region or client_rect_on_screen(self.hwnd)
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
        gdi32.SelectObject(mem, bmp)
        bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = np.empty((h, w, 4), dtype=np.uint8)
        prev = None
        try:
            while not self._stop.is_set():
                t = time.time()
                gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, SRCCOPY | CAPTUREBLT)
                gdi32.GetDIBits(mem, bmp, 0, h, buf.ctypes.data, ctypes.byref(bih), 0)
                cur = buf[:, :, :3].copy()
                if prev is not None:
                    diff = np.any(cur != prev, axis=2)
                    n = int(diff.sum())
                    if n:
                        cols = np.flatnonzero(diff.any(axis=0))
                        width = int(cols[-1] - cols[0] + 1)
                        if width > self.caret_px:
                            self.changes.append((t, n, width))
                prev = cur
                rest = self.interval - (time.time() - t)
                if rest > 0:
                    time.sleep(rest)
        finally:
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, screen)

    def start(self) -> None:
        self.changes = []
        self._stop.clear()
        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()

    def stop(self) -> list[tuple[float, int, int]]:
        self._stop.set()
        if self._thread:
            self._thread.join(2)
        return self.changes
