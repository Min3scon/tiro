# Tiro feature checklist

Every feature, setting, mode, hotkey, installer step, update path, sound and platform build in this round's plan.
A feature ships only when its row has passing tests; otherwise it is off by default or left out, and the reason is in
PROGRESS.md under "Left out and why".

Columns: **Unit** tests, **Int**egration test, **E2E** on the installed app (real audio path, real typing),
**Fail**ure injection, **Result** (`ship`, `off` = shipped but off by default, `out` = left out, `-` = not done yet).
A cell holds the test name or `n/a` (with a reason) when that kind of test doesn't apply.

## Phase A1: speed

| Feature | Unit | Int | E2E | Fail | Result |
|---|---|---|---|---|---|
| Stage timing log (capture, end of speech, model, transcription, correction, insertion) | n/a (logging) | `dev/latency_e2e.py` reads it | latency matrix | n/a | ship |
| Fast end of speech: no fixed tail; stop at once if already quiet, else wait for the word to end (max 0.4 s) | `tests/test_stream.py` | latency matrix | Notepad 342 -> 54 ms median | key released mid-word: waits (TAIL_MAX) | ship |
| Words appear while speaking (streaming, stable commits only); final decode reused after a pause | `tests/test_stream.py` (reuse, mid-speech release) | `dev/eval_final_reuse.py`: 940/955 identical, reuse 13 vs 20 word errors on the rest | long dictation 433 -> 24 ms | release mid-speech: fresh decode | ship |
| Warm model (preload, warm-up of all GPU shape buckets, GPU wake on key-down); language model yields the GPU to speech | n/a | `dev/llm_coldstart.py` | first dictation after start 1.4 s -> 24 ms; after 3 min idle 92 ms | n/a | ship |
| Text insertion: type or paste (Shift+Insert in consoles), every character in order, clipboard restored | n/a | `dev/insertion_e2e.py`: 15/15 (accents, 5 scripts, emoji with joiners, newlines, 2,289 chars; clipboard restored) | latency matrix (Notepad, console, Chrome) | focus moved: guard drops text | ship (auto-paste per app left out: see PROGRESS) |
| Correction pass within budget (tier 0 skip, tier 1 ms-level, tier 2 deadline 150 ms, safe late swaps) | `tests/test_correct.py` | accuracy report | correction 0.0-0.4 ms at the end in the matrix | LLM busy: skipped, never waits | ship |

| Global hotkey on a native thread (never starved while a model loads) | `core/tests/test_hotkey.cc` (15 cases, same as `tests/test_hotkey.py`) | `dev/hook_gil_probe.py --native`: 29/29 taps swallowed in time during a model load (Python hook: 4 got through in 2.8 s, up to 2.2 s late) | latency matrix | DLL missing -> Python hook | ship |

## Phase A2: updates

| Feature | Unit | Int | E2E | Fail | Result |
|---|---|---|---|---|---|
| Background update check (launch + ~6 h jittered, never while dictating, offline/metered aware) | `tests/test_update_service.py` (metered, offline, busy) | `dev/update_e2e.py` | passed on frozen builds 9.0.0 -> 9.0.1 -> 9.0.2 | offline / metered / bad feed | ship |
| Background download with resume; only changed packs; unchanged files hard-linked | `tests/test_update_stage.py` | `dev/update_e2e.py` | passed on frozen builds 9.0.0 -> 9.0.1 -> 9.0.2 | tampered pack, corrupted local file | ship |
| "Tiro x.y is ready" notification (click: restart now; otherwise installs at next start) | n/a (Qt) | `dev/render_settings.py` | pending | dictating: restart refused | - |
| Tray badge + "Check for updates" / "Restart to update" menu item | n/a (Qt) | manual render | pending | n/a | - |
| Install at next start (default on; off = only when you click Restart) | `tests/test_update_service.py` | `dev/update_e2e.py` | passed on frozen builds 9.0.0 -> 9.0.1 -> 9.0.2 | n/a | ship |
| What's new after updating (CHANGELOG section, once) | n/a | `tiro/update/whatsnew.py` | pending | n/a | - |
| Settings > Updates (status, Check now, Restart, auto toggle, install at start, channel, metered, go back) | n/a | `dev/render_settings.py` | pending | n/a | - |
| Signed feed (Ed25519, DSSE envelope) + SHA-256 of every file; bad signature / tampering rejected | `tests/test_ed25519.py` (RFC 8032), `tests/test_update_feed.py` | `dev/update_e2e.py` | passed on frozen builds 9.0.0 -> 9.0.1 -> 9.0.2 | wrong key, tampered feed, junk, replay, expired | ship |
| Downgrade protection (except explicit roll back or a withdrawn version) | `tests/test_update_feed.py` | `tests/test_update_service.py` | pending | replayed old manifest | - |
| Side-by-side install, previous version kept, trial start, roll back after 2 failed starts, safe mode after 2 crashes | `tests/test_launcher.py` (6 cases, fake app) | `dev/update_e2e.py` | passed on frozen builds 9.0.0 -> 9.0.1 -> 9.0.2 | crashing build | ship |
| Critical-update flag (skips staged rollout, stronger notice) | `tests/test_update_feed.py` | n/a | n/a | n/a | - |
| Staged rollout percentage + withdrawn release (`update-feed.yml`) | `tests/test_update_feed.py`, `tests/test_update_service.py` | `tools/feed.py compose --rollout-all / --revoke` | pending | n/a | - |
| Minimum OS / architecture respected | `tests/test_update_feed.py` | n/a | n/a | n/a | - |
| Model updates through the same feed (progress, checksum, resume, swap when idle, previous model kept) | - | - | - | - | out (Phase B: no model changes in 2.0.3) |
| Release pipeline builds installer, packs, signed feed; refuses a version that isn't newer and a build trusting a test key | n/a | `release.yml` | pending (first run at v2.0.3) | n/a | - |
| Privacy: update check sends only version, OS, architecture; automatic checks off sends nothing | `tests/test_update_service.py::test_user_agent_has_no_identifiers` | n/a | pending | n/a | - |
| Mac: told about new versions, opens the download (no in-place updates until a Developer ID) | `tests/test_update_service.py` (no-inventory entry) | Mac CI build | untested on a Mac | n/a | - |

