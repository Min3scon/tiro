"""End-to-end test of TiroSetup on the versioned layout, in a temporary folder (run after dev/update_e2e.py, which
makes the test builds and packages; nothing is published and Windows integration is left alone: --no-shell).

  1. Fresh install of 9.0.0: launcher at the top, app-9.0.0, state.json, self-test passes.
  2. An old flat install (Tiro.exe + _internal directly in the folder, like 2.0.x) upgraded to 9.0.1: the old
     files move into their own app-* folder and become "previous"; the launcher takes Tiro.exe's place.

    python dev/installer_e2e.py
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E2E = ROOT / "work" / "installer-e2e"
SERVER = ROOT / "work" / "update-e2e" / "server"
SETUP = ROOT / "installer" / "out" / "setup-test" / "TiroSetup.exe"
CPU_MODEL = ROOT / "models" / "parakeet-tdt-0.6b-v2"


def manifest_for(version: str, port: int) -> Path:
    """The test package's app/gpu entries + the real model list (the models are pre-linked, so nothing downloads)."""
    test = json.loads((SERVER / f"v{version}" / "manifest.json").read_text(encoding="utf-8"))
    real = json.loads((ROOT / "installer" / "TiroSetup" / "manifest.json").read_text(encoding="utf-8"))
    test["models"] = real["models"]
    out = E2E / f"manifest-{version}.json"
    out.write_text(json.dumps(test), encoding="utf-8")
    return out


def prelink_models(root: Path) -> None:
    dst = root / "models" / "parakeet-tdt-0.6b-v2"
    dst.mkdir(parents=True, exist_ok=True)
    for f in CPU_MODEL.iterdir():
        if f.is_file() and not (dst / f.name).exists():
            os.link(f, dst / f.name)


def setup(version: str, root: Path, port: int, env: dict) -> int:
    log = E2E / f"setup-{version}.log"
    e = {**env, "TIRO_SETUP_MANIFEST": str(manifest_for(version, port))}
    p = subprocess.run([str(SETUP), "--silent", "--cpu", "--no-shell", "--dir", str(root), "--log", str(log)],
                       env=e, timeout=900, check=False)
    print(log.read_text(encoding="utf-8", errors="replace")[-1500:])
    return p.returncode


def main() -> int:
    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""), flush=True)

    if E2E.exists():
        shutil.rmtree(E2E)
    E2E.mkdir(parents=True)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(SERVER)))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    # the packages were made for update_e2e's server port: point the manifests at this one
    for mf in SERVER.glob("v*/manifest.json"):
        m = json.loads(mf.read_text(encoding="utf-8"))
        for key in ("app", "gpu"):
            url = m[key]["url"]
            m[key]["url"] = f"http://127.0.0.1:{port}/" + url.split("/", 3)[3]
        mf.write_text(json.dumps(m), encoding="utf-8")
    profile = E2E / "profile"
    profile.mkdir()
    (profile / "settings.json").write_text(json.dumps({"hotkey": "f24", "setup_done": True, "device": "cpu"}))
    local = E2E / "localappdata"
    local.mkdir()
    env = {**os.environ, "TIRO_CONFIG_DIR": str(profile), "LOCALAPPDATA": str(local)}
    try:
        # 1. fresh install
        fresh = E2E / "fresh" / "Tiro"
        prelink_models(fresh)
        code = setup("9.0.0", fresh, port, env)
        state = json.loads((fresh / "state.json").read_text()) if (fresh / "state.json").exists() else {}
        check("fresh install: exit code 0 (self-test passed)", code == 0, f"code {code}")
        check("fresh install: versioned layout", (fresh / "app-9.0.0" / "Tiro.exe").is_file() and
              state.get("current") == "9.0.0", json.dumps(state))
        launcher = (fresh / "Tiro.exe").read_bytes()
        check("fresh install: launcher at the top", launcher == (ROOT / "installer" / "out" / "launcher" /
                                                                 "Tiro.exe").read_bytes())
        check("fresh install: no flat leftovers", not (fresh / "_internal").exists())

        # 2. upgrade an old flat install
        flat = E2E / "flat" / "Tiro"
        flat.mkdir(parents=True)
        build = ROOT / "build" / "e2e-9.0.0" / "Tiro"
        shutil.copy2(build / "Tiro.exe", flat / "Tiro.exe")
        shutil.copytree(build / "_internal", flat / "_internal")
        prelink_models(flat)
        code = setup("9.0.1", flat, port, env)
        state = json.loads((flat / "state.json").read_text()) if (flat / "state.json").exists() else {}
        olds = [d.name for d in flat.glob("app-*") if d.name != "app-9.0.1"]
        check("flat upgrade: exit code 0", code == 0, f"code {code}")
        check("flat upgrade: old version moved aside and kept as previous",
              len(olds) == 1 and (flat / olds[0] / "Tiro.exe").is_file() and state.get("previous") == olds[0][4:],
              f"{olds} {json.dumps(state)}")
        check("flat upgrade: new version current", state.get("current") == "9.0.1" and
              (flat / "app-9.0.1" / "Tiro.exe").is_file())
        check("flat upgrade: models untouched", (flat / "models" / "parakeet-tdt-0.6b-v2").is_dir())
    finally:
        httpd.shutdown()
    ok = all(c["ok"] for c in checks)
    (E2E / "report.json").write_text(json.dumps({"ok": ok, "checks": checks}, indent=1), encoding="utf-8")
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
