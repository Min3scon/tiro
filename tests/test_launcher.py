"""The Windows launcher (installer/TiroLauncher) with fake Tiro versions: trial, commit, roll back, safe mode.

Needs the launcher and the fake app built (dotnet build ... -o installer/out/launcher, installer/out/faketiro);
skipped otherwise. Runs entirely inside a temporary folder.
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "installer" / "out" / "launcher" / "Tiro.exe"
FAKE = ROOT / "installer" / "out" / "faketiro" / "Tiro.exe"

pytestmark = pytest.mark.skipif(sys.platform != "win32" or not LAUNCHER.is_file() or not FAKE.is_file(),
                                reason="launcher / fake app not built")


def install(tmp_path: Path, versions: dict[str, str], state: dict | None) -> Path:
    root = tmp_path / "Programs" / "Tiro"
    root.mkdir(parents=True)
    shutil.copy2(LAUNCHER, root / "Tiro.exe")
    for v, behaviour in versions.items():
        d = root / f"app-{v}"
        d.mkdir()
        shutil.copy2(FAKE, d / "Tiro.exe")
        (d / "behaviour.txt").write_text(behaviour)
    if state is not None:
        (root / "state.json").write_text(json.dumps({"format": 1, "bad": [], **state}))
    return root


def launch(root: Path, *args: str, wait_runs: int = 1, timeout: float = 30) -> int:
    p = subprocess.run([str(root / "Tiro.exe"), *args], timeout=timeout)
    end = time.time() + 10
    while time.time() < end and len(runs(root)) < wait_runs:
        time.sleep(0.1)
    time.sleep(0.3)
    return p.returncode


def runs(root: Path) -> list[str]:
    f = root / "runs.txt"
    return f.read_text().splitlines() if f.exists() else []


def state(root: Path) -> dict:
    return json.loads((root / "state.json").read_text())


def wait_gone(seconds: float = 4.0) -> None:
    time.sleep(seconds)  # let the fake app exit before the next launch (it "runs" for ~2 s)


def test_normal_start_runs_current(tmp_path):
    root = install(tmp_path, {"1.0.0": "good"}, {"current": "1.0.0"})
    launch(root, "--autostart")
    assert runs(root) == ["1.0.0 --autostart"]


def test_good_update_is_committed(tmp_path):
    root = install(tmp_path, {"1.0.0": "good", "1.1.0": "good"}, {"current": "1.0.0", "pending": "1.1.0"})
    launch(root)
    assert runs(root)[0].startswith("1.1.0 --update-trial ")
    s = state(root)
    assert s["current"] == "1.1.0" and s["previous"] == "1.0.0" and s["pending"] is None


def test_bad_update_is_tried_twice_then_rolled_back(tmp_path):
    root = install(tmp_path, {"1.0.0": "good", "1.2.0": "crash"}, {"current": "1.0.0", "pending": "1.2.0"})
    launch(root, wait_runs=3)
    r = runs(root)
    assert r[0].startswith("1.2.0 --update-trial") and r[1].startswith("1.2.0 --update-trial")
    assert r[2] == "1.0.0 --rolled-back-from 1.2.0"
    s = state(root)
    assert s["current"] == "1.0.0" and s["pending"] is None and "1.2.0" in s["bad"]
    wait_gone()
    launch(root, wait_runs=4)  # never tried again
    assert runs(root)[3] == "1.0.0 "


def test_two_failed_starts_mean_safe_mode(tmp_path):
    root = install(tmp_path, {"1.0.0": "crash"}, {"current": "1.0.0"})
    launch(root, wait_runs=1)
    launch(root, wait_runs=2)
    launch(root, wait_runs=3)
    assert runs(root)[2] == "1.0.0 --safe-mode"
    (root / "app-1.0.0" / "behaviour.txt").write_text("good")
    launch(root, wait_runs=4)  # starts properly in safe mode: confirmed
    wait_gone()
    launch(root, wait_runs=5)
    assert runs(root)[4] == "1.0.0 "  # back to normal


def test_commands_pass_through_with_exit_code(tmp_path):
    root = install(tmp_path, {"1.0.0": "good", "1.1.0": "good"}, {"current": "1.0.0", "pending": "1.1.0"})
    code = launch(root, "--quit")
    assert code == 7 and runs(root) == ["1.0.0 --quit"]  # commands never start a trial
    assert state(root)["pending"] == "1.1.0"


def test_missing_state_uses_newest_version(tmp_path):
    root = install(tmp_path, {"1.0.0": "good", "1.10.0": "good", "1.9.0": "good"}, None)
    launch(root)
    assert runs(root) == ["1.10.0 "]
    assert state(root)["current"] == "1.10.0"
