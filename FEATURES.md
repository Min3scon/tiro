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
| Stage timing log (capture, end of speech, model, transcription, correction, insertion) | - | - | - | - | - |
| Fast end of speech (VAD endpointing, tuned silence) | - | - | - | - | - |
| Words appear while speaking (streaming, stable commits only) | - | - | - | - | - |
| Warm model (preload, no idle unload, warm-up pass, GPU clock wake) | - | - | - | - | - |
| Fast text insertion per app (type / paste / bracketed paste for terminals), clipboard restored | - | - | - | - | - |
| Correction pass within budget (tier 0 skip, tier 1 ms-level, tier 2 deadline 150 ms, safe late swaps) | - | - | - | - | - |

## Phase A2: updates

| Feature | Unit | Int | E2E | Fail | Result |
|---|---|---|---|---|---|
| Background update check (launch + ~6 h jittered, never while dictating, offline/metered aware) | - | - | - | - | - |
| Background download with resume (delta where possible) | - | - | - | - | - |
| "Tiro x.y is ready" notification (Restart to update / Later / See what's new) | - | - | - | - | - |
| Tray badge + "Check for updates" / "Update ready" menu items | - | - | - | - | - |
| Install on quit (default on) | - | - | - | - | - |
| What's new page after updating (from CHANGELOG) | - | - | - | - | - |
| Settings > Updates (version, check, last checked, auto toggle, channel, metered toggle, roll back, Updates off) | - | - | - | - | - |
| Signed feed + SHA-256 verification, bad signature rejected | - | - | - | - | - |
| Downgrade protection (except explicit roll back) | - | - | - | - | - |
| Atomic install, previous version kept, auto roll back on failed self-test / 2 crashes | - | - | - | - | - |
| Critical-update flag | - | - | - | - | - |
| Staged rollout percentage + pulled release | - | - | - | - | - |
| Minimum OS / architecture respected | - | - | - | - | - |
| Model updates through the same feed (progress, checksum, resume, swap when idle, previous model kept) | - | - | - | - | - |
| Release pipeline builds installer, packages, signed feed, checksums; refuses bad versions | - | - | - | - | - |
| Privacy: update check sends only version, OS, architecture; Updates off sends nothing | - | - | - | - | - |
| Mac updater (Sparkle or equivalent) | - | - | - | - | - |

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
| Safe mode (flag / hold Shift; auto after 2 crashed starts) | - | - | - | - | - |
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
