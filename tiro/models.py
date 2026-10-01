"""Model catalog (speech, voice activity, correction language models), discovery and download.

Downloads resume where they stopped (HTTP range requests on a .part file) and every large file is checked
against the SHA-256 that Hugging Face publishes for it before it is used.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tiro.paths import model_search_dirs, user_models_dir

log = logging.getLogger(__name__)

HF_BASE = "https://huggingface.co"


@dataclass(frozen=True)
class ModelSpec:
    key: str
    title: str
    subtitle: str
    repo: str
    folder: str
    files: tuple[str, ...]  # full-precision set (GPU, or fastest on CPUs without VNNI)
    size_gb: float
    int8_files: tuple[str, ...] = ()  # compact set for CPU-only installs
    int8_size_gb: float = 0.0

    def variant_files(self, variant: str) -> tuple[str, ...]:
        return self.int8_files if variant == "int8" else self.files


_TDT_FILES = (
    "config.json",
    "vocab.txt",
    "encoder-model.onnx",
    "encoder-model.onnx.data",
    "decoder_joint-model.onnx",
    "decoder_joint-model.int8.onnx",
)
_TDT_INT8_FILES = ("config.json", "vocab.txt", "encoder-model.int8.onnx", "decoder_joint-model.int8.onnx")

MODELS: dict[str, ModelSpec] = {
    spec.key: spec
    for spec in (
        ModelSpec(
            key="parakeet-tdt-0.6b-v2",
            title="Parakeet TDT 0.6B v2",
            subtitle="English · most accurate",
            repo="istupakov/parakeet-tdt-0.6b-v2-onnx",
            folder="parakeet-tdt-0.6b-v2",
            files=_TDT_FILES,
            size_gb=2.4,
            int8_files=_TDT_INT8_FILES,
            int8_size_gb=0.63,
        ),
        ModelSpec(
            key="parakeet-tdt-0.6b-v3",
            title="Parakeet TDT 0.6B v3",
            subtitle="25 European languages",
            repo="istupakov/parakeet-tdt-0.6b-v3-onnx",
            folder="parakeet-tdt-0.6b-v3",
            files=_TDT_FILES,
            size_gb=2.4,
            int8_files=_TDT_INT8_FILES,
            int8_size_gb=0.64,
        ),
    )
}
DEFAULT_MODEL = "parakeet-tdt-0.6b-v2"

_LM_COMMON = ("config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json")
# Correction language models (tier 2). `files` run on an NVIDIA GPU (4-bit weights, fp16 compute);
# `int8_files` is the CPU build (4-bit weights, fp32 compute).
LANGUAGE_MODELS: dict[str, ModelSpec] = {
    spec.key: spec
    for spec in (
        ModelSpec(
            key="small",
            title="Qwen2.5 1.5B",
            subtitle="best judgement · for GPUs",
            repo="onnx-community/Qwen2.5-1.5B-Instruct",
            folder="qwen2.5-1.5b-instruct",
            files=(*_LM_COMMON, "onnx/model_q4f16.onnx"),
            size_gb=1.23,
            int8_files=(*_LM_COMMON, "onnx/model_q4.onnx"),
            int8_size_gb=1.79,
        ),
        ModelSpec(
            key="tiny",
            title="Qwen2.5 0.5B",
            subtitle="fast · for CPUs and small GPUs",
            repo="onnx-community/Qwen2.5-0.5B-Instruct",
            folder="qwen2.5-0.5b-instruct",
            files=(*_LM_COMMON, "onnx/model_q4f16.onnx"),
            size_gb=0.49,
            int8_files=(*_LM_COMMON, "onnx/model_q4.onnx"),
            int8_size_gb=0.79,
        ),
    )
}

VAD_SPEC = ModelSpec(
    key="silero-vad",
    title="Silero VAD",
    subtitle="voice activity detection",
    repo="istupakov/silero-vad-onnx",
    folder="silero-vad",
    files=("silero_vad.onnx",),
    size_gb=0.002,
)


_MLX_FILES = ("added_tokens.json", "config.json", "merges.txt", "model.safetensors", "model.safetensors.index.json",
              "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json", "vocab.json")
# The same models for Apple Silicon, as 4-bit MLX weights (run on the Mac's GPU through Metal).
MLX_LANGUAGE_MODELS: dict[str, ModelSpec] = {
    "small": ModelSpec(key="small", title="Qwen2.5 1.5B", subtitle="best judgement · Apple GPU",
                       repo="mlx-community/Qwen2.5-1.5B-Instruct-4bit", folder="qwen2.5-1.5b-instruct-mlx",
                       files=_MLX_FILES, size_gb=0.88),
    "tiny": ModelSpec(key="tiny", title="Qwen2.5 0.5B", subtitle="fast · Apple GPU",
                      repo="mlx-community/Qwen2.5-0.5B-Instruct-4bit", folder="qwen2.5-0.5b-instruct-mlx",
                      files=_MLX_FILES, size_gb=0.29),
}


def vad_model_path() -> Path:
    """Silero VAD ships with the app (assets/vad, 2 MB); older layouts kept it under models/."""
    from tiro.paths import asset

    bundled = asset("vad", "silero_vad.onnx")
    if bundled.is_file():
        return bundled
    folder = find_model(VAD_SPEC)
    if folder is None:
        raise RuntimeError("Silero VAD model missing (assets/vad/silero_vad.onnx)")
    return folder / "silero_vad.onnx"


def find_model(spec: ModelSpec, variant: str | None = None) -> Path | None:
    """Return the first folder that holds a complete copy of the model (the given variant, or either)."""
    sets = [spec.variant_files(variant)] if variant else [spec.files, spec.int8_files]
    for base in model_search_dirs():
        folder = base / spec.folder
        for files in sets:
            if files and all((folder / name).is_file() for name in files):
                return folder
    return None


def is_installed(spec: ModelSpec) -> bool:
    return find_model(spec) is not None


class DownloadCancelled(Exception):
    pass


def _head(url: str) -> int:
    """Size of a file on Hugging Face."""
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return int(resp.headers.get("X-Linked-Size") or resp.headers.get("Content-Length") or 0)


def remote_files(spec: ModelSpec, files: tuple[str, ...]) -> dict[str, tuple[int, str | None]]:
    """{path: (size, sha256 or None)} from the repository's file listing (large files list their SHA-256)."""
    import json

    out: dict[str, tuple[int, str | None]] = {}
    for folder in sorted({f.rpartition("/")[0] for f in files}):
        url = f"{HF_BASE}/api/models/{spec.repo}/tree/main" + (f"/{folder}" if folder else "")
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                for entry in json.load(resp):
                    lfs = entry.get("lfs") or {}
                    sha = lfs.get("oid") if re.fullmatch(r"[0-9a-f]{64}", str(lfs.get("oid", ""))) else None
                    out[entry["path"]] = (int(entry.get("size") or lfs.get("size") or 0), sha)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            log.warning("could not list %s (%s); checking sizes only", url, exc)
    for name in files:
        if name not in out:
            out[name] = (_head(f"{HF_BASE}/{spec.repo}/resolve/main/{name}"), None)
    return out


