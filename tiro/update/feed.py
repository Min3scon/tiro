"""The update feed: one signed file per channel that says which version each platform should run.

Format: a DSSE-style envelope, so the manifest and its signature always arrive together:

    {"payloadType": "application/vnd.tiro.update.v1+json",
     "payload": "<base64 manifest>",
     "signatures": [{"keyid": "tiro-2026a", "sig": "<base64 Ed25519 over PAE(payloadType, payload)>"}]}

Decoded manifest:

    {"product": "Tiro", "channel": "stable", "serial": 202610011200,
     "issued_at": "2026-10-01T12:00:00Z", "expires_at": "2027-01-29T12:00:00Z",
     "revoked_versions": [], "revoked_keys": [],
     "platforms": {"win-x64": {"version": "2.0.3", "summary": "...", "notes_url": "...",
                               "rollout_percent": 100, "rollout_salt": "2.0.3", "critical": false,
                               "min_os": "10.0.17763",
                               "inventory": {"url": ..., "size": ..., "sha256": ...},
                               "packs": {"core": {...}, "runtime": {...}, "gpu": {...}}}},
     "models": {...}}

Tiro accepts a manifest only if a built-in key signed it, it is for the channel asked for, it is not older than the
newest one already seen (serial: no replays) and not expired (no freezes). It then moves to the version named only
if that is newer than the running one (no downgrades), the OS is recent enough and this copy falls inside the
staged-rollout percentage. Nothing about the user is ever sent: the rollout bucket is computed on the device.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass

from tiro.update import ed25519

PAYLOAD_TYPE = "application/vnd.tiro.update.v1+json"
PRODUCT = "Tiro"
MAX_BYTES = 256 * 1024
EXPIRY_GRACE = dt.timedelta(days=1)  # tolerate a clock that runs a little behind or ahead

# Release-signing public keys: keyid -> raw 32-byte Ed25519 public key, base64. The CI key signs every release;
# the offline key (private half never on any server) only signs emergency manifests and key rotations.
TRUSTED_KEYS: dict[str, str] = {
    "ci-2026a": "niDjW3ycpsyyVcMrJCbksKQbw+urpBSM04IZeUEFufw=",  # release pipeline (GitHub secret TIRO_UPDATE_KEY)
    "offline-2026a": "9Hi/GP3umvEQAKtSLX+jGY4Rvq1ZUAjepkZWfhae9e8=",  # kept offline: emergencies and key rotation
}
try:  # test builds only (tools/build.ps1 -TestKey): a throwaway key for the end-to-end update tests
    from tiro._build import TEST_KEYS

    TRUSTED_KEYS.update(TEST_KEYS)
except ImportError:
    pass


class FeedError(Exception):
    """The feed can't be trusted or read. Nothing from it is used."""


# ------------------------------------------------------------------------------------------------ versions
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?$")
_PRE_RANK = {"alpha": 0, "beta": 1, "rc": 2, None: 3}


def parse_version(v: str) -> tuple[int, int, int, int, int]:
    """'2.0.3' -> (2, 0, 3, 3, 0); '2.1.0-beta.2' -> (2, 1, 0, 1, 2). Pre-releases sort before the release."""
    m = _VERSION.match(v.strip().lstrip("v"))
    if not m:
        raise ValueError(f"not a version: {v!r}")
    major, minor, patch, pre, n = m.groups()
    return int(major), int(minor), int(patch), _PRE_RANK[pre], int(n or 0)


def newer(a: str, b: str) -> bool:
    """Is version a newer than version b?"""
    return parse_version(a) > parse_version(b)


# ------------------------------------------------------------------------------------------------ envelope
def pae(payload_type: bytes, payload: bytes) -> bytes:
    """DSSE pre-authentication encoding: what is actually signed (binds the payload type to the bytes)."""
    return b"DSSEv1 %d %s %d %s" % (len(payload_type), payload_type, len(payload), payload)


def seal(manifest: dict, seed: bytes, keyid: str) -> bytes:
    """Release pipeline: sign a manifest into an envelope."""
    payload = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    sig = ed25519.sign(seed, pae(PAYLOAD_TYPE.encode(), payload))
    env = {"payloadType": PAYLOAD_TYPE, "payload": base64.b64encode(payload).decode(),
           "signatures": [{"keyid": keyid, "sig": base64.b64encode(sig).decode()}]}
    return json.dumps(env, indent=1).encode()


