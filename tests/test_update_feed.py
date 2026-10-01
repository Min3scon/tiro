"""Update feed: envelope signatures, replay/freeze/downgrade protection, rollout and withdrawal."""
import base64
import datetime as dt
import json

import pytest

from tiro.update import ed25519, feed

SEED = bytes(range(32))
KEYS = {"ci": base64.b64encode(ed25519.public_key(SEED)).decode()}
NOW = dt.datetime(2026, 10, 2, 12, 0, tzinfo=dt.UTC)
SHA = "ab" * 32


def asset(name):
    return {"url": f"https://example.invalid/{name}", "size": 10, "sha256": SHA}


def manifest(version="2.0.4", serial=100, channel="stable", **entry):
    e = {"version": version, "rollout_percent": 100, "inventory": asset("files.json"),
         "packs": {"core": asset("core.zip"), "runtime": asset("runtime.zip")}}
    e.update(entry)
    return {"product": "Tiro", "channel": channel, "serial": serial, "issued_at": "2026-10-01T00:00:00Z",
            "expires_at": "2027-01-01T00:00:00Z", "revoked_versions": [], "platforms": {"win-x64": e}}


def decide(m, **kw):
    args = {"channel": "stable", "current": "2.0.3", "platform": "win-x64", "install_id": "x", "now": NOW}
    args.update(kw)
    return feed.decide(m, **args)


def test_version_order():
    assert feed.newer("2.0.3", "2.0.2")
    assert feed.newer("2.1.0", "2.0.9")
    assert feed.newer("10.0.0", "9.9.9")
    assert feed.newer("2.1.0", "2.1.0-beta.3")
    assert feed.newer("2.1.0-beta.3", "2.1.0-beta.2")
    assert not feed.newer("2.0.2", "2.0.2")
    with pytest.raises(ValueError):
        feed.parse_version("2.0")


def test_envelope_round_trip_and_tampering():
    raw = feed.seal(manifest(), SEED, "ci")
    assert feed.open_envelope(raw, keys=KEYS)["serial"] == 100
    env = json.loads(raw)
    payload = json.loads(base64.b64decode(env["payload"]))
    payload["platforms"]["win-x64"]["version"] = "9.9.9"  # tampered manifest, old signature
    env["payload"] = base64.b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(feed.FeedError):
        feed.open_envelope(json.dumps(env).encode(), keys=KEYS)


def test_untrusted_or_revoked_keys_and_junk():
    raw = feed.seal(manifest(), b"\x07" * 32, "ci")  # right key id, wrong key
    with pytest.raises(feed.FeedError):
        feed.open_envelope(raw, keys=KEYS)
    raw = feed.seal(manifest(), SEED, "other")  # unknown key id
    with pytest.raises(feed.FeedError):
        feed.open_envelope(raw, keys=KEYS)
    raw = feed.seal(manifest(), SEED, "ci")
    with pytest.raises(feed.FeedError):
        feed.open_envelope(raw, keys=KEYS, revoked_keys={"ci"})
    with pytest.raises(feed.FeedError):
        feed.open_envelope(raw, keys={})  # a build without keys trusts nothing
    for junk in (b"", b"{", b"[]", b'{"payloadType": "x"}', b"\xff\xfe", b" " * (feed.MAX_BYTES + 1)):
        with pytest.raises(feed.FeedError):
            feed.open_envelope(junk, keys=KEYS)
    m = manifest()
    m["product"] = "Other"
    with pytest.raises(feed.FeedError):
        feed.open_envelope(feed.seal(m, SEED, "ci"), keys=KEYS)


def test_offers_a_newer_version():
    d = decide(manifest())
    assert d.offer.version == "2.0.4" and d.serial == 100
    assert set(d.offer.packs) == {"core", "runtime"}


def test_up_to_date_and_never_downgrades():
    assert decide(manifest("2.0.3")).offer is None
    assert decide(manifest("2.0.2")).offer is None


def test_replay_of_an_older_manifest_is_refused():
    d = decide(manifest(serial=99), highest_serial=100)
    assert d.offer is None and "older" in d.reason


def test_expired_manifest_is_ignored_quietly():
    m = manifest()
    m["expires_at"] = "2026-09-30T00:00:00Z"
    assert decide(m).reason == "expired"
    m["expires_at"] = "2026-10-01T18:00:00Z"  # inside the one-day grace
    assert decide(m).offer is not None


def test_channel_must_match():
    assert decide(manifest(channel="beta")).reason == "wrong channel"
    assert decide(manifest(channel="beta", version="2.1.0-beta.1"), channel="beta").offer.version == "2.1.0-beta.1"


def test_staged_rollout():
    m = manifest(rollout_percent=10, rollout_salt="2.0.4")
    ids = [f"id-{i}" for i in range(2000)]
    offered = sum(decide(m, install_id=i).offer is not None for i in ids)
    assert 120 < offered < 280  # about 10 %
    inside = next(i for i in ids if decide(m, install_id=i).offer is not None)
    m2 = manifest(rollout_percent=50, rollout_salt="2.0.4")
    assert decide(m2, install_id=inside).offer is not None  # widening never drops anyone
    outside = next(i for i in ids if decide(m, install_id=i).offer is None)
    assert decide(m, install_id=outside, manual=True).offer is not None  # "Check now" skips the gate
    assert decide(manifest(rollout_percent=0, critical=True), install_id=outside).offer is not None


def test_withdrawn_versions():
    m = manifest()
    m["revoked_versions"] = ["2.0.4"]
    assert decide(m).offer is None  # nobody moves to a withdrawn version
    d = decide(m, current="2.0.4")
    assert d.revoked_current  # copies already on it go back to their previous version
    assert decide(manifest(), bad_versions={"2.0.4"}).offer is None  # failed here before


def test_os_and_platform():
    assert decide(manifest(min_os="10.0.22000"), os_version="10.0.19045").offer is None
    assert decide(manifest(min_os="10.0.22000"), os_version="10.0.22631").offer is not None
    assert decide(manifest(), platform="win-arm64").offer is None


def test_malformed_entries():
    m = manifest()
    m["platforms"]["win-x64"]["inventory"] = {"url": "http://insecure.invalid/x", "size": 1, "sha256": SHA}
    assert decide(m).offer is None
    m = manifest()
    del m["serial"]
    assert decide(m).reason == "malformed manifest"
    m = manifest()
    m["platforms"]["win-x64"]["packs"] = {"core": {"url": "https://x.invalid", "size": 1, "sha256": "zz"}}
    assert decide(m).offer is None
