"""Short, quiet UI cues on macOS (NSSound)."""

from __future__ import annotations

from tiro.paths import asset

_cache: dict = {}


def play(name: str) -> None:
    path = asset("sounds", f"{name}.wav")
    if not path.is_file():
        return
    try:
        from AppKit import NSSound

        snd = _cache.get(name)
        if snd is None:
            snd = _cache[name] = NSSound.alloc().initWithContentsOfFile_byReference_(str(path), True)
        if snd is not None:
            snd.stop()
            snd.play()
    except Exception:
        pass