## Phase B1: models, Lite and the installer

| Feature | Unit | Int | E2E | Fail | Result |
|---|---|---|---|---|---|
| Lite engine inside Tiro (C++ core, Moonshine Streaming rungs) | - | - | - | - | - |
| Adaptive ladder from FREE resources, step down/up between dictations | core/tests/test_ladder.cc | - | - | - | - |
| Resource use setting: Light / Balanced / Max accuracy / Auto with plain-English text | - | - | - | - | - |
| Measured rung table shipped as data | - | - | - | - | - |
| Smallest rung < ~200 MB peak RAM (native engine) | - | - | - | - | - |
| Switch Lite <-> Standard in Settings without reinstalling | - | - | - | - | - |
| Installer: detect CPU/GPU/VRAM/RAM/OS, short benchmark, pick Lite/Standard + rung, explain, override | - | - | - | - | - |
| Installer: versioned layout (launcher + app-X.Y.Z), migration from the flat 2.0.x layout, stops only its own copies | n/a | `dev/installer_e2e.py` (8 checks) | fresh + flat upgrade | n/a | ship |
| Installer: download progress, resume, checksum; Lite works offline (smallest rungs bundled) | - | - | - | - | - |
| Installer: live test dictation showing the user's own delay | - | - | - | - | - |
| Standard model decision (keep v2 / Unified / fine-tuned) by the ship rule | - | - | - | - | - |
| "Use previous model" + auto roll back on failed model self-test | - | - | - | - | - |
| Distilled Lite students (only if they beat off-the-shelf) | - | - | - | - | - |

## Phase B2: personal learning and voice setup

| Feature | Unit | Int | E2E | Fail | Result |
|---|---|---|---|---|---|
| Voice setup: 10-15 phrases, ~1 minute, skippable, held-out phrases scored | - | - | - | - | - |
| Live read-along (words turn orange as heard; not colour-only; screen reader text) | - | - | - | - | - |
| "My speech has changed" mode | - | - | - | - | - |
| Pace/pause learning tunes VAD and endpointing | - | - | - | - | - |
| Personal correction rules from confirmed corrections (strict swap-only, diff-checked) | core/tests/test_correct.cc | - | - | - | - |
| History-backed context (George vs GeoGuessr) | - | - | - | - | - |
| Personal vocabulary, hotwords, contextual biasing | - | - | - | - | - |
| Personal n-gram rescoring (only if it helps) | - | - | - | - | - |
| Mic gain / noise floor / room calibration | - | - | - | - | - |
| Starter dictionary | - | - | - | - | - |
| Fix last transcription (hotkey + tray), learn from it | - | - | - | - | - |
| See / edit / delete / export everything learned | - | - | - | - | - |
| Personal adapter (LoRA) on capable devices, gated | - | - | - | - | - |
| Privacy: learning off switch, one-click delete, never from password fields | - | - | - | - | - |
| Show the user's own before/after accuracy | - | - | - | - | - |

