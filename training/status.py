"""Rewrite the LIVE-STATUS block at the top of PROGRESS.md from the background jobs' logs.

    python -m training.status            # once
    python -m training.status --loop 300 # every 5 minutes (started detached by training/tools/launch.ps1)

A job is anything started with launch.ps1: it has work/logs/<name>.pid and <name>.out. Jobs can also write a
one-line human summary to work/logs/<name>.status (e.g. "labelling: 1,234 / 9,876 shards, 3.2 h left").
"""
from __future__ import annotations

import argparse
import re
import shutil
import time
from pathlib import Path

import psutil

from training.common import LOGS, REPO, WORK

PROGRESS = REPO / "PROGRESS.md"
BEGIN, END = "<!-- LIVE-STATUS:BEGIN -->", "<!-- LIVE-STATUS:END -->"
NOISE = re.compile(r"Warning: You are sending|it/s\]|Loading weights|MatMul8bitLt|^\s*$")


def tail(path: Path, n: int = 3) -> list[str]:
    if not path.exists():
        return []
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 64_000))
        lines = f.read().decode("utf-8", "replace").splitlines()
    lines = [ln.strip() for ln in lines if not NOISE.search(ln)]
    return [ln[:220] for ln in lines[-n:]]


def gpu_line() -> str:
    try:
        import pynvml

        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        util = pynvml.nvmlDeviceGetUtilizationRates(h)
        temp = pynvml.nvmlDeviceGetTemperature(h, 0)
        return f"GPU {util.gpu}% busy, {mem.used / 2**30:.1f}/{mem.total / 2**30:.1f} GB, {temp} °C"
    except Exception:
        return "GPU: n/a"


def render() -> str:
    rows = []
    for pid_file in sorted(LOGS.glob("*.pid"), key=lambda p: p.stat().st_mtime, reverse=True):
        name = pid_file.stem
        try:
            pid = int(pid_file.read_text().strip())
            alive = psutil.pid_exists(pid) and "python" in psutil.Process(pid).name().lower()
        except Exception:
            alive = False
        started = time.strftime("%d %b %H:%M", time.localtime(pid_file.stat().st_mtime))
        status_file = LOGS / f"{name}.status"
        summary = status_file.read_text(encoding="utf-8").strip() if status_file.exists() else ""
        last = tail(LOGS / f"{name}.out", 2)
        errs = [ln for ln in tail(LOGS / f"{name}.err", 6) if "Error" in ln or "Traceback" in ln]
        state = "running" if alive else "finished"
        rows.append((name, state, started, summary, last, errs))
    du = shutil.disk_usage(WORK)
    out = [f"_Updated {time.strftime('%Y-%m-%d %H:%M')}. {gpu_line()}. "
           f"Disk: {du.free / 2**30:.0f} GB free on the work drive._", ""]
    running = [r for r in rows if r[1] == "running"]
    done = [r for r in rows if r[1] != "running"][:6]
    if not running:
        out.append("No background job is running right now.")
    for name, state, started, summary, last, errs in running + done:
        icon = "▶" if state == "running" else "✓"
        out.append(f"- {icon} **{name}** ({state}, started {started}){': ' + summary if summary else ''}")
        for ln in last:
            out.append(f"  - `{ln}`")
        for ln in errs[-2:]:
            out.append(f"  - ⚠ `{ln}`")
    return "\n".join(out)


def update() -> None:
    text = PROGRESS.read_text(encoding="utf-8")
    if BEGIN not in text:
        return
    head, rest = text.split(BEGIN, 1)
    _, tail_ = rest.split(END, 1)
    new = f"{head}{BEGIN}\n{render()}\n{END}{tail_}"
    if new != text:
        tmp = PROGRESS.with_suffix(".md.tmp")
        tmp.write_text(new, encoding="utf-8")
        for _ in range(5):  # the file may be open in an editor for a moment
            try:
                tmp.replace(PROGRESS)
                break
            except PermissionError:
                time.sleep(1)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--loop", type=int, default=0)
    a = p.parse_args()
    while True:
        try:
            update()
        except Exception as exc:  # never let the status writer die
            print("status update failed:", exc, flush=True)
        if not a.loop:
            break
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
