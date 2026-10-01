# Tiro: progress log

## Current state

- **Round:** "beat Wispr Flow" plan (pasted 2026-10-01 17:30). Branch `lite`; `main` stays releasable.
- **Phase:** A done in code and measured (A1 table below); A2 updater built and unit/launcher-tested; version 2.0.3.
- **Next:** update end-to-end test on frozen test builds (`dev/update_e2e.py`), installer test
  (`dev/installer_e2e.py`), local full build + `dev/release_smoke.ps1`, merge `lite` -> `main`, tag v2.0.3 (CI
  builds, publishes, signs the feed), install on this PC with `dev/install_on_pc.ps1` (backup + roll back), then
  Phase B.
- **Keys:** update-signing key `ci-2026a` is the GitHub secret `TIRO_UPDATE_KEY`; the offline key
  `offline-2026a` is only in `work/keys/` (never committed) until you move it to a password manager.
- **Background jobs:** `training/scheduler.py` (pid file `work/logs/scheduler.pid`). It runs the queue in
  `work/schedule.json` today 18:00-21:00, then continuously from Fri 2 Oct 00:00. Logs are in `work/logs/<job>.out`.
  The queue: prepare_all, label_parakeet, tts, bench_devmini, lite_suite.
- **How to resume after a restart:** start the scheduler again with
  `powershell -File training\tools\launch.ps1 -Name scheduler -Module training.scheduler -Low`.
  Every job resumes from its own output files.

The block below is rewritten every 5 minutes by `training/status.py`.

<!-- LIVE-STATUS:BEGIN -->
_Updated 2026-10-01 19:27. GPU 5% busy, 2.9/8.0 GB, 50 °C. Disk: 185 GB free on the work drive._

- ▶ **scheduler** (running, started 01 Oct 17:42): Working until 21:00: nothing left to run; queued: prepare_all, label_parakeet, tts, bench_devmini, lite_suite
  - `2026-10-01 17:42:07  scheduler up (pid 17140)`
- ✓ **tts** (finished, started 01 Oct 15:49)
  - `2026-10-01 15:49:10  120000 sentences, 60 shards to synthesise with 3 workers`
- ✓ **fetch_tts2** (finished, started 01 Oct 15:41)
  - `kokoro-multi-lang-v1_0.tar.bz2: 187/350 MB (18.5 MB/s)`
  - `done kokoro-multi-lang-v1_0.tar.bz2 in 71s`
- ✓ **fetch_tts** (finished, started 01 Oct 15:38)
  - `kokoro-multi-lang-v1_1.tar.bz2: 342/365 MB (17.0 MB/s)`
  - `done kokoro-multi-lang-v1_1.tar.bz2 in 80s`
- ✓ **label_parakeet** (finished, started 01 Oct 15:35): 75 h labelled this run at 137 h/hour; now ami-sdm 7/27
  - `2026-10-01 16:07:58  ami-sdm [6/27] sdm__train-00005-of-00027.parquet: 3318 utts 3.2 h in 53s (RTFx 217)`
  - `2026-10-01 16:08:44  ami-sdm [7/27] sdm__train-00006-of-00027.parquet: 3230 utts 3.0 h in 45s (RTFx 238)`
- ✓ **fetch_noise** (finished, started 01 Oct 15:15)
  - `musan.tar.gz: 11072/11086 MB (2.4 MB/s)`
  - `done musan.tar.gz in 4800s`
- ✓ **prepare_all** (finished, started 01 Oct 14:27)
  - `2026-10-01 16:10:10  peoples-speech: [5/62] clean/train-00052-of-00804.parquet: 1868 utts, 7.2 h in 26s (this run 35 h)`
  - `2026-10-01 16:10:33  peoples-speech: [6/62] clean/train-00065-of-00804.parquet: 1868 utts, 6.5 h in 23s (this run 42 h)`
<!-- LIVE-STATUS:END -->

## Decisions

