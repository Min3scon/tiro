"""Runs the heavy jobs only inside the allowed hours (default 18:00-21:00, local time) and stops them outside.

    python -m training.scheduler            # run forever (started detached at below-normal priority)
    python -m training.scheduler --once     # one decision pass, for testing

The plan lives in work/schedule.json (created on first run; edit it any time, it's re-read every minute):
  windows: [{"start": "18:00", "end": "21:00"}]   allowed hours (several windows allowed)
  jobs:    ordered queue; each {name, module, args, python ("work" or "app"), uses ("gpu"|"cpu"), after [names]}
  limits:  {"gpu": 1, "cpu": 2}   how many jobs of each kind may run at once
Every job is resumable, so stopping one at the end of a window costs at most its current piece of work.
Trainers get a few minutes' warning (a stop file) to save a checkpoint before the window closes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from training.common import LOGS, REPO, WORK

PLAN = WORK / "schedule.json"
STATE = LOGS / "scheduler_state.json"
STATUS = LOGS / "scheduler.status"
WORK_PY = WORK / "venv" / "Scripts" / "python.exe"
APP_PY = REPO / ".venv" / "Scripts" / "python.exe"
WIND_DOWN = timedelta(minutes=3)

DEFAULT_PLAN = {
    "windows": [{"start": "2000-01-01 00:00", "end": None}],  # no restriction unless the plan file says so
    "limits": {"gpu": 1, "cpu": 2},
    "jobs": [
        {"name": "prepare_all", "module": "training.data.prepare", "uses": "cpu",
         "args": "--source ami-ihm,ami-sdm,librispeech,voxpopuli,peoples-speech --workers 4"},
        {"name": "label_parakeet", "module": "training.labels.parakeet_label", "python": "app", "uses": "gpu",
         "args": "--follow --gpu-mem-gb 4 --batch-sec 60", "after": ["prepare_all"]},
        {"name": "tts", "module": "training.synth.tts", "uses": "cpu", "args": "--workers 3"},
        {"name": "bench_devmini", "module": "training.eval.bench_all", "uses": "cpu",
         "args": "--sets dev-mini --threads 4"},
    ],
}

BELOW_NORMAL = 0x00004000
NO_WINDOW = 0x08000000
NEW_GROUP = 0x00000200


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    with open(LOGS / "scheduler.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def parse_hm(s: str, day: datetime) -> datetime:
    h, m = (int(x) for x in s.split(":"))
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


FOREVER = datetime(9999, 1, 1)


def _absolute(w: dict) -> tuple[datetime, datetime] | None:
    """A one-off window: {"start": "2026-10-01 18:00", "end": "2026-10-01 21:00"}; "end": null = no end."""
    if " " not in str(w.get("start", "")):
        return None
    start = datetime.strptime(w["start"], "%Y-%m-%d %H:%M")
    end = datetime.strptime(w["end"], "%Y-%m-%d %H:%M") if w.get("end") else FOREVER
    return start, end


def window_now(plan: dict, now: datetime) -> tuple[datetime, datetime] | None:
    """The allowed window containing `now`, else None. Windows are either one-off (with dates) or daily
    ("HH:MM" to "HH:MM", may run past midnight)."""
    for w in plan.get("windows", []):
        ab = _absolute(w)
        if ab:
            if ab[0] <= now < ab[1]:
                return ab
            continue
        for base in (now, now - timedelta(days=1)):
            start = parse_hm(w["start"], base)
            end = parse_hm(w["end"], base)
            if end <= start:
                end += timedelta(days=1)
            if start <= now < end:
                return start, end
    return None


def next_start(plan: dict, now: datetime) -> datetime | None:
    starts = []
    for w in plan.get("windows", []):
        ab = _absolute(w)
        if ab:
            if ab[0] > now:
                starts.append(ab[0])
            continue
        for d in range(0, 2):
            s = parse_hm(w["start"], now + timedelta(days=d))
            if s > now:
                starts.append(s)
    return min(starts) if starts else None


def describe(plan: dict) -> str:
    parts = []
    for w in plan.get("windows", []):
        ab = _absolute(w)
        if ab:
            parts.append(f"{ab[0]:%a %d %b %H:%M}" + (f"-{ab[1]:%H:%M}" if ab[1] != FOREVER else " onwards"))
        else:
            parts.append(f"daily {w['start']}-{w['end']}")
    return ", ".join(parts)


def alive(pid: int) -> bool:
    try:
        import psutil

        return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


class Scheduler:
    def __init__(self):
        self.procs: dict[str, subprocess.Popen] = {}
        self.state = load_json(STATE, {"done": [], "attempts": {}, "stopped_by_window": []})

    def start(self, job: dict) -> None:
        name = job["name"]
        py = APP_PY if job.get("python") == "app" else WORK_PY
        args = [str(py), "-u", "-m", job["module"]] + job.get("args", "").split()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        out = open(LOGS / f"{name}.out", "a", encoding="utf-8")
        err = open(LOGS / f"{name}.err", "a", encoding="utf-8")
        (LOGS / f"{name}.stop").unlink(missing_ok=True)
        p = subprocess.Popen(args, cwd=str(REPO), stdout=out, stderr=err, env=env,
                             creationflags=BELOW_NORMAL | NO_WINDOW | NEW_GROUP)
        (LOGS / f"{name}.pid").write_text(str(p.pid), encoding="utf-8")
        self.procs[name] = p
        self.state["attempts"][name] = self.state["attempts"].get(name, 0) + 1
        log(f"started {name} (pid {p.pid})")

    def stop(self, name: str, reason: str) -> None:
        p = self.procs.pop(name, None)
        if p is not None and p.poll() is None:
            kill_tree(p.pid)
            log(f"stopped {name}: {reason}")

    def reap(self, plan: dict) -> None:
        jobs = {j["name"]: j for j in plan["jobs"]}
        for name, p in list(self.procs.items()):
            code = p.poll()
            if code is None:
                continue
            del self.procs[name]
            deps = jobs.get(name, {}).get("after", [])
            if code == 0 and all(d in self.state["done"] for d in deps):
                self.state["done"].append(name)
                log(f"{name} finished")
            elif code == 0:
                log(f"{name} exited cleanly but waits on {deps}: will run again in the next window")
            elif code == 3:
                log(f"{name} paused itself (checkpoint saved)")
            else:
                log(f"{name} exited with code {code}; will retry")

    def tick(self) -> None:
        plan = load_json(PLAN, None)
        if plan is None:
            save_json(PLAN, DEFAULT_PLAN)
            plan = DEFAULT_PLAN
        self.reap(plan)
        now = datetime.now()
        win = window_now(plan, now)
        pending = [j for j in plan["jobs"] if j["name"] not in self.state["done"]]
        if win is None:
            for name in list(self.procs):
                self.stop(name, "outside the allowed hours")
            nxt = next_start(plan, now)
            status = (f"Paused until {nxt:%a %H:%M} (heavy work runs {describe(plan)})" if nxt
                      else "Paused (no allowed hours left in work/schedule.json)")
        else:
            start, end = win
            if end - now <= WIND_DOWN:  # ask trainers to checkpoint and stop
                for name in self.procs:
                    (LOGS / f"{name}.stop").write_text("window closing", encoding="utf-8")
            else:
                limits = plan.get("limits", {"gpu": 1, "cpu": 2})
                used = {"gpu": 0, "cpu": 0}
                for name in self.procs:
                    kind = next((j.get("uses", "cpu") for j in plan["jobs"] if j["name"] == name), "cpu")
                    used[kind] = used.get(kind, 0) + 1
                for job in pending:
                    if job["name"] in self.procs:
                        continue
                    kind = job.get("uses", "cpu")
                    if used.get(kind, 0) >= limits.get(kind, 1):
                        continue
                    if job.get("hold"):  # paused by hand in schedule.json (e.g. while a test needs the GPU)
                        continue
                    if self.state["attempts"].get(job["name"], 0) > 50:
                        continue
                    self.start(job)
                    used[kind] = used.get(kind, 0) + 1
            until = "" if end == FOREVER else f" until {end:%H:%M}"
            status = f"Working{until}: " + (", ".join(self.procs) or "nothing left to run")
        left = [j["name"] for j in pending if j["name"] not in self.procs]
        if left:
            status += "; queued: " + ", ".join(left)
        STATUS.write_text(status, encoding="utf-8")
        save_json(STATE, self.state)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--every", type=int, default=30)
    a = p.parse_args()
    LOGS.mkdir(parents=True, exist_ok=True)
    s = Scheduler()
    log(f"scheduler up (pid {os.getpid()})")
    while True:
        try:
            s.tick()
        except Exception as exc:  # keep scheduling whatever happens
            log(f"tick failed: {exc!r}")
        if a.once:
            break
        time.sleep(a.every)


if __name__ == "__main__":
    sys.exit(main())
