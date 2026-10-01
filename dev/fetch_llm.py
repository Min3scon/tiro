"""Download the correction language models (ONNX) into models/ for development."""
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

ROOT = Path(__file__).resolve().parents[1]
WANT = {
    "qwen2.5-1.5b-instruct": ("onnx-community/Qwen2.5-1.5B-Instruct", ["onnx/model_q4f16.onnx"]),
    "qwen2.5-0.5b-instruct": ("onnx-community/Qwen2.5-0.5B-Instruct", ["onnx/model_q4f16.onnx", "onnx/model_q4.onnx"]),
}
COMMON = ["config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json"]
for folder, (repo, files) in WANT.items():
    if len(sys.argv) > 1 and folder not in sys.argv[1:]:
        continue
    dest = ROOT / "models" / folder
    for f in COMMON + files:
        path = hf_hub_download(repo, f, local_dir=dest)
        print(folder, f, Path(path).stat().st_size // (1 << 20), "MB", flush=True)
print("done")
