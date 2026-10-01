# Tiro: progress log

## Current state

- **Round:** "beat Wispr Flow" plan (pasted 2026-10-01 17:30). Branch `lite`; `main` stays releasable.
- **Phase:** A1, finding where the delay goes between speech ending and text appearing (Standard app).
- **Next:** time every stage, test in a terminal, Notepad and a browser box, fix the biggest delay first.
- **Background jobs:** `training/scheduler.py` (pid file `work/logs/scheduler.pid`). It runs the queue in
  `work/schedule.json` today 18:00-21:00, then continuously from Fri 2 Oct 00:00. Logs are in `work/logs/<job>.out`.
  The queue: prepare_all, label_parakeet, tts, bench_devmini, lite_suite.
- **How to resume after a restart:** start the scheduler again with
  `powershell -File training\tools\launch.ps1 -Name scheduler -Module training.scheduler -Low`.
  Every job resumes from its own output files.

The block below is rewritten every 5 minutes by `training/status.py`.

<!-- LIVE-STATUS:BEGIN -->
_Updated 2026-10-01 17:42. GPU 10% busy, 3.7/8.0 GB, 51 °C. Disk: 188 GB free on the work drive._

- ▶ **scheduler** (running, started 01 Oct 17:42): Paused until Thu 18:00 (heavy work runs Thu 01 Oct 18:00-21:00, Fri 02 Oct 00:00 onwards); queued: prepare_all, label_parakeet, tts, bench_devmini, lite_suite
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

## Left out and why

_(nothing yet)_

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