def open_envelope(raw: bytes, keys: dict[str, str] | None = None, revoked_keys: set[str] | None = None) -> dict:
    """Verify an envelope and return its manifest. Raises FeedError for anything untrusted or malformed."""
    keys = TRUSTED_KEYS if keys is None else keys
    revoked = revoked_keys or set()
    if len(raw) > MAX_BYTES:
        raise FeedError("the feed is too large")
    try:
        env = json.loads(raw.decode("utf-8"))
        if env.get("payloadType") != PAYLOAD_TYPE:
            raise FeedError("unknown payload type")
        payload = base64.b64decode(env["payload"], validate=True)
        sigs = list(env["signatures"])
    except FeedError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, binascii.Error) as exc:
        raise FeedError(f"malformed feed: {exc}") from exc
    signed = pae(PAYLOAD_TYPE.encode(), payload)
    ok = False
    for s in sigs:
        try:
            keyid, sig = s["keyid"], base64.b64decode(s["sig"], validate=True)
        except (KeyError, TypeError, ValueError, binascii.Error):
            continue
        if keyid in keys and keyid not in revoked and ed25519.verify(base64.b64decode(keys[keyid]), signed, sig):
            ok = True
            break
    if not ok:
        raise FeedError("no trusted signature")
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except ValueError as exc:
        raise FeedError("the signed manifest is not JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("product") != PRODUCT:
        raise FeedError("the manifest is not for Tiro")
    return manifest


# ------------------------------------------------------------------------------------------------ decisions
@dataclass
class Asset:
    url: str
    size: int
    sha256: str

    @classmethod
    def of(cls, d: dict) -> Asset:
        url, size, sha = str(d["url"]), int(d["size"]), str(d["sha256"]).lower()
        local = url.startswith(("http://127.0.0.1:", "http://localhost:"))  # end-to-end tests on this machine
        if not (url.startswith("https://") or local) or size < 0 or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError("bad asset entry")
        return cls(url, size, sha)


@dataclass
class Offer:
    version: str
    summary: str
    notes_url: str
    critical: bool
    inventory: Asset | None  # None: no in-place update for this platform (e.g. Mac): tell the user instead
    packs: dict[str, Asset]


@dataclass
class Decision:
    offer: Offer | None  # the version to move to, if any
    reason: str  # for the log and the Settings page: "up to date", "rollout", "expired", ...
    serial: int  # the manifest's serial (remember it once the manifest was accepted)
    revoked_current: bool = False  # the running version was pulled: go back to the previous one


def _parse_time(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def _os_ok(min_os: str, os_version: str) -> bool:
    if not min_os or not os_version:
        return True
    try:
        need = tuple(int(x) for x in min_os.split("."))
        have = tuple(int(x) for x in os_version.split("."))
    except ValueError:
        return True
    return have >= need


def rollout_bucket(install_id: str, salt: str) -> int:
    """0-9999, fixed for this install and this release (raising the percentage only ever adds copies)."""
    return int.from_bytes(hashlib.sha256(f"{install_id}/{salt}".encode()).digest()[:8], "big") % 10000


def decide(manifest: dict, *, channel: str, current: str, platform: str, os_version: str = "",
           install_id: str = "", highest_serial: int = 0, bad_versions: frozenset[str] | set[str] = frozenset(),
           manual: bool = False, now: dt.datetime | None = None) -> Decision:
    """What this copy of Tiro should do with an already-verified manifest."""
    now = now or dt.datetime.now(dt.UTC)
    try:
        serial = int(manifest["serial"])
        expires = _parse_time(str(manifest["expires_at"]))
    except (KeyError, TypeError, ValueError):
        return Decision(None, "malformed manifest", 0)
    if manifest.get("channel") != channel:
        return Decision(None, "wrong channel", serial)
    if serial < highest_serial:
        return Decision(None, "older than a manifest already seen", serial)
    if expires + EXPIRY_GRACE < now:
        return Decision(None, "expired", serial)
    revoked = {str(v) for v in manifest.get("revoked_versions", [])}
    entry = (manifest.get("platforms") or {}).get(platform)
    if current in revoked:
        return Decision(None, "this version was withdrawn", serial, revoked_current=True)
    if not isinstance(entry, dict):
        return Decision(None, "no release for this platform", serial)
    try:
        version = str(entry["version"])
        if not newer(version, current):
            return Decision(None, "up to date", serial)
        if version in revoked or version in bad_versions:
            return Decision(None, "that version was withdrawn or failed here before", serial)
        if not _os_ok(str(entry.get("min_os", "")), os_version):
            return Decision(None, "needs a newer Windows or macOS", serial)
        critical = bool(entry.get("critical", False))
        percent = max(0.0, min(100.0, float(entry.get("rollout_percent", 100))))
        gated = not manual and not critical and percent < 100
        if gated and (not install_id or rollout_bucket(install_id, str(entry.get("rollout_salt", version))) >= percent * 100):
            return Decision(None, "rollout", serial)
        inventory = Asset.of(entry["inventory"]) if "inventory" in entry else None
        packs = {str(k): Asset.of(v) for k, v in dict(entry.get("packs", {})).items()}
        offer = Offer(version=version, summary=str(entry.get("summary", "")), notes_url=str(entry.get("notes_url", "")),
                      critical=critical, inventory=inventory, packs=packs)
    except (KeyError, TypeError, ValueError):
        return Decision(None, "malformed release entry", serial)
    return Decision(offer, "update available", serial)