| When | Decision | Why |
|---|---|---|
| 10-01 | Lite engine = C++ core (`core/`) on the vendored Moonshine Streaming runtime (MIT) | best accuracy per MB in the survey; streaming; trainable; one engine for all platforms |
| 10-01 | Teacher = IBM Granite Speech 4.1 2B (8-bit LM, 3.4 GB VRAM); the Standard model labels everything first, Granite re-labels doubtful clips | best open model (5.33% mean WER), Apache 2.0, fits next to Tiro on the 8 GB GPU; slow here (about 5x real time) |
| 10-01 | Cohere Transcribe not used | its weights are gated behind terms only the user can accept |
| 10-01 | Decoder runs single-threaded; encoder gets the thread budget | ONNX Runtime 1.30's 4-bit int8-compute kernel crashed with 4 threads; one thread is also faster per token |
| 10-01 | Moonshine exports: decoder 4-bit blocks (output layer untied), encoder and cross-attention int8 | 45 -> 10 ms per token, 280 -> 230 MB peak, same transcripts |
| 10-01 | Vocabulary bias only helps FINISH a term (no bonus for starting one) | the upstream start bonus changed ordinary words ("ranked" -> "rank") |
| 10-01 | One app, one installer; Lite and Standard switchable in Settings | user decision |
| 10-01 | Heavy jobs today only 18:00-21:00, then normal | user decision |
| 10-01 | No fixed 0.25 s listening tail after the key comes up: stop at once if you'd already stopped talking, else wait for the word to end (max 0.4 s) | the tail was ~275 of ~320 ms in every dictation |
| 10-01 | Global hotkey moves to a native thread (`tiro_hook.dll`, C++ port of the hotkey state machine, same tests) | the Python hook needs Python's lock: while a model loaded, keyboard input stalled system-wide (4 of 28 taps got through in 2.8 s, up to 2.2 s late) and a press could be lost; native: 29 of 29, none late |
| 10-01 | The correction model yields the GPU whenever a speech decode is waiting or a final decode is due | first dictations after start took 1.4 s: the final decode queued behind the correction model |
| 10-01 | Updater: own signed feed + side-by-side versions + launcher with trial start and roll back (not Velopack, tufup, WinSparkle) | research (`work/research/updater.md`): each framework misses at least three of resume, signed feed, keep previous version, staged rollout; Velopack's uninstall would also wipe `%LOCALAPPDATA%\Tiro` |
| 10-01 | Feed signature: Ed25519 in pure Python (RFC 8032 vectors pass), DSSE envelope with serial + expiry | no extra crypto dependency in the app; serial stops replays, expiry stops freezes |
| 10-01 | Mac: updates stay "tell me and open the download" until the app has a Developer ID | ad-hoc signed apps lose Microphone/Accessibility permission on every update (Apple TN3127) |

## Left out and why

