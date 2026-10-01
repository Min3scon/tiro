"""Update feed tools for the release pipeline.

  python tools/feed.py keygen [--keyid ci-2026a]
      New Ed25519 key pair. Prints the public key (goes into tiro/update/feed.py TRUSTED_KEYS) and the private
      seed (goes into the GitHub Actions secret TIRO_UPDATE_KEY; never into git, never into a file in the repo).
  python tools/feed.py compose --channel stable --fragment release/update-fragment-win-x64.json [...]
                               [--previous old-stable.json] [--rollout 100] [--expires-days 120] --out manifest.json
      Merge per-platform release entries into a channel manifest. Platforms not rebuilt keep their entry from the
      previous feed. The serial only ever goes up.
  python tools/feed.py sign manifest.json --keyid ci-2026a --out stable.json      (seed from $TIRO_UPDATE_KEY)
  python tools/feed.py verify stable.json [--check-assets]
      Check the signature with the keys built into the app, print what each platform is offered and, with
      --check-assets, that every download exists with the right size.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import secrets
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tiro.update import ed25519, feed


def cmd_keygen(a) -> None:
    seed = secrets.token_bytes(32)
    pub = base64.b64encode(ed25519.public_key(seed)).decode()
    print(f"keyid:       {a.keyid}")
    print(f"public key:  {pub}")
    print(f"private key: {base64.b64encode(seed).decode()}   <- secret: GitHub secret / password manager only")


def _serial(previous: dict | None) -> int:
    now = int(dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M"))
    return max(now, int((previous or {}).get("serial", 0)) + 1)


def cmd_compose(a) -> None:
    previous = None
    if a.previous and Path(a.previous).is_file():
        previous = feed.open_envelope(Path(a.previous).read_bytes())  # must itself be genuine
        if previous.get("channel") != a.channel:
            sys.exit("the previous feed is for another channel")
    platforms = dict((previous or {}).get("platforms", {}))
    if not a.fragment and a.rollout_all is not None:
        # promote / pull back a staged rollout without a new build
        for entry in platforms.values():
            entry["rollout_percent"] = a.rollout_all
    for frag_path in a.fragment:
        frag = json.loads(Path(frag_path).read_text(encoding="utf-8"))
        entry = frag["entry"]
        entry["rollout_percent"] = a.rollout
        entry.setdefault("rollout_salt", entry["version"])
        old = platforms.get(frag["platform"])
        if old and feed.newer(old["version"], entry["version"]):
            sys.exit(f"{frag['platform']}: {entry['version']} is older than {old['version']} (never downgrade)")
        if old and old["version"] == entry["version"] and not a.allow_same:
            sys.exit(f"{frag['platform']}: {entry['version']} is already in the feed (use --allow-same to re-sign)")
        platforms[frag["platform"]] = entry
    now = dt.datetime.now(dt.UTC)
    manifest = {
        "product": feed.PRODUCT, "channel": a.channel, "serial": _serial(previous),
        "issued_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + dt.timedelta(days=a.expires_days)).isoformat(timespec="seconds"),
        "revoked_versions": sorted(set((previous or {}).get("revoked_versions", [])) | set(a.revoke or [])),
        "revoked_keys": list((previous or {}).get("revoked_keys", [])),
        "platforms": platforms,
        "models": (previous or {}).get("models", {}),
    }
    Path(a.out).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"{a.channel}: serial {manifest['serial']}, " +
          ", ".join(f"{p} {e['version']} ({e['rollout_percent']}%)" for p, e in platforms.items()))


def cmd_sign(a) -> None:
    seed_b64 = os.environ.get(a.key_env, "")
    if not seed_b64:
        sys.exit(f"${a.key_env} is not set")
    seed = base64.b64decode(seed_b64)
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    env = feed.seal(manifest, seed, a.keyid)
    # refuse to publish something the app itself would reject
    feed.open_envelope(env)
    Path(a.out).write_bytes(env)
    print(f"signed {a.out} with {a.keyid}")


def cmd_verify(a) -> None:
    m = feed.open_envelope(Path(a.envelope).read_bytes())
    print(f"genuine: channel {m['channel']}, serial {m['serial']}, expires {m['expires_at']}")
    bad = 0
    for plat, e in m.get("platforms", {}).items():
        print(f"  {plat}: {e['version']} ({e.get('rollout_percent', 100)}%)")
        if not a.check_assets:
            continue
        assets = ([("inventory", e["inventory"])] if "inventory" in e else []) + list(e.get("packs", {}).items())
        for name, asset in assets:
            req = urllib.request.Request(asset["url"], method="HEAD", headers={"User-Agent": "Tiro-release-check"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    size = int(r.headers.get("Content-Length", -1))
            except Exception as exc:  # noqa: BLE001
                print(f"    {name}: UNREACHABLE ({exc})")
                bad += 1
                continue
            ok = size == asset["size"]
            bad += not ok
            print(f"    {name}: {'ok' if ok else f'SIZE {size} != {asset['size']}'}")
    if bad:
        sys.exit(f"{bad} problem(s)")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--keyid", default="ci-2026a")
    c = sub.add_parser("compose")
    c.add_argument("--channel", choices=["stable", "beta"], required=True)
    c.add_argument("--fragment", action="append", default=[])
    c.add_argument("--previous")
    c.add_argument("--rollout", type=float, default=100.0, help="rollout percentage for the new fragments")
    c.add_argument("--rollout-all", type=float, help="without fragments: set every platform's rollout percentage")
    c.add_argument("--expires-days", type=int, default=120)
    c.add_argument("--revoke", action="append")
    c.add_argument("--allow-same", action="store_true", help="re-sign the same versions (refresh, rollout change)")
    c.add_argument("--out", required=True)
    s = sub.add_parser("sign")
    s.add_argument("manifest")
    s.add_argument("--keyid", default="ci-2026a")
    s.add_argument("--key-env", default="TIRO_UPDATE_KEY")
    s.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("envelope")
    v.add_argument("--check-assets", action="store_true")
    a = ap.parse_args()
    {"keygen": cmd_keygen, "compose": cmd_compose, "sign": cmd_sign, "verify": cmd_verify}[a.cmd](a)


if __name__ == "__main__":
    main()
