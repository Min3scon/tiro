"""End-to-end test of in-app updates on real frozen builds, entirely on this PC (nothing published).

  1. Three small CPU-only test builds: 9.0.0, 9.0.1, and 9.0.2 that crashes on its trial start
     (tools/build.ps1 -TestVersion ... -TestKey <throwaway key> -NoCuda).
  2. An install in the versioned layout under work/update-e2e/Programs/Tiro (launcher + app-9.0.0 + models),
     with its own settings (TIRO_CONFIG_DIR), data folder (LOCALAPPDATA) and the F24 hotkey.
  3. A local HTTP server with the signed feed and the packs.
Checks: 9.0.0 finds 9.0.1, downloads only the changed pack, verifies it, queues it; a restart tries 9.0.1 and
commits it (9.0.0 kept as "previous"); 9.0.2 is found, fails its trial twice and Tiro goes back to 9.0.1 for good;
a feed signed with another key is refused; a tampered pack is refused.

    python dev/update_e2e.py [--skip-build] [--keep]
Results: work/update-e2e/report.json
"""
from __future__ import annotations

import argparse
import base64
import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tiro.update import ed25519, feed

E2E = ROOT / "work" / "update-e2e"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
VERSIONS = {"9.0.0": "", "9.0.1": "", "9.0.2": "crash"}
CPU_MODEL = ROOT / "models" / "parakeet-tdt-0.6b-v2"


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    """Static files with HTTP Range support (the updater resumes downloads)."""

    def send_head(self):
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        start = int(rng.split("=")[1].split("-")[0])
        f = open(path, "rb")  # noqa: SIM115
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
        self.send_header("Content-Length", str(size - start))
        self.end_headers()
        return f

    def log_message(self, *args):
        pass


def run(cmd, **kw):
    print("$", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def build(version: str, pub: str, crash: str) -> Path:
    dist = f"build\\e2e-{version}"
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ROOT / "tools" / "build.ps1",
            "-TestVersion", version, "-TestKey", pub, "-NoCuda", "-Dist", dist, "-SkipInstaller", "-SkipPackage"]
    if crash:
        args += ["-TestCrash", "1"]
    run(args)
    return ROOT / dist / "Tiro"


def package(app: Path, version: str, server: Path, port: int) -> Path:
    out = server / f"v{version}"
    run([PY, ROOT / "tools" / "package_release.py", "--dist", app, "--out", out, "--no-models", "--version", version,
         "--base-url", f"http://127.0.0.1:{port}/v{version}"])
    return out / "update-fragment-win-x64.json"


def sign_feed(fragments: list[Path], seed: bytes, server: Path, name: str = "stable.json", revoke=()) -> None:
    manifest = E2E / "manifest.json"
    cmd = [PY, ROOT / "tools" / "feed.py", "compose", "--channel", "stable", "--out", manifest, "--allow-same"]
    for f in fragments:
        cmd += ["--fragment", f]
    for v in revoke:
        cmd += ["--revoke", v]
    run(cmd)
    env = {**os.environ, "TIRO_UPDATE_KEY": base64.b64encode(seed).decode()}
    m = json.loads(manifest.read_text(encoding="utf-8"))
    (server / name).write_bytes(feed.seal(m, seed, "e2e-test"))
    del env


