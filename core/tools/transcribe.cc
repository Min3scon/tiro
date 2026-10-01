// tiro-transcribe MODEL_DIR FILE.wav [FILE2.wav ...] [--threads N]
// Transcribes 16 kHz mono 16-bit PCM WAV files with the Tiro engine and prints one JSON line per file,
// then a summary with real-time factor and peak memory.
#include <cstdio>
#include <cstdlib>
#include <algorithm>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#include "tiro_core.h"

static bool read_wav(const char *path, std::vector<float> *pcm) {
  std::ifstream f(path, std::ios::binary);
  if (!f) return false;
  std::vector<char> data((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
  if (data.size() < 44 || std::memcmp(data.data(), "RIFF", 4) != 0) return false;
  size_t pos = 12;
  int channels = 1, bits = 16;
  while (pos + 8 <= data.size()) {
    uint32_t size;
    std::memcpy(&size, &data[pos + 4], 4);
    if (std::memcmp(&data[pos], "fmt ", 4) == 0) {
      uint16_t ch, bps;
      std::memcpy(&ch, &data[pos + 10], 2);
      std::memcpy(&bps, &data[pos + 22], 2);
      channels = ch;
      bits = bps;
    } else if (std::memcmp(&data[pos], "data", 4) == 0) {
      if (bits != 16) return false;
      size_t n = std::min<size_t>(size, data.size() - pos - 8) / 2 / channels;
      pcm->resize(n);
      const int16_t *s = reinterpret_cast<const int16_t *>(&data[pos + 8]);
      for (size_t i = 0; i < n; ++i) (*pcm)[i] = s[i * channels] / 32768.0f;
      return true;
    }
    pos += 8 + size + (size & 1);
  }
  return false;
}

int main(int argc, char **argv) {
  setvbuf(stdout, nullptr, _IONBF, 0);
  if (argc < 3) {
    std::fprintf(stderr, "usage: tiro-transcribe MODEL_DIR FILE.wav [...] [--threads N]\n");
    return 2;
  }
  int threads = 0;
  int prepack = 1;
  int spin = 0;
  bool live = false;
  std::string vad;
  std::vector<const char *> files;
  for (int i = 2; i < argc; ++i) {
    if (!std::strcmp(argv[i], "--threads") && i + 1 < argc) {
      threads = std::atoi(argv[++i]);
    } else if (!std::strcmp(argv[i], "--no-prepack")) {
      prepack = 0;
    } else if (!std::strcmp(argv[i], "--spin")) {
      spin = 1;
    } else if (!std::strcmp(argv[i], "--live") && i + 1 < argc) {
      live = true;
      vad = argv[++i];
    } else {
      files.push_back(argv[i]);
    }
  }
  std::string opts = "{\"threads\": " + std::to_string(threads) + ", \"prepack\": " +
                     (prepack ? "true" : "false") + ", \"spin\": " + (spin ? "true" : "false") +
                     (live ? ", \"vad\": \"" + vad + "\"" : std::string()) + "}";
  int64_t base = tiro_process_rss();
  char *err = nullptr;
  tiro_model *model = tiro_model_load(argv[1], opts.c_str(), &err);
  if (!model) {
    std::fprintf(stderr, "load failed: %s\n", err ? err : "?");
    tiro_free(err);
    return 1;
  }
  int64_t loaded = tiro_process_rss();
  double audio_s = 0, proc_ms = 0;
  for (const char *path : files) {
    std::vector<float> pcm;
    if (!read_wav(path, &pcm)) {
      std::fprintf(stderr, "cannot read %s (16-bit PCM WAV at 16 kHz expected)\n", path);
      continue;
    }
    char *out = nullptr;
    if (live) {
      // the dictation pipeline: 80 ms chunks through VAD gating, then a second of silence and the end
      tiro_session *s = tiro_session_begin(model, "{\"partial_ms\": 0}");
      for (size_t i = 0; i < pcm.size(); i += 1280) {
        tiro_session_feed(s, pcm.data() + i, std::min<size_t>(1280, pcm.size() - i));
      }
      std::vector<float> silence(16000, 0.0f);
      tiro_session_feed(s, silence.data(), silence.size());
      tiro_session_finish(s, &out);
      tiro_session_free(s);
      std::printf("{\"file\": \"%s\", \"live\": %s}\n", path, out ? out : "null");
      tiro_free(out);
      audio_s += pcm.size() / 16000.0;
      continue;
    }
    tiro_model_transcribe(model, pcm.data(), pcm.size(), &out);
    std::printf("{\"file\": \"%s\", \"result\": %s}\n", path, out ? out : "null");
    if (out) {
      const char *ms = std::strstr(out, "\"ms\":");
      if (ms) proc_ms += std::atof(ms + 5);
    }
    audio_s += pcm.size() / 16000.0;
    tiro_free(out);
  }
  std::printf("{\"summary\": {\"audio_s\": %.2f, \"rtf\": %.4f, \"rss_base_mb\": %.1f, \"rss_loaded_mb\": %.1f, "
              "\"rss_peak_mb\": %.1f}}\n",
              audio_s, audio_s > 0 ? proc_ms / 1000.0 / audio_s : 0.0, base / 1048576.0, loaded / 1048576.0,
              tiro_process_peak_rss() / 1048576.0);
  tiro_model_free(model);
  return 0;
}
