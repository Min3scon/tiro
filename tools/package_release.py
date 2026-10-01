"""Split the PyInstaller build into release downloads and write the installer manifest.

  release/Tiro-<v>-app-win64.zip          the app (Python runtime, Qt, ONNX Runtime CPU, VAD model)
  release/Tiro-<v>-gpu-runtime-win64.zip  ONNX Runtime CUDA provider + CUDA 13 / cuDNN 9 runtime DLLs
  release/manifest.json                   URLs, sizes and SHA-256 of everything the installer downloads,
                                          including the speech models on Hugging Face (pinned revisions)

The manifest is also copied into the installer project, which embeds it.
Usage: python tools/package_release.py [--repo Min3scon/tiro]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tiro import __version__
from tiro.models import MODELS

DIST = ROOT / "build" / "dist" / "Tiro"  # (--dist / --out for test builds)
OUT = ROOT / "release"
GPU_PARTS = ("_internal/cuda/", "_internal/onnxruntime/capi/onnxruntime_providers_cuda.dll")
DROP = ("models/", "_internal/onnxruntime/capi/onnxruntime_providers_tensorrt.dll")
KEEP_MODELS = ()  # (the VAD model ships in assets/vad)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build_zips() -> tuple[Path, Path, int, int]:
    OUT.mkdir(exist_ok=True)
    app_zip = OUT / f"Tiro-{__version__}-app-win64.zip"
    gpu_zip = OUT / f"Tiro-{__version__}-gpu-runtime-win64.zip"
    app_bytes = gpu_bytes = 0
    with zipfile.ZipFile(app_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as app, zipfile.ZipFile(
        gpu_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6
    ) as gpu:
        for f in sorted(DIST.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(DIST).as_posix()
            if any(rel.startswith(p) for p in GPU_PARTS):
                gpu.write(f, rel)
                gpu_bytes += f.stat().st_size
            elif any(rel.startswith(p) for p in KEEP_MODELS) or not any(rel.startswith(p) for p in DROP):
                app.write(f, rel)
                app_bytes += f.stat().st_size
    return app_zip, gpu_zip, app_bytes, gpu_bytes


CORE_PARTS = ("Tiro.exe", "_internal/base_library.zip", "_internal/assets/", "_internal/tiro_")


def pack_of(rel: str) -> str:
    """Which update pack a file travels in: Tiro's own code changes often, the runtime and GPU parts rarely."""
    if any(rel.startswith(p) for p in GPU_PARTS):
        return "gpu"
    if any(rel == p or (p.endswith(("/", "_")) and rel.startswith(p)) for p in CORE_PARTS):
        return "core"
    return "runtime"


def build_update_packs() -> tuple[Path, Path, Path, dict]:
    """The in-app updater's files: an inventory of every installed file and the core and runtime packs (the
    GPU pack is the GPU runtime zip). Writes the inventory into the build too (.tiro/files.json), so an
    installed copy can reuse its unchanged files in the next update. Run before build_zips()."""
    files = []
    core_zip = OUT / f"Tiro-{__version__}-win64-core.zip"
    runtime_zip = OUT / f"Tiro-{__version__}-win64-runtime.zip"
    with zipfile.ZipFile(core_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as core, zipfile.ZipFile(
        runtime_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9
    ) as runtime:
        for f in sorted(DIST.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(DIST).as_posix()
            if rel.startswith(".tiro/") or (any(rel.startswith(p) for p in DROP) and
                                              not any(rel.startswith(p) for p in KEEP_MODELS)):
                continue
            pack = pack_of(rel)
            files.append({"path": rel, "size": f.stat().st_size, "sha256": sha256(f), "pack": pack})
            if pack == "core":
                core.write(f, rel)
            elif pack == "runtime":
                runtime.write(f, rel)
    inventory = {"version": __version__, "files": files}
    inv_path = OUT / f"Tiro-{__version__}-win64-files.json"
    inv_path.write_text(json.dumps(inventory), encoding="utf-8")
    # the installed copy carries its own inventory, so the next update can reuse unchanged files
    (DIST / ".tiro").mkdir(exist_ok=True)
    (DIST / ".tiro" / "files.json").write_text(json.dumps(inventory), encoding="utf-8")

    by_pack = {p: sum(f["size"] for f in files if f["pack"] == p) for p in ("core", "runtime", "gpu")}
    print("update packs: " + ", ".join(f"{p} {n / (1 << 20):.0f} MB" for p, n in by_pack.items()))
    return inv_path, core_zip, runtime_zip, inventory


def write_fragment(base: str, inv_path: Path, core_zip: Path, runtime_zip: Path, gpu_zip: Path, summary: str,
                   notes_url: str) -> Path:
    """This platform's entry for the signed feed (combined and signed by tools/feed.py in the release job)."""

    def asset(p: Path) -> dict:
        return {"url": f"{base}/{p.name}", "size": p.stat().st_size, "sha256": sha256(p)}

    entry = {"version": __version__, "summary": summary, "notes_url": notes_url, "critical": False,
             "min_os": "10.0.17763", "inventory": asset(inv_path),
             "packs": {"core": asset(core_zip), "runtime": asset(runtime_zip), "gpu": asset(gpu_zip)}}
    frag = OUT / "update-fragment-win-x64.json"
    frag.write_text(json.dumps({"platform": "win-x64", "entry": entry}, indent=1), encoding="utf-8")
    return frag


def changelog_summary(version: str) -> str:
    """The first line of this version's section in CHANGELOG.md (shown in the update notification)."""
    path = ROOT / "CHANGELOG.md"
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## ") and version in line:
            for nxt in lines[i + 1:]:
                if nxt.startswith("## "):
                    break
                text = nxt.strip().lstrip("-* ").strip()
                if text:
                    return text
    return ""


def hf_files(repo: str) -> tuple[str, dict[str, dict]]:
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}", timeout=30) as r:
        revision = json.load(r)["sha"]
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}/tree/{revision}", timeout=30) as r:
        tree = json.load(r)
    files = {}
    for item in tree:
        if item.get("type") != "file":
            continue
        lfs = item.get("lfs") or {}
        files[item["path"]] = {"size": item["size"], "sha256": lfs.get("oid", "")}
    return revision, files


