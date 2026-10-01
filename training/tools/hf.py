"""Small Hugging Face Hub query helper (no login needed for public repos).

    python training/tools/hf.py models --author moonshine-ai [--search streaming]
    python training/tools/hf.py datasets --search librispeech
    python training/tools/hf.py tree moonshine-ai/moonshine-streaming-tiny[:subdir] [--dataset]
    python training/tools/hf.py info nvidia/parakeet-unified-en-0.6b [--dataset]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request

API = "https://huggingface.co/api"


def get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "tiro-training/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def cmd_list(kind: str, args) -> None:
    q = {"limit": str(args.limit), "full": "false"}
    if args.author:
        q["author"] = args.author
    if args.search:
        q["search"] = args.search
    if args.sort:
        q["sort"] = args.sort
    for m in get(f"{API}/{kind}?{urllib.parse.urlencode(q)}"):
        modified = (m.get("lastModified") or m.get("createdAt") or "")[:10]
        extra = m.get("pipeline_tag") or ""
        print(f"{m['id']:70s} {modified}  dl={m.get('downloads', '?'):>8}  {extra}")


def cmd_tree(args) -> None:
    for repo in args.repo:
        name, _, sub = repo.partition(":")
        kind = "datasets" if args.dataset else "models"
        url = f"{API}/{kind}/{name}/tree/main/{sub}?recursive={'true' if args.recursive else 'false'}"
        print("==", repo)
        total = 0
        try:
            files = get(url)
        except urllib.error.HTTPError as exc:
            print("   HTTP error:", exc.code, exc.reason)
            continue
        for f in files[: args.max]:
            size = f.get("size", 0) or 0
            if f.get("lfs"):
                size = f["lfs"].get("size", size)
            total += size
            print(f"  {f['type']:4s} {f['path']:80s} {size / 1e6:10.1f} MB")
        if len(files) > args.max:
            print(f"  ... {len(files) - args.max} more entries")
        print(f"  total {total / 1e9:.2f} GB (listed entries)")


def cmd_info(args) -> None:
    kind = "datasets" if args.dataset else "models"
    info = get(f"{API}/{kind}/{args.repo}")
    keep = {k: info.get(k) for k in ("id", "lastModified", "gated", "private", "disabled", "downloads",
                                     "likes", "pipeline_tag", "library_name", "tags")}
    card = info.get("cardData") or {}
    keep["license"] = card.get("license") or [t for t in info.get("tags", []) if t.startswith("license:")]
    keep["files"] = len(info.get("siblings") or [])
    print(json.dumps(keep, indent=1))


def main() -> None:
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd", required=True)
    for kind in ("models", "datasets"):
        s = sp.add_parser(kind)
        s.add_argument("--author")
        s.add_argument("--search")
        s.add_argument("--sort", default="")
        s.add_argument("--limit", type=int, default=100)
    s = sp.add_parser("tree")
    s.add_argument("repo", nargs="+")
    s.add_argument("--dataset", action="store_true")
    s.add_argument("--recursive", action="store_true")
    s.add_argument("--max", type=int, default=80)
    s = sp.add_parser("info")
    s.add_argument("repo")
    s.add_argument("--dataset", action="store_true")
    args = p.parse_args()
    try:
        if args.cmd in ("models", "datasets"):
            cmd_list(args.cmd, args)
        elif args.cmd == "tree":
            cmd_tree(args)
        else:
            cmd_info(args)
    except urllib.error.HTTPError as exc:
        print("HTTP error:", exc.code, exc.reason, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
