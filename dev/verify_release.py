"""Check a published release from the outside: download links, manifest URLs, sizes and checksums."""
import hashlib
import json
import sys
import urllib.request

REPO = "Min3scon/tiro"
TAG = sys.argv[1] if len(sys.argv) > 1 else "latest"


def head(url):
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, int(r.headers.get("Content-Length") or 0), r.geturl()


ok = True
for name in ("TiroSetup.exe", "Tiro-mac-arm64.dmg", "manifest.json"):
    url = f"https://github.com/{REPO}/releases/latest/download/{name}"
    try:
        status, size, final = head(url)
        print(f"latest/{name}: HTTP {status}, {size / 1e6:.1f} MB")
    except Exception as exc:
        ok = False
        print(f"latest/{name}: FAILED {exc}")

with urllib.request.urlopen(f"https://github.com/{REPO}/releases/latest/download/manifest.json", timeout=60) as r:
    man = json.load(r)
print("manifest version", man["version"])
for key in ("app", "gpu"):
    pkg = man[key]
    status, size, _ = head(pkg["url"])
    match = size == pkg["size"]
    ok &= match
    print(f"{key}: {pkg['url'].rsplit('/', 1)[-1]} HTTP {status} size {'OK' if match else f'MISMATCH {size} vs {pkg[chr(115) + chr(105) + chr(122) + chr(101)]}'}")
# verify the app zip's checksum end to end (75 MB)
h = hashlib.sha256()
with urllib.request.urlopen(man["app"]["url"], timeout=120) as r:
    for chunk in iter(lambda: r.read(1 << 20), b""):
        h.update(chunk)
print("app zip sha256", "OK" if h.hexdigest() == man["app"]["sha256"] else "MISMATCH")
ok &= h.hexdigest() == man["app"]["sha256"]
for mkey, model in man["models"].items():
    for variant in ("fp32", "int8"):
        for f in model.get(variant, []):
            status, size, _ = head(f["url"])
            if size != f["size"]:
                ok = False
                print(f"  {mkey} {variant} {f['name']}: size MISMATCH {size} vs {f['size']}")
    print(f"model {mkey}: all files reachable")
print("ALL OK" if ok else "PROBLEMS FOUND")
