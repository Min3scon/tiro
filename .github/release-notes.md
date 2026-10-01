## Tiro 2.0.1: your words, right

Hold a key, speak, let go: Tiro types what you said wherever your cursor is. Everything runs on your own computer.

### Download

| | |
|---|---|
| **Windows 10/11 (64-bit)** | [TiroSetup.exe](https://github.com/Min3scon/tiro/releases/latest/download/TiroSetup.exe): checks your PC, picks the fastest setup and walks you through it |
| **Mac with Apple Silicon (M1 or newer), macOS 13+** | [Tiro-mac-arm64.dmg](https://github.com/Min3scon/tiro/releases/latest/download/Tiro-mac-arm64.dmg): open it and drag Tiro to Applications. **Intel Macs aren't supported.** The first launch needs one extra step because Tiro isn't signed with a paid Apple ID: see [the README](https://github.com/Min3scon/tiro#mac-apple-silicon). |
| Portable Windows app | `Tiro-…-app-win64.zip` (plus `Tiro-…-gpu-runtime-win64.zip` for NVIDIA GPUs; the app's setup fetches it for you) |

### What's new in 2.0.1

- On Macs, speech now runs on the Apple chip's CPU cores with the compact model (about 6× faster than before on M1)
  and the download is 1.8 GB smaller. Core ML stays available as an experimental option.
- Safer corrections: a strong audio mismatch now vetoes a candidate outright, whatever else supports it.

### Tiro 2.0

- **A correction pass that learns what you mean.** Rare names, brands, places, games and jargon come out the way you
  write them: from your dictionary, fixes you teach it, and your own dictation history. Candidates are checked
  against the audio, and a small local AI model breaks ties. It only ever swaps a misheard word for one that
  sounds like it, and the rules are enforced in code.
- **Fix last transcription** (Ctrl+Alt+F): correct a word once, and Tiro remembers it.
- **Mac version** (Apple Silicon): a menu-bar app with Core ML speech recognition and an MLX AI check.
- **New installer and setup**: hardware detection with a plain-English recommendation, verified resumable
  downloads, a real speed test, microphone, shortcut, permissions on Mac, and a live test.
- **Privacy**: everything stays on your device. You can switch history learning off or clear it with one click,
  and Tiro never learns from password fields.

See the [accuracy report](https://github.com/Min3scon/tiro/blob/main/tests/accuracy/REPORT.md) for measurements.