| What | Why |
|---|---|
| Automatic switch to pasting in browsers / Electron apps | Typing the last ~15 characters costs ~50 ms more than pasting in Chrome (54 vs 6 ms), too small to justify touching the clipboard when you chose "Type it". Paste stays a setting. |
| Measuring the Claude Code CLI after the fixes | Claude Code started asking "do you trust this folder?" for every test folder (even `D:\`, listed as trusted). Answering it is the user's decision, so the test refuses to type into it. Baseline before that: 359 ms short / 403 ms long; the same console input path after the fixes: 52 ms / 24 ms. |
| Mac automatic updates | Need an Apple Developer ID (see Decisions); Macs are told about new versions instead. |
| Model updates through the feed | Not needed for 2.0.3 (no model changes); the feed format has a `models` section for Phase B. |

## Phase B plan (order of work)

Each item ships only with a passing FEATURES.md row; otherwise it stays off or is left out, with the reason.
1. **Lite inside Tiro** (B1): Settings "Engine: Standard / Lite" + "Resource use"; Lite runs the shared C++ engine's
   streaming session; measured rung table; installer picks Lite on weak machines; switching needs no reinstall.
2. **Sound set** (B5): the synthesised set in `tools/sounds/design.py`, preloaded playback, Settings > Sounds,
   mic-leak and latency tests.
3. **Everyday wins**: snippets, per-app formatting, Polish mode (off by default, local model, shows changes, one-key
   undo), command mode on selected text.
4. **Voice setup and read-along** (B2), cheap personal learning (pause/pace -> endpointing, correction rules),
   My voice page with resets and deletion.
5. **Noise** (B3): measured comparison (none / classic / neural), fail-open gate in shadow mode first.
6. **Voiceprint** (B4): only with enrolment and a confident match; off otherwise.
7. **Standard model decision** on identical test sets (v2 vs Parakeet Unified); distillation only if it beats the
   off-the-shelf models (training runs in the background from the queue).

## Beat Wispr Flow: their features and where Tiro stands

From `work/research/wispr_flow.md` (their own docs, changelog and independent reviews, read 2026-10-01). Their
numbers are their claims. Tiro numbers are measured here, on this PC.

| Their feature (public info) | Tiro |
|---|---|
| Speed: "<700 ms" target from end of speech (no published measurement); desktop shows no text until you stop | Words typed while you talk; the rest lands 24-54 ms after the key comes up (A1, RTX 3070) |
| Cloud only; no offline mode; audio and context processed in the US | Everything on the device; works offline (win) |
| Free tier 2,000 words/week; Pro $12-15/month; no lifetime licence | Free and open source, no limit (win) |
| ~800 MB RAM idle, 8-10 s start on Windows (How-To Geek) | to measure in Phase B (Lite rungs from ~200 MB) |
| Punctuation, capitals, ~30 spoken symbols, spoken lists | Punctuation and capitals from the model; "new line"/"new paragraph". Spoken symbols and lists: Phase B |
| Filler removal (none/light/medium; medium rewrites) | Fillers removed (um, uh); rewriting only in the optional Polish mode (Phase B, off by default) |
| Backtrack ("2 actually 3" -> "3") | Polish mode candidate (Phase B); never in normal dictation (strict swap-only rule) |
| App-adaptive styles (formal/casual per app) | Per-app formatting profiles: Phase B |
| Context awareness (sends screen and text-box contents) | Local only: history and dictionary on the device; never password fields |
| Personal dictionary + auto-learn from your corrections | Dictionary, "Fix last transcription", learning from history (local); voice-specific learning in Phase B |
| Snippets (spoken trigger -> saved text) | Phase B |
| Push-to-talk, hands-free, double-tap lock | Same (hold, toggle, double-tap lock, Esc cancels) |
| Command Mode / Transforms (edit selected text by voice) | Phase B (command mode on selected text, local model) |
| Whisper / quiet speech (relies on a close mic) | Phase B3 (noise and quiet-speech work, measured) |
| 100+ languages, one per dictation | English (best model) or 25 European languages |
| Mac, Windows x64, iPhone, Android; no Linux / iPad / Windows on ARM | Windows x64 and Mac Apple Silicon today; others in the platform plan, labelled until tested |
| Updates: app store / their updater | Signed in-app updates with roll back (A2) |

Wins to keep: offline and private, free, faster after you stop, learns your words locally, roll back on bad
updates. Not to be named or shown in Tiro's marketing (standing rule).

---

## Log

## 2026-10-01: starting point

### This machine (where training runs)

| | |
|---|---|
| CPU | Intel Core i5-10600KF, 6 cores / 12 threads, AVX2 (no AVX-512) |
| RAM | 16 GB (about 6 GB free with the usual apps open) |
| GPU | NVIDIA GeForce RTX 3070, **8 GB VRAM**, driver 581.29 (CUDA 13.0). The installed copy of Tiro keeps about 2.5–3 GB of it busy, which leaves about 4.5 GB for training jobs. |
| Disk | D: 328 GB free (all datasets, labels and checkpoints live in `D:\dictation\work`, which is never committed) |
| Other | WSL 2.7.8 is installed (needed later for NVIDIA NeMo training); Docker Desktop is installed but stopped |

**Is the GPU enough for the teacher?** Yes, with limits.
- The chosen teachers (2B-parameter speech models in bf16, about 4–5 GB) fit for labelling, and every Lite student
  (34M–250M parameters) can be trained here.
- Fine-tuning the 0.6B Standard model fully would need about 10 GB, so on this card it is fine-tuned with
  memory-saving methods: frozen lower layers or LoRA, 8-bit optimiser states and gradient checkpointing.
- Labelling thousands of hours is the slow part: days, not hours. Time was declared not to be a constraint, so
  everything runs here, resumable, in the background.
- A cloud notebook is prepared as an optional speed-up (see `training/cloud/`). Nothing waits on it.

### What Standard ships today (the baseline to beat)

| Platform | Speech model | Precision | Runtime |
|---|---|---|---|
| Windows + NVIDIA GPU | NVIDIA Parakeet TDT 0.6B v2 (600M params) | fp32 encoder (2.4 GB), int8 joint | ONNX Runtime 1.30, CUDA EP, custom greedy TDT decoder with hotword boosting |
| Windows, CPU-only install | Parakeet TDT 0.6B v2 | int8 encoder (≈ 620 MB) + int8 joint | ONNX Runtime CPU EP |
| Windows, CPU with fp32 present | Parakeet TDT 0.6B v2 | fp32 encoder + int8 joint | ONNX Runtime CPU EP |
| Windows, Vulkan / DirectML | not implemented (no such path exists in Standard) | – | – |
| Mac (Apple Silicon) | Parakeet TDT 0.6B v2 | int8 encoder + int8 joint | ONNX Runtime CPU EP (default); Core ML EP experimental (static shapes) |
| Mac, Metal | not used for speech (MLX/Metal is only used by the optional AI check) | – | – |

Voice activity detection: Silero VAD (ONNX). Correction pass: Tier 0/1/2 (see README).

### What exists in October 2026 (checked today)

Teacher candidates (Open ASR leaderboard, English, mean WER over 8 test sets; lower is better):

| Model | Params | Mean WER | Licence | Notes |
|---|---|---|---|---|
| IBM Granite Speech 4.1 2B | 2B | 5.33 | Apache 2.0 | Conformer CTC encoder + Granite LLM; punctuation and case via prompt |
| Cohere Transcribe 03-2026 | 2B | 5.42 | Apache 2.0 (gated: needs you to accept on Hugging Face) | Conformer encoder-decoder, fast (RTFx 525) |
| IBM Granite 4.0 1B Speech | 2B total | 5.52 | Apache 2.0 | |
| NVIDIA Canary-Qwen 2.5B | 2.5B | 5.63 | CC-BY-4.0 | needs NeMo |
| Qwen3-ASR 1.7B | 1.7B | 5.76 | Apache 2.0 | |
| NVIDIA Parakeet Unified EN 0.6B | 0.6B | ≈ 5.91 (offline) | NVIDIA Open Model License | same size as Standard, streaming and offline |
| **Parakeet TDT 0.6B v2 (Standard today)** | 0.6B | 6.05 | CC-BY-4.0 | |

Small models for Lite rungs:

| Model | Params | Mean WER | int8 size | Licence |
|---|---|---|---|---|
| Moonshine Streaming tiny | 34M | 12.01 | ≈ 45 MB | MIT |
| Moonshine Streaming small | 123M | 7.84 | ≈ 142 MB | MIT |
| Moonshine Streaming medium | 245M | 6.65 | ≈ 269 MB | MIT |
| Moonshine v1 tiny / base | 27M / 61M | ≈ 12.7 / 10.1 | 43 / 135 MB | MIT |

The published numbers above are the model makers'. Every number used for a decision is re-measured here on the
same test sets with the same normaliser.

### Engine decision (2026-10-01)

The Lite engine (`core/`) is a C++ library with a C API (`core/include/tiro_core.h`) that every Lite app links.
It runs **Moonshine Streaming** models through ONNX Runtime, using the speech runtime from
[moonshine-ai/moonshine](https://github.com/moonshine-ai/moonshine) (MIT) vendored in `core/third_party/moonshine`.
Tiro's local changes there are marked `TIRO:`.

Why Moonshine Streaming:
- Best accuracy per megabyte of everything tested.
- MIT licence.
- Truly streaming: a sliding-window encoder with 320 ms of look-ahead.
- Trainable in Hugging Face Transformers, with an MIT exporter to the runtime's graphs.

Parakeet (the Standard model) will be added to the same engine for the top rungs.

First native-engine measurements on this PC (Moonshine Streaming **small**, one CPU thread, LibriSpeech dev, before any training):

| Build | Decoder file | Decode per token | Peak RAM | Note |
|---|---|---|---|---|
| Moonshine's own int8 release | 82 MB | 45 ms | 280 MB | the output layer is de-quantised on every token |
| Tiro export, 4-bit decoder (int8 compute), untied output layer | 51 MB | 16 ms | 229 MB | same transcripts |

Remaining costs and how they'll be fixed:
- The key/value cache layout of the exported decoder copies the whole cache on every step: re-export it with per-layer caches.
- The front-end convolutions are expensive: quantise them or give them threads.

In live use most of the work happens while you speak, so the delay after you stop is much smaller than these totals.

### Schedule (2026-10-01 16:10, updated 16:20)

Today only, you asked for heavy work between 18:00 and 21:00 (you're using the PC now). The scheduler
(`training/scheduler.py`) runs the heavy jobs today 18:00–21:00, pauses them at 21:00, and from midnight
(Fri 2 Oct 00:00) runs them normally again, in the background at low priority. Outside those hours only light work
runs (downloads, writing code). Every job resumes where it stopped. The hours live in `work/schedule.json`
(re-read every minute).

Queue, in order:
1. Finish converting the training audio.
2. Label it with the Standard model.
3. Synthesise the vocabulary and dictation audio.
4. Finish the candidate benchmark.

Training rounds join the queue as soon as labels exist.

### Decision (2026-10-01 17:00): one app, one installer

Lite and Standard are **the same Tiro app**, installed by **one installer**:
- The installer checks the hardware (it already measures GPU, VRAM, RAM and CPU) and picks Standard or Lite with a
  plain-English reason, so nobody with a strong PC ends up on Lite by accident. You can override its choice.
- Settings can switch between Lite and Standard at any time without reinstalling. Tiro downloads the other engine's
  files if they're missing.

The C++ engine in `core/` therefore plays two roles:
- **inside Tiro on Windows and Mac**, it is the Lite engine (loaded as a library next to Standard's Parakeet engine);
- **on phones and the web**, it is the whole engine, because those apps have to be separate by platform.

The separate native Windows Lite app I'd started is dropped.

Note on the RAM column of the first candidate benchmarks: those runs measured the whole Python process, test-set
loading included, so the "peak" there is not the model's. RAM is measured with the native engine instead
(`tiro-transcribe`). For example, Moonshine Streaming small at 4-bit/8-bit peaks at 230 MB, and that includes ONNX
Runtime and buffers.

## Phase A1: make dictation fast (started 2026-10-01 17:40)

**Problem reported:** dictating into the Claude Code text box (a terminal) takes a few seconds between finishing
speaking and the text appearing. The target is about 300 ms.

**Method:**
- `tiro/session.py` now records stage timestamps and logs one line per dictation:
  "latency: release->typed … (tail, decode, correction, typing)".
- Setting `TIRO_TIMING_LOG` makes it also write JSON lines.
- `dev/latency_e2e.py` drives the real app from source, with:
  - the F24 hotkey;
  - an isolated profile;
  - a WAV file as the microphone;
  - a guard that only lets it type into windows the test opened.

It times key release → last character visible, polling every 5 ms, in four targets:
- **notepad** (Win32 edit control);
- **term** (a plain console program, measuring keystroke delivery);
- **claude** (the real Claude Code CLI 2.1.216 in a console window, read-only plan mode, never sent);
- **browser** (Chrome text box).

Windows Terminal isn't installed on this PC, so "terminal" means the classic console window.

### A1 baseline (2026-10-01 18:00, v2.0.2 code, GPU, typing)

Measured with `dev/latency/run_baseline.ps1`. Each row: 6 dictations of a 3.5 s sentence (or 3 of a 16.6 s,
four-sentence one). "Visible" = key release → last character on screen. Tiro's own stage timing comes from
`tiro/session.py`.

| Target | Visible, median (p95) | Release → typed, inside Tiro | Where Tiro's time goes |
|---|---|---|---|
| Notepad | 342 ms (742) | ~320 ms | listening tail 275, final decode 40-60, typing 3-6 |
| Console program | 374 ms (387) | ~325 ms | same |
| Claude Code (console) | 359 ms (364) | ~323 ms | same |
| Chrome text box | 528 ms (540) | ~323 ms | same, plus ~200 ms inside Chrome |
| Notepad, long | 433 ms (456) | ~400 ms | tail 275, final decode 110-150 (longer window) |
| Console, long | 429 ms (446) | | |
| Claude Code, long | 403 ms (411) | | |

What the numbers say:
1. **The fixed 0.25 s listening tail is most of the delay.** After the key comes up, v2.0.2 always keeps
   listening for 250 ms (plus up to 30 ms of loop granularity), even when you finished speaking long before.
2. **The first dictations after Tiro starts were slow: 1.37-1.47 s.** The final decode waited 1.1-1.2 s.
   The correction model's first (cold) runs share the GPU lock with the speech model, so the speech model
   queued behind them. Once warm, the final decode takes 40-60 ms.
3. **A hotkey press while the correction model is loading is lost.** The F24 press 1-5 s after "speech model
   ready" did nothing. The keyboard hook is Python code; loading a model holds Python's lock for seconds, and
   Windows silently removes a hook that doesn't answer in time. v2.0.2 re-installs it after loading or every
   60 s.
4. **Chrome adds ~200 ms on top of Tiro**, about one screen frame per typed character (15 characters typed at
   the end).
5. Long dictations type most words while you talk (203 of 218 characters), so the end cost barely grows.

### A1 after the fixes (2026-10-01 18:52-19:02, same tests)

Fixes: no fixed listening tail; the decode made during your pause is reused at the end (`dev/eval_final_reuse.py`:
940 of 955 eligible clips identical; on the 15 others the reused decode made 13 word errors against 20 for a
fresh one, difference not significant); the correction model yields the GPU to speech decoding; the hotkey runs on
a native thread. Browser numbers now come from the page itself (it reports each change after it is painted), so
the Chrome baseline below was re-measured the same way.

| Target | Before: median (p95) | After: median (p95) | Inside Tiro after (release → typed) |
|---|---|---|---|
| Notepad | 342 ms (742) | **54 ms** (55) | 2-5 ms |
| Console program | 374 ms (387) | **53 ms** (57) | 2-5 ms |
| Chrome text box, typing | 353 ms (393) | **54 ms** (59) | 4 ms |
| Chrome text box, paste | – | **6 ms** (23) | 2 ms |
| First dictations after Tiro starts (Notepad) | 1,370-1,470 ms | **24 ms** (25) | |
| First dictation after 3 min idle (Notepad) | – | 92 ms (108) | |
| Notepad, long (16.6 s, 4 sentences) | 433 ms (456) | **24 ms** (24) | |
| Console, long | 429 ms (446) | **24 ms** (35) | |
| Chrome, long | 446 ms (464) | **29 ms** (36) | |
| Notepad, CPU only (no GPU) | 496 ms (513) | **25 ms** (52) | |
| Notepad, CPU only, long | 862 ms (1,218) | **32 ms** (426: one run released before the pause decode, so it decoded fresh) | |

What's left is the app drawing the last few characters (about 3.5 ms per typed character in Chrome, ~1-2 ms in
Notepad and the console). Inside Tiro, release → typed is now 2-5 ms when you'd paused before letting go.

## Phase A2: in-app updates (2026-10-01 18:10-19:20)

Research: `work/research/updater.md` (Velopack, tufup, WinSparkle/Sparkle, custom). Chosen: a small custom updater
(see Decisions). How it works:
- **Feed:** one signed file per channel (`stable.json`, `beta.json`) on a permanent `update-feed` pre-release;
  Ed25519 signature over the manifest (DSSE envelope), a serial that only grows (replays refused), an expiry
  (refreshed monthly by `update-feed.yml`), staged rollout per release, withdrawn versions, minimum OS.
- **Client** (`tiro/update/`): checks 2 min after start and every ~6 h (never while dictating, not on metered
  connections unless allowed; off = no network), stages the new version next to the running one (unchanged files
  hard-linked, only changed packs downloaded with resume, every file checked against the signed inventory), runs
  its health check, and queues it for the next start.
- **Launcher** (`installer/TiroLauncher`, the new `Tiro.exe` at the top of the install folder): starts the queued
  version on trial; it must confirm it came up (speech model loaded, self-test clip transcribed), else it's tried
  once more and then rolled back for good. Two failed normal starts -> safe mode.
- **Installer:** installs the same versioned layout and moves an old flat 2.0.x install aside as "previous".
- **Release pipeline:** builds the native hook and launcher, publishes the update packs, then a `feed` job signs
  and publishes the feed with the `TIRO_UPDATE_KEY` secret (set; offline key kept off-line).

Tests: RFC 8032 vectors, feed rules (17), staging (8), service (11), network (4: what a check sends, resume after a
dropped download, corrupted download refused, other hosts refused), launcher with a fake app (6), feed tool dry
run with the real key.

**End-to-end on real frozen builds** (`dev/update_e2e.py`, 19:25, all 9 checks passed): an install of 9.0.0 with a
local signed feed. A feed signed with an unknown key is refused. 9.0.1 is found 3 s after start, staged next to
9.0.0 with 347 files reused (104 DLLs as hard links) and only the 10 MB core pack downloaded, queued, started on
trial after a restart and committed (9.0.0 kept as previous). 9.0.2, built to fail on start, is found, staged,
tried twice, rolled back to 9.0.1 and never tried again. A tampered pack is refused and nothing is queued. Found
and fixed on the way: a quit request sent while Tiro was still starting was dropped.

**Installer end-to-end** (`dev/installer_e2e.py`, 19:30, all 8 checks passed): a fresh install lays out the launcher,
`state.json` and `app-9.0.0` and passes its self-test; installing over an old flat install (2.0.x layout) moves it
into its own folder as the previous version, keeps the models, and the new version becomes current.

**Release smoke test of the 2.0.3 build** (`dev/release_smoke.ps1`): health check with only the release keys,
GPU self-test 52 ms, CPU self-test 266 ms, dictation into Notepad 26 ms and a console 58 ms (median after key
release); Chrome 30 ms when re-checked.

**Incident, 19:18 (fixed):** the update test's Tiro copies used the F24 hotkey and the real microphone, and the
release smoke test (also F24) ran at the same time, so those copies opened your microphone 4 times for a few
seconds. Nothing was typed or kept (history was off in that profile). Now test copies get a silent test recording
instead of the microphone, an empty typing allow-list and their own key (F22), and the smoke test refuses to start
while another test's copy runs.

**Incident, 18:55 (fixed):** your real Windows autostart entry for Tiro pointed at the source checkout
(`pythonw run_tiro.pyw --autostart`) instead of your copy (`dist\Tiro\Tiro.exe`). A run from source "repairs"
autostart to itself on start (`autostart.refresh_path`). Fixed in code (only an installed copy without a test
profile may do that) and your entry restored to `"D:\dictation\dist\Tiro\Tiro.exe" --autostart` (checked with an
unvirtualized process).

Not reproduced: "a few seconds" in steady state. The few-seconds cases match points 2 and 3 (first dictations
after start or wake, a press during loading). Note that your PC still runs Tiro 1.0.0, which is older than all of
this.