def main() -> None:
    global DIST, OUT, __version__
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="Min3scon/tiro")
    ap.add_argument("--dist", type=Path, help="the PyInstaller folder (default build/dist/Tiro)")
    ap.add_argument("--out", type=Path, help="where release files go (default release/)")
    ap.add_argument("--base-url", help="download address of this release's files (tests: a local server)")
    ap.add_argument("--no-models", action="store_true", help="skip the Hugging Face model entries (offline tests)")
    ap.add_argument("--version", help="the build's version when it isn't the source's (test builds)")
    args = ap.parse_args()
    DIST, OUT = args.dist or DIST, args.out or OUT
    __version__ = args.version or __version__
    if not (DIST / "Tiro.exe").is_file():
        sys.exit("build first: tools/build.ps1")

    base = args.base_url or f"https://github.com/{args.repo}/releases/download/v{__version__}"
    OUT.mkdir(parents=True, exist_ok=True)
    inv_path, core_zip, runtime_zip, _inventory = build_update_packs()
    app_zip, gpu_zip, app_bytes, gpu_bytes = build_zips()  # the app zip now carries .tiro/files.json
    write_fragment(base, inv_path, core_zip, runtime_zip, gpu_zip, changelog_summary(__version__),
                   f"https://github.com/{args.repo}/releases/tag/v{__version__}")
    manifest: dict = {
        "version": __version__,
        "repo": args.repo,
        "app": {"url": f"{base}/{app_zip.name}", "size": app_zip.stat().st_size, "sha256": sha256(app_zip),
                "installed_bytes": app_bytes},
        "gpu": {"url": f"{base}/{gpu_zip.name}", "size": gpu_zip.stat().st_size, "sha256": sha256(gpu_zip),
                "installed_bytes": gpu_bytes, "cuda": "13.0", "min_driver": 580, "min_compute": [7, 5]},
        "models": {},
    }
    for spec in () if args.no_models else MODELS.values():
        revision, files = hf_files(spec.repo)
        entry = {"title": spec.title, "subtitle": spec.subtitle, "folder": spec.folder, "repo": spec.repo,
                 "revision": revision}
        for variant, names in (("fp32", spec.files), ("int8", spec.int8_files)):
            entry[variant] = [
                {"name": n, "url": f"https://huggingface.co/{spec.repo}/resolve/{revision}/{n}",
                 "size": files[n]["size"], "sha256": files[n]["sha256"]}
                for n in names
            ]
        manifest["models"][spec.key] = entry
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if args.out is None:
        shutil.copy2(OUT / "manifest.json", ROOT / "installer" / "TiroSetup" / "manifest.json")
    gb = 1 << 30
    print(f"app zip  {app_zip.stat().st_size / (1 << 20):7.1f} MB  ({app_bytes / (1 << 20):.0f} MB installed)")
    print(f"gpu zip  {gpu_zip.stat().st_size / (1 << 20):7.1f} MB  ({gpu_bytes / (1 << 20):.0f} MB installed)")
    for key, m in manifest["models"].items():
        for v in ("fp32", "int8"):
            print(f"{key} {v}: {sum(f['size'] for f in m[v]) / gb:.2f} GB")


if __name__ == "__main__":
    main()
