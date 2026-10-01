"""Phase A1 latency test on real windows: how long after the key is released does the text finish appearing?

    python dev/latency_e2e.py [--exe Tiro.exe] [--targets notepad,term,claude,browser] [--runs 6]
                              [--insertion type|paste] [--wav file.wav] [--idle-first 0] [--tag name]

Safety (see the testing rules): Tiro runs with an isolated profile, the F24 hotkey (no physical keyboard has
it), a WAV file as its microphone, and a guard that drops text unless the foreground window is one this test
opened (TIRO_TEST_TARGET_HWND_FILE). The Claude Code target runs `claude --permission-mode plan` (read-only) and
no Enter is ever sent; the window is closed afterwards.

For every dictation it records the time from key release to the last character landing (polled every 5 ms)
plus Tiro's own stage timing (TIRO_TIMING_LOG). Results go to work/results/latency/<tag>.json.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import wave
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tiro.injector import INPUT, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
WM_GETTEXT, WM_GETTEXTLENGTH, WM_SETTEXT, WM_CLOSE, EM_SETMODIFY = 0x000D, 0x000E, 0x000C, 0x0010, 0x00B9
LOG = Path(os.environ["LOCALAPPDATA"]) / "Tiro" / "logs" / "tiro.log"
F24 = 0x87
WORKDIR = ROOT / "work" / "results" / "latency"
CLAUDE_CWD = os.environ.get("TIRO_TEST_CLAUDE_CWD", "D:\\")


def key(vk: int, up: bool) -> None:
    inp = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), KEYEVENTF_KEYUP if up else 0, 0, 0))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def tap(vk: int, hold: float = 0.03) -> None:
    key(vk, False)
    time.sleep(hold)
    key(vk, True)


def title_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def windows_of_pid(pid: int) -> list[int]:
    found = []

    def cb(hwnd, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def find_title(prefix: str, timeout: float = 15) -> int:
    end = time.time() + timeout
    while time.time() < end:
        found = []

        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd) and title_of(hwnd).startswith(prefix):
                found.append(hwnd)
            return True

        user32.EnumWindows(EnumWindowsProc(cb), 0)
        if found:
            return found[0]
        time.sleep(0.1)
    return 0


def focus(hwnd: int) -> bool:
    tap(0xE8, 0.01)  # any input event grants the right to set the foreground window
    user32.ShowWindow(hwnd, 5)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    return user32.GetForegroundWindow() == hwnd


def wait_log(marker: str, since: int, timeout: float = 120) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if LOG.exists():
            with LOG.open(encoding="utf-8", errors="replace") as f:
                f.seek(since)
                if marker in f.read():
                    return True
        time.sleep(0.25)
    return False


# ---------------------------------------------------------------- targets: open, read, clear, close
class Target:
    name = ""
    hwnd = 0

    def open(self) -> None: ...
    def read(self) -> str: ...
    def clear(self) -> None: ...
    def close(self) -> None: ...


class Notepad(Target):
    name = "notepad"

    def open(self):
        path = WORKDIR / "TIRO-TEST.txt"
        path.write_text("", encoding="utf-8")
        self.proc = subprocess.Popen(["notepad.exe", str(path)])
        self.hwnd = find_title("TIRO-TEST.txt")
        self.edit = user32.FindWindowExW(self.hwnd, None, "Edit", None) or \
            user32.FindWindowExW(self.hwnd, None, "RichEditD2DPT", None)

    def read(self):
        n = user32.SendMessageW(self.edit, WM_GETTEXTLENGTH, 0, 0)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.SendMessageW(self.edit, WM_GETTEXT, n + 1, ctypes.addressof(buf))
        return buf.value

    def clear(self):
        user32.SendMessageW(self.edit, WM_SETTEXT, 0, ctypes.addressof(ctypes.create_unicode_buffer("")))

    def close(self):
        user32.SendMessageW(self.edit, EM_SETMODIFY, 0, 0)
        user32.SendMessageW(self.hwnd, WM_CLOSE, 0, 0)


class TermReader(Target):
    """A plain console program: measures how fast keystrokes reach a console app (no TUI rendering)."""
    name = "term"

    def open(self):
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "dev" / "latency" / "term_reader.py")],
                                     creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.hwnd = find_title("TIRO-TEST-TERM")
        self.base = 0

    def read(self):
        t = title_of(self.hwnd)
        try:
            return "x" * (int(t.rsplit(" ", 1)[1]) - self.base)
        except (ValueError, IndexError):
            return ""

    def clear(self):
        t = title_of(self.hwnd)
        try:
            self.base = int(t.rsplit(" ", 1)[1])
        except (ValueError, IndexError):
            pass

    def close(self):
        self.proc.kill()


class ClaudeCode(Target):
    """The real Claude Code CLI in a console window, read-only (plan mode); text is never submitted."""
    name = "claude"

    BLOCKERS = ("trust this folder", "Do you trust", "Quick safety check", "Enter to confirm")

    def open(self):
        # D:\ is a folder this user already trusts in Claude Code (so no trust question appears); plan mode is
        # read-only, and nothing typed is ever submitted
        self.proc = subprocess.Popen(["cmd.exe", "/k", "title TIRO-TEST-CLAUDE && claude --permission-mode plan"],
                                     cwd=CLAUDE_CWD, creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.hwnd = find_title("TIRO-TEST-CLAUDE", 20) or (windows_of_pid(self.proc.pid) or [0])[0]
        time.sleep(6)  # let the CLI start and draw its input box
        self.peek = subprocess.Popen([sys.executable, str(ROOT / "dev" / "latency" / "console_peek.py"),
                                      str(self.proc.pid), "5"], stdout=subprocess.PIPE, text=True,
                                     encoding="utf-8")
        self._text = ""
        self._lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()
        time.sleep(0.5)
        self.base = self._text
        if self.blocked():
            # A question (e.g. "do you trust this folder?") is on screen. Typed text must never answer it:
            # give up on this target. The folder's trust is the user's decision, never the test's.
            print("claude: Claude Code is asking a question (folder trust?); target skipped", flush=True)
            with self._lock:
                (WORKDIR / "claude-screen-blocked.txt").write_text(self._text, encoding="utf-8")
            self.hwnd = 0

    def blocked(self) -> bool:
        with self._lock:
            screen = self._text
        return any(b in screen for b in self.BLOCKERS)

    def _pump(self):
        for line in self.peek.stdout:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            with self._lock:
                self._text = rec.get("text", "")

    def read(self):
        with self._lock:
            screen = self._text
        # what changed on screen since the dictation started (the input box content)
        return "".join(screen.split()) if screen != self.base else ""

    def clear(self):
        with self._lock:
            self.base = self._text

    def close(self):
        with self._lock:
            (WORKDIR / "claude-screen.txt").write_text(self._text, encoding="utf-8")
        self.peek.kill()
        subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)


class Browser(Target):
    """A Chrome text box. The page reports its length to a local server after each change is painted
    (requestAnimationFrame), so the time is when the text was on screen, not when a title changed."""
    name = "browser"

    def open(self):
        import http.server

        target = self
        self.length = 0
        self.base = 0
        page = (ROOT / "dev" / "latency" / "browser.html").read_bytes()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/len?n="):
                    try:
                        target.length = int(self.path.split("=", 1)[1])
                    except ValueError:
                        pass
                    body, ctype = b"", "text/plain"
                else:
                    body, ctype = page, "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        exe = chrome if chrome.exists() else Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
        self.profile = Path(tempfile.mkdtemp(prefix="tiro-latency-browser-"))
        self.proc = subprocess.Popen([str(exe), f"--app={url}", f"--user-data-dir={self.profile}",
                                      "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                                      "--window-size=900,700"])
        self.hwnd = find_title("TIRO-TEST-BROWSER", 20)

    def read(self):
        return "x" * max(0, self.length - self.base)

    def clear(self):
        self.base = self.length

    def close(self):
        subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)
        self.server.shutdown()


TARGETS = {t.name: t for t in (Notepad, TermReader, ClaudeCode, Browser)}


def last_timing(path: Path, n_before: int) -> dict | None:
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1]) if len(lines) > n_before else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe")
    ap.add_argument("--targets", default="notepad,term,claude,browser")
    ap.add_argument("--runs", type=int, default=6)
    ap.add_argument("--insertion", default="type")
    ap.add_argument("--wav", default=str(ROOT / "tests" / "accuracy" / "audio" / "noharm" / "nh-001_zira.wav"))
    ap.add_argument("--release-after", type=float, default=0.15, help="seconds after the audio ends")
    ap.add_argument("--idle-first", type=float, default=0, help="idle this long before the first dictation")
    ap.add_argument("--settle", type=float, default=0.8)
    ap.add_argument("--tag", default="")
    ap.add_argument("--extra-settings", default="{}")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="profile setting (true/false/numbers understood), e.g. --set device=cpu")
    ap.add_argument("--first-immediately", action="store_true",
                    help="start the first dictation as soon as the speech model is ready (language model still loading)")
    a = ap.parse_args()

    WORKDIR.mkdir(parents=True, exist_ok=True)
    with wave.open(a.wav) as w:
        duration = w.getnframes() / w.getframerate()
    profile = ROOT / "dev" / "latency_profile"
    profile.mkdir(exist_ok=True)
    settings = {"hotkey": "f24", "mode": "hold", "double_tap_lock": False, "sounds": False,
                "welcome_shown": True, "setup_done": True, "insertion": a.insertion, "history": False,
                "auto_update_check": False, "whats_new_seen": "test"}
    settings.update(json.loads(a.extra_settings))
    for item in a.set:
        k, _, v = item.partition("=")
        settings[k] = {"true": True, "false": False}.get(v.lower(), int(v) if v.isdigit() else v)
    (profile / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    hwnd_file = WORKDIR / "allowed_hwnds.txt"
    hwnd_file.write_text("", encoding="utf-8")
    timing = WORKDIR / f"timing-{a.tag or 'run'}.jsonl"
    timing.unlink(missing_ok=True)
    env = os.environ.copy()
    env.update(TIRO_TEST_WAV=a.wav, TIRO_CONFIG_DIR=str(profile), TIRO_TEST_TARGET_HWND_FILE=str(hwnd_file),
               TIRO_TIMING_LOG=str(timing))
    since = LOG.stat().st_size if LOG.exists() else 0
    cmd = [a.exe, "--verbose"] if a.exe else [str(ROOT / ".venv" / "Scripts" / "pythonw.exe"),
                                              str(ROOT / "run_tiro.pyw"), "--verbose"]
    t0 = time.time()
    tiro = subprocess.Popen(cmd, env=env, cwd=str(ROOT))
    if not wait_log("ASR ready", since):
        print("FAIL: Tiro did not report the model ready")
        tiro.kill()
        return 1
    wait_log("correction knowledge", since, timeout=40)
    settings_now = json.loads((profile / "settings.json").read_text(encoding="utf-8"))
    if settings_now.get("ai_correction", True) and settings_now.get("correction", True) and not a.first_immediately:
        wait_log("instruct ready on", since, timeout=90)  # the language model too, as after a normal start
    print(f"Tiro ready after {time.time() - t0:.1f}s", flush=True)
    results = {"wav": a.wav, "duration": duration, "insertion": a.insertion, "targets": {}}
    try:
        for name in a.targets.split(","):
            tgt = TARGETS[name]()
            tgt.open()
            if not tgt.hwnd:
                print(f"{name}: window not found, skipped")
                try:
                    tgt.close()  # never leave a test window behind
                except Exception as exc:  # noqa: BLE001
                    print(f"{name}: close failed: {exc}")
                continue
            with open(hwnd_file, "a", encoding="utf-8") as f:
                f.write(f"{tgt.hwnd}\n")
            runs = []
            for r in range(a.runs):
                if r == 0 and a.idle_first:
                    time.sleep(a.idle_first)
                if not focus(tgt.hwnd):
                    print(f"{name} run {r}: could not focus the window")
                tgt.clear()
                n_before = len(timing.read_text(encoding="utf-8").splitlines()) if timing.exists() else 0
                key(F24, False)
                time.sleep(duration + a.release_after)
                t_release = time.time()
                key(F24, True)
                last, t_last = tgt.read(), None
                end = time.time() + 8
                t_record = None  # when Tiro logged this dictation as finished
                while time.time() < end:
                    cur = tgt.read()
                    now = time.time()
                    if cur != last:
                        last, t_last = cur, now
                    if t_record is None and last_timing(timing, n_before) is not None:
                        t_record = now
                    # done once Tiro has finished AND the screen has been still for `settle` seconds since
                    if t_record is not None and now - max(t_record, t_last or 0) > a.settle:
                        break
                    time.sleep(0.005)
                if isinstance(tgt, ClaudeCode) and tgt.blocked():
                    print("claude: a question appeared on screen; stopping this target", flush=True)
                    break
                tim = last_timing(timing, n_before)
                run = {"visible_ms": round((t_last - t_release) * 1000, 1) if t_last else None,
                       "chars": len(last), "tiro": tim}
                runs.append(run)
                print(f"{name} run {r}: text done {run['visible_ms']} ms after release; tiro {tim}", flush=True)
                time.sleep(1.0)
            tgt.close()
            vis = [x["visible_ms"] for x in runs if x["visible_ms"] is not None]
            summary = {"runs": runs}
            if vis:
                summary["median_ms"] = statistics.median(vis)
                summary["p95_ms"] = sorted(vis)[max(0, int(round(0.95 * len(vis))) - 1)]
            results["targets"][name] = summary
            print(f"== {name}: median {summary.get('median_ms')} ms, p95 {summary.get('p95_ms')} ms", flush=True)
    finally:
        subprocess.run(cmd[:-1] + ["--quit"], env=env, cwd=str(ROOT), timeout=30)
        try:
            tiro.wait(15)
        except subprocess.TimeoutExpired:
            tiro.kill()
    out = WORKDIR / f"{a.tag or 'latency'}.json"
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
