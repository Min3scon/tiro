"""List files (with sizes) under a folder of Hugging Face model repos."""
import json
import sys
import urllib.request

repos = sys.argv[1:] or ["onnx-community/Qwen2.5-0.5B-Instruct", "onnx-community/Qwen2.5-1.5B-Instruct"]
for repo in repos:
    name, _, sub = repo.partition(":")
    url = f"https://huggingface.co/api/models/{name}/tree/main/{sub or 'onnx'}"
    print("==", repo)
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            for f in json.load(r):
                if f.get("type") == "file":
                    print(f"   {f['path']:52s} {f.get('size', 0) / 1e6:9.1f} MB")
    except Exception as exc:
        print("   error:", exc)
