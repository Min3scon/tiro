// Silero VAD v5 (MIT): speech probability for each 32 ms window of 16 kHz audio.
#pragma once

#include <array>
#include <string>
#include <vector>

struct OrtApi;
struct OrtEnv;
struct OrtSession;
struct OrtMemoryInfo;

namespace tiro {

class Vad {
 public:
  static constexpr int kWindow = 512;  // samples per decision (32 ms)
  static constexpr int kContext = 64;  // samples carried over from the previous window

  Vad() = default;
  ~Vad();
  Vad(const Vad &) = delete;
  Vad &operator=(const Vad &) = delete;

  bool load(const std::string &path, std::string *error);
  bool loaded() const { return session_ != nullptr; }
  void reset();
  // Probability that the 512 samples at `window` contain speech.
  float prob(const float *window);

 private:
  const OrtApi *api_ = nullptr;
  OrtEnv *env_ = nullptr;
  OrtSession *session_ = nullptr;
  OrtMemoryInfo *mem_ = nullptr;
  std::array<float, 2 * 128> state_{};
  std::array<float, kContext + kWindow> input_{};
};

}  // namespace tiro