def _sha256(path: Path, h=None):
    h = h or hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h


def download_file(url: str, dest: Path, size: int, sha: str | None, progress: Callable[[int], None] | None = None,
                  cancel: threading.Event | None = None, retries: int = 4) -> None:
    """Download one file with resume (a .part file and HTTP ranges), retries and verification."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(retries + 1):
        have = part.stat().st_size if part.is_file() else 0
        if size and have > size:
            part.unlink()
            have = 0
        try:
            if not size or have < size:
                log.info("Downloading %s%s", url, f" (resuming at {have >> 20} MB)" if have else "")
                req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
                with urllib.request.urlopen(req, timeout=60) as resp, part.open("ab" if have else "wb") as out:
                    if have and resp.status != 206:  # the server ignored the range: start over
                        out.truncate(0)
                        have = 0
                    got = have
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise DownloadCancelled
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                        got += len(chunk)
                        if progress:
                            progress(got)
            if size and part.stat().st_size != size:
                raise OSError(f"{dest.name}: got {part.stat().st_size} of {size} bytes")
            if sha and _sha256(part).hexdigest() != sha:
                part.unlink()
                raise OSError(f"{dest.name}: checksum mismatch (download corrupted)")
            shutil.move(part, dest)
            return
        except DownloadCancelled:
            raise
        except (OSError, urllib.error.URLError) as exc:
            if attempt == retries:
                raise
            log.warning("download of %s interrupted (%s); retrying", dest.name, exc)
            time.sleep(min(30, 2 ** attempt))


def download_model(
    spec: ModelSpec,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    variant: str = "fp32",
    retries: int = 4,
) -> Path:
    """Download a model from Hugging Face into the per-user models folder and return the folder.

    Interrupted downloads resume from their .part file; files are verified (size, and SHA-256 where
    Hugging Face provides it) before being moved into place."""
    target = user_models_dir() / spec.folder
    files = spec.variant_files(variant)
    info = remote_files(spec, files)
    total = sum(size for size, _ in info.values())
    done = 0
    for name in files:
        size, sha = info[name]
        dest = target / name
        if dest.is_file() and dest.stat().st_size == size:
            done += size
            if progress:
                progress(done, total)
            continue
        base = done
        download_file(f"{HF_BASE}/{spec.repo}/resolve/main/{name}", dest, size, sha,
                      (lambda got, base=base: progress(base + got, total)) if progress else None, cancel, retries)
        done += size
    return target
