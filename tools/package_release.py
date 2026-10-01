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

from tiro import __version__  # noqa: E402
from tiro.models import MODELS  # noqa: E402

DIST = ROOT / "build" / "dist" / "Tiro"
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="Min3scon/tiro")
    args = ap.parse_args()
    if not (DIST / "Tiro.exe").is_file():
        sys.exit("build first: tools/build.ps1")

    app_zip, gpu_zip, app_bytes, gpu_bytes = build_zips()
    base = f"https://github.com/{args.repo}/releases/download/v{__version__}"
    manifest: dict = {
        "version": __version__,
        "repo": args.repo,
        "app": {"url": f"{base}/{app_zip.name}", "size": app_zip.stat().st_size, "sha256": sha256(app_zip),
                "installed_bytes": app_bytes},
        "gpu": {"url": f"{base}/{gpu_zip.name}", "size": gpu_zip.stat().st_size, "sha256": sha256(gpu_zip),
                "installed_bytes": gpu_bytes, "cuda": "13.0", "min_driver": 580, "min_compute": [7, 5]},
        "models": {},
    }
    for spec in MODELS.values():
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
    shutil.copy2(OUT / "manifest.json", ROOT / "installer" / "TiroSetup" / "manifest.json")
    gb = 1 << 30
    print(f"app zip  {app_zip.stat().st_size / (1 << 20):7.1f} MB  ({app_bytes / (1 << 20):.0f} MB installed)")
    print(f"gpu zip  {gpu_zip.stat().st_size / (1 << 20):7.1f} MB  ({gpu_bytes / (1 << 20):.0f} MB installed)")
    for key, m in manifest["models"].items():
        for v in ("fp32", "int8"):
            print(f"{key} {v}: {sum(f['size'] for f in m[v]) / gb:.2f} GB")


if __name__ == "__main__":
    main()
