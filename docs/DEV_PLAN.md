# Tiro – development plan (round 2)

Goal: general accuracy for rare words via a context-aware, tiered correction pass; a Mac (Apple Silicon)
menu-bar version; a guided installer on both platforms; public GitHub repo with CI releases; Pages site.

## Architecture decisions
- **One codebase** (`tiro/`), platform layer `tiro/platform/` (windows, mac) for hotkeys, typing,
  window context, secure-field detection, autostart, sounds. Qt UI (tray = macOS menu bar via QSystemTrayIcon).
- **Speech**: Parakeet TDT 0.6B via ONNX Runtime everywhere. Windows: CUDA (NVIDIA) or CPU.
  Mac: Core ML EP (GPU/ANE) with CPU fallback. Word confidences from the TDT decoder (token softmax).
  Hotwords = decoder boosting trie (user dictionary + learned + top history terms, capped).
- **Correction pass** (`tiro/correct/`): Tier 0 gate (confidence + common-word + exact sound-key lexicon hit),
  Tier 1 lexicon matcher (sound-key deletion index + trigram index; dictionary, learned rules, history profile,
  starter dictionary), Tier 2 LLM *scorer* (small LM picks among lexicon candidates or keep-original by
  log-likelihood; prefix KV cache; never free text). Speculative prefetch on partial hypotheses, so results
  are ready at commit. Deadline (150 ms default) per commit; late in-place fix only if provably safe.
  Strict validator: only span swaps, phonetically similar, lexicon-backed; anything else rejected.
- **LLM runtime**: ORT (CUDA/CPU) with onnx-community Qwen2.5 q4 exports + `tokenizers`; Mac: MLX if available,
  else ORT CPU.
- **History**: SQLite in the user data dir; profile/indexes built in background; never during dictation;
  never from password/secure fields; one-click clear; off switch; export.
- **Learning loop**: "Fix last transcription" hotkey + tray item → edit → learned rules (plain lookup).
- **Setup**: shared Qt setup wizard (hardware detection, downloads with resume/verify, real benchmark incl.
  correction pass, mic + level meter, hotkey, permissions on Mac, custom words, learning opt-out, live test).
  Windows bootstrapper (WPF) installs the app then hands off to the wizard. Mac: .dmg, wizard on first run.
- **Release**: GitHub Actions builds Windows (zip + CUDA pack + TiroSetup.exe) and macOS (.dmg) on tags.
  Pages site from `site/`.

## Stages
1. Engine confidences; history store + profile; lexicon + indexes; starter dictionary; Tier 0/1; validator;
   session integration with prefetch. Unit tests.
2. Tier 2 LLM scorer (ORT), deadline/async/late fix, GPU arbitration, metrics, auto-degrade.
3. UI: settings pages (Accuracy, Dictionary & learning, Privacy, debug latency), Fix-last window + hotkey + tray.
4. Platform layer + Mac implementation + mac packaging (.app/.dmg).
5. Setup wizard + Windows bootstrapper hand-off + benchmark + runtime-pack downloads.
6. Test sets: 50+ tricky phrases (audio), history-context, do-no-harm, speed. Reports.
7. Repo, README, Actions (ci, release, pages), site; tag release; verify.