def wait_for(pred, timeout: float, what: str):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for: {what}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    report: dict = {"checks": []}

    def check(name: str, ok: bool, detail: str = "") -> None:
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""), flush=True)

    E2E.mkdir(parents=True, exist_ok=True)
    seed_file = E2E / "throwaway-test-key.txt"  # work/ is never committed; this key only ever signs test feeds
    if not seed_file.exists():
        seed_file.write_text(base64.b64encode(os.urandom(32)).decode())
    seed = base64.b64decode(seed_file.read_text())
    pub = base64.b64encode(ed25519.public_key(seed)).decode()
    builds = {}
    for v, crash in VERSIONS.items():
        builds[v] = (ROOT / f"build/e2e-{v}" / "Tiro") if a.skip_build else build(v, pub, crash)

    # local server
    server_dir = E2E / "server"
    if server_dir.exists():
        shutil.rmtree(server_dir)
    server_dir.mkdir()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(RangeHandler,
                                                                                 directory=str(server_dir)))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    frags = {v: package(builds[v], v, server_dir, port) for v in VERSIONS}

    # the install: launcher + app-9.0.0 (with its inventory) + shared CPU model, own settings and data folder
    inst = E2E / "Programs" / "Tiro"
    if inst.parent.exists():
        shutil.rmtree(inst.parent)
    inst.mkdir(parents=True)
    shutil.copy2(ROOT / "installer" / "out" / "launcher" / "Tiro.exe", inst / "Tiro.exe")
    shutil.copytree(builds["9.0.0"], inst / "app-9.0.0")
    (inst / "state.json").write_text(json.dumps({"format": 1, "current": "9.0.0", "previous": None,
                                                 "pending": None, "trial": None, "bad": []}))
    models = inst / "models" / "parakeet-tdt-0.6b-v2"
    models.mkdir(parents=True)
    for f in CPU_MODEL.iterdir():
        if f.is_file() and ("int8" in f.name or f.suffix in (".json", ".txt")):
            os.link(f, models / f.name)
    profile = E2E / "profile"
    if profile.exists():
        shutil.rmtree(profile)
    profile.mkdir()
    (profile / "settings.json").write_text(json.dumps({
        "hotkey": "f22", "setup_done": True, "welcome_shown": True, "device": "cpu", "ai_correction": False,
        "history": False, "sounds": False, "auto_update_check": True, "install_on_quit": True,
        "whats_new_seen": "9.0.0"}))
    local = E2E / "localappdata"
    if local.exists():
        shutil.rmtree(local)
    local.mkdir()
    # Isolation: these copies never hear the real microphone (a silent test recording instead), may type into no
    # window (empty allow-list), and listen to a key no other test presses (F22).
    silence = E2E / "silence.wav"
    import wave

    with wave.open(str(silence), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000)
    no_windows = E2E / "allowed-windows.txt"
    no_windows.write_text("")
    env = {**os.environ, "TIRO_CONFIG_DIR": str(profile), "LOCALAPPDATA": str(local),
           "TIRO_UPDATE_FEED_URL": f"http://127.0.0.1:{port}/{{channel}}.json", "TIRO_UPDATE_FIRST_CHECK": "3",
           "TIRO_TEST_WAV": str(silence), "TIRO_TEST_TARGET_HWND_FILE": str(no_windows)}
    log = local / "Tiro" / "logs" / "tiro.log"

    def logtext() -> str:
        return log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""

    def state() -> dict:
        return json.loads((inst / "state.json").read_text())

    def launch(*args: str) -> None:
        subprocess.Popen([str(inst / "Tiro.exe"), *args], env=env)

    def quit_running() -> None:
        cur = state()["current"]
        for _ in range(3):  # (a copy that is still starting may not be listening yet: ask again)
            subprocess.run([str(inst / f"app-{cur}" / "Tiro.exe"), "--quit"], env=env, timeout=30)
            try:
                wait_for(lambda: not running(), 15, "Tiro to quit")
                return
            except AssertionError:
                continue
        raise AssertionError("timed out waiting for: Tiro to quit")

    def running() -> bool:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              f"(Get-Process Tiro -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like "
                              f"'{inst}\\app-*' }}).Count"], capture_output=True, text=True).stdout.strip()
        return out not in ("", "0")

    try:
        # --- 1. a feed signed by a key the app doesn't trust is refused
        other = os.urandom(32)
        m = {"product": "Tiro", "channel": "stable", "serial": 1, "issued_at": "2026-10-01T00:00:00Z",
             "expires_at": "2099-01-01T00:00:00Z", "platforms": {}}
        (server_dir / "stable.json").write_bytes(feed.seal(m, other, "e2e-test"))
        launch()
        wait_for(lambda: "update feed rejected" in logtext(), 120, "the untrusted feed to be refused")
        check("feed signed with an unknown key is refused", True)
        quit_running()

        # --- 2. 9.0.1 is offered, staged with only the changed pack, queued, and committed after a trial
        sign_feed([frags["9.0.1"]], seed, server_dir)
        launch()
        wait_for(lambda: "update 9.0.1 staged" in logtext(), 300, "9.0.1 to be staged")
        staged = logtext().split("update 9.0.1 staged", 1)[1].splitlines()[0]
        check("9.0.1 staged side by side", (inst / "app-9.0.1" / "Tiro.exe").is_file(), staged.strip())
        check("only changed packs downloaded", "runtime" not in staged.split("packs", 1)[1], staged.strip())
        same = sum(1 for p in (inst / "app-9.0.1").rglob("*.dll")
                   if (inst / "app-9.0.0" / p.relative_to(inst / "app-9.0.1")).exists()
                   and os.stat(p).st_ino == os.stat(inst / "app-9.0.0" / p.relative_to(inst / "app-9.0.1")).st_ino)
        check("unchanged files are hard links (no extra disk)", same > 50, f"{same} DLLs shared")
        check("queued for the next start", state()["pending"] == "9.0.1")
        quit_running()
        launch()
        wait_for(lambda: state()["current"] == "9.0.1", 300, "the 9.0.1 trial to pass")
        s = state()
        check("9.0.1 committed after its trial", s["current"] == "9.0.1" and s["previous"] == "9.0.0", str(s))
        wait_for(running, 30, "9.0.1 running")

        # --- 3. 9.0.2 crashes on its trial: tried twice, then Tiro goes back to 9.0.1 and never retries it
        sign_feed([frags["9.0.2"]], seed, server_dir)
        quit_running()
        launch()  # 9.0.1 starts, finds 9.0.2, stages and queues it
        wait_for(lambda: "update 9.0.2 staged" in logtext(), 300, "9.0.2 to be staged")
        quit_running()
        launch()
        wait_for(lambda: "9.0.2" in state()["bad"], 120, "9.0.2 to be rolled back")
        s = state()
        check("broken 9.0.2 rolled back to 9.0.1", s["current"] == "9.0.1" and s["pending"] is None, str(s))
        launcher_log = (inst / ".update" / "launcher.log").read_text()
        check("tried twice before going back", launcher_log.count("trying 9.0.2") == 2, launcher_log.strip()[-200:])
        wait_for(running, 30, "9.0.1 running again")
        quit_running()

        # --- 4. a tampered pack is refused (the feed's SHA-256 no longer matches)
        bad = server_dir / "v9.0.2"
        for z in bad.glob("*core.zip"):
            z.write_bytes(z.read_bytes()[:-10] + b"tampered!!")
        shutil.rmtree(inst / "app-9.0.2", ignore_errors=True)
        s = state()
        s["bad"] = []
        (inst / "state.json").write_text(json.dumps(s))
        for f in (local / "Tiro" / "updates" / "downloads").glob("*"):
            f.unlink()
        launch()
        wait_for(lambda: "download failed" in logtext() or "checksum mismatch" in logtext(), 300,
                 "the tampered pack to be refused")
        check("tampered pack refused, nothing queued", state()["pending"] is None and
              not (inst / "app-9.0.2").exists())
        quit_running()
    except AssertionError as exc:
        check(str(exc), False)
    finally:
        httpd.shutdown()
        if running():
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Get-Process Tiro -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like "
                            f"'{inst}\\app-*' }} | Stop-Process -Force"])
    report["ok"] = all(c["ok"] for c in report["checks"])
    (E2E / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("ALL PASSED" if report["ok"] else "SOME CHECKS FAILED", flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
