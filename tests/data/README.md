# Test audio

- `dummy/`: 73 utterances from LibriSpeech dev-clean (https://www.openslr.org/12), CC-BY-4.0, by Vassil Panayotov,
  Guoguo Chen, Daniel Povey and Sanjeev Khudanpur. `refs.json` holds the reference transcripts.
- `long/`: a longer recording assembled from the same LibriSpeech clips, for streaming tests.
- `rare/`, `tts/`: synthesized with Windows text-to-speech voices (`dev/make_rare_words.ps1`).

The larger accuracy test sets (`tests/accuracy/*.json`) are synthesized on demand by `dev/accuracy.py`.
