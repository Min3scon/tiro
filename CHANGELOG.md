# Changelog

## 2.0.3 (2026-10-02)

- **Text appears right after you let go.** Tiro no longer keeps listening for a fixed quarter-second after you
  release the key, and reuses what it already worked out during your pause. Measured on an RTX 3070, from key
  release to the last word on screen: about 0.34 s before, about 0.05 s now (Notepad, a console window, a Chrome
  text box), and the first dictations after Tiro starts are no longer slow.
- **The shortcut always works**, even while a model is loading: the global hotkey now runs on its own native thread.
  Before, loading a model could stall typing on the whole PC for a moment and miss a press.
- **Automatic updates.** Tiro checks for a new version now and then (never while you dictate), downloads only what
  changed, checks every file against a signed list, and installs it the next time it starts. If a new version
  doesn't start properly, Tiro goes back to the one you had. Settings → Updates has the controls, including
  switching automatic checks off and going back a version. A check sends only Tiro's version, your Windows or macOS
  version and processor type. This version has to be installed by hand once; after that, updates are automatic.
  On a Mac, Tiro tells you about a new version and opens the download instead (not yet tried on a real Mac).
- **Safe mode**: hold Shift while starting Tiro, or it starts that way by itself after two failed starts.

## 2.0.2 (2026-09-30)

- Mac: Settings no longer says "No NVIDIA GPU found" (Macs don't use NVIDIA's CUDA).

## 2.0.1

- On Macs, speech now runs on the Apple chip's CPU cores with the compact model (about 6× faster than before on
  M1) and the download is 1.8 GB smaller. Core ML stays available as an experimental option.
- Safer corrections: a strong audio mismatch now vetoes a candidate outright, whatever else supports it.

## 2.0

- **A correction pass that learns what you mean.** Rare names, brands, places, games and jargon come out the way you
  write them: from your dictionary, fixes you teach it, and your own dictation history. Candidates are checked
  against the audio, and a small local AI model breaks ties. It only ever swaps a misheard word for one that
  sounds like it, and the rules are enforced in code.
- **Fix last transcription** (Ctrl+Alt+F): correct a word once, and Tiro remembers it.
- **Mac version** (Apple Silicon): a menu-bar app; speech on the Apple chip, AI check on its GPU with MLX.
- **New installer and setup**: hardware detection with a plain-English recommendation, verified resumable
  downloads, a real speed test, microphone, shortcut, permissions on Mac, and a live test.
- **Privacy**: everything stays on your device. You can switch history learning off or clear it with one click,
  and Tiro never learns from password fields.