## Phase B3: noise and hearing only the user

| Feature | Unit | Int | E2E | Fail | Result |
|---|---|---|---|---|---|
| Noise front end chosen by measurement (none / classic / neural / combos), adaptive | - | - | - | - | - |
| Fail-open noise gate (timeouts, bypass, pre-roll 300 ms, soft attenuation) | - | - | - | - | - |
| Panic switch (hotkey + tray) and watchdog | - | - | - | - | - |
| Shadow mode (gated vs ungated comparison, auto back-off) | - | - | - | - | - |
| Hear only the user (voiceprint gating / dominant voice) | - | - | - | - | - |
| Wind detection, reduction and advice | - | - | - | - | - |
| Zero typed words on pure noise (false insertions per hour) | - | - | - | - | - |
| "Noisy environment" indicator + Noise reduction Auto/Off/Strong setting | - | - | - | - | - |

## Phase B4: voiceprint and "My voice"

| Feature | Unit | Int | E2E | Fail | Result |
|---|---|---|---|---|---|
| Voiceprint (mic-robust speaker embedding), per-device adjustment, drift guard | - | - | - | - | - |
| Encrypted export (file / QR), optional own-cloud sync (off by default) | - | - | - | - | - |
| My voice page: re-record, reset voiceprint, isolation off, reset learning, delete everything | - | - | - | - | - |
| Voice-change detection + gentle prompt | - | - | - | - | - |
| Versioned voiceprint format, safe migration | - | - | - | - | - |

## Phase B5: sound and feel

| Feature | Unit | Int | E2E | Fail | Result |
|---|---|---|---|---|---|
| Sound set (UI + dictation cues), one language, matched loudness | - | - | - | - | - |
| Sounds start within ~20 ms; preloaded | - | - | - | - | - |
| No mic leak (no false insertions / WER change with sounds on) | - | - | - | - | - |
| Settings > Sounds: Off / Subtle / Full, volume, per-category toggles, previews | - | - | - | - | - |
| Visual equivalents, haptics where supported | - | - | - | - | - |

## Beat Wispr Flow

| Feature | Unit | Int | E2E | Fail | Result |
|---|---|---|---|---|---|
| Polish mode (off by default, shows changes, one-key undo to raw text) | - | - | - | - | - |
| Snippets (spoken phrase -> saved text) | - | - | - | - | - |
| Per-app formatting profiles (code editor, chat, email, document) | - | - | - | - | - |
| Command mode on selected text ("make this shorter", "turn this into a list") | - | - | - | - | - |
| Whisper-quiet speech | - | - | - | - | - |
| Filler removal, punctuation, capitals (existing) | - | - | - | - | - |
| Hands-free and push-to-talk modes (existing) | - | - | - | - | - |

## Reliability

| Feature | Unit | Int | E2E | Fail | Result |
|---|---|---|---|---|---|
| Safe mode (hold Shift / `--safe-mode`; automatic after 2 failed starts; corrections, learning, GPU, native hook off; tray item to leave) | `tests/test_launcher.py::test_two_failed_starts_mean_safe_mode` | settings overrides never saved (`Settings.apply_safe_mode`) | pending | crashing start | - |
| Per-feature crash isolation (a failing feature disables only itself) | - | - | - | - | - |
| Long-run soak (RAM flat, no growing delay) | - | - | - | - | - |
| Resource pressure (busy CPU, tight RAM, game, battery, heat) | - | - | - | - | - |

## Platforms

| Build | Unit | Int | E2E | Fail | Result |
|---|---|---|---|---|---|
| Windows x64 (Standard + Lite) | - | - | - | - | - |
| Windows ARM64 | - | - | - | - | - |
| Web (WASM, "Try it now") | - | - | - | - | - |
| Mac Apple Silicon (menu bar, Sparkle) | - | - | - | - | - |
| Mac Intel | - | - | - | - | - |
| iPhone / iPad | - | - | - | - | - |
| Android | - | - | - | - | - |
| Linux | - | - | - | - | - |

## Release deliverables

| Item | Check | Result |
|---|---|---|
| Trailer: hero, 30 s, 15 s, 9:16, poster, loop, captions; 5 review passes; every claim measured | - | - |
| Website updated and live (version, downloads, measured numbers, device table, noise chart, "hear it") | - | - |
| README updated (install, updates, privacy, numbers, licences, SmartScreen note) | - | - |
| GitHub Release with installer, update packages, signed feed, checksums, trailer | - | - |
| This PC updated through the in-app updater | - | - |
| Same version everywhere (PC, main, tag, Release, feed, installer, site) | - | - |
