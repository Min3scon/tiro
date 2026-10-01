// Moonshine Streaming speech model (tiny / small / medium and Tiro's distilled variants).
#pragma once

#include <memory>
#include <string>
#include <vector>

struct MoonshineStreamingModel;
struct MoonshineStreamingState;
class ContextBiaser;

namespace tiro {

struct AsrResult {
  std::string text;
  std::vector<int> tokens;
  double encode_ms = 0;
  double decode_ms = 0;
};

class MoonshineAsr {
 public:
  MoonshineAsr();
  ~MoonshineAsr();
  MoonshineAsr(const MoonshineAsr &) = delete;
  MoonshineAsr &operator=(const MoonshineAsr &) = delete;

  // Loads frontend/encoder/adapter/cross_kv/decoder_kv(.ort) + streaming_config.json + tokenizer.bin.
  bool load(const std::string &dir, std::string *error);

  // Terms to bias decoding towards (names, brands, jargon), written the way they should come out.
  void set_keyterms(const std::vector<std::string> &terms, float boost);

  // Whole-utterance transcription.
  AsrResult transcribe(const float *pcm, size_t samples);

  // Streaming: begin, feed audio as it arrives, ask for the current hypothesis or the final one.
  void begin();
  bool feed(const float *pcm, size_t samples);
  AsrResult partial();
  AsrResult finish();

 private:
  AsrResult decode(bool final_pass);
  void flush();

  std::vector<float> pending_;  // audio waiting for a whole 80 ms chunk

  std::unique_ptr<MoonshineStreamingModel> model_;
  MoonshineStreamingState *state_ = nullptr;
  std::unique_ptr<ContextBiaser> biaser_;
  std::vector<int> last_tokens_;  // previous hypothesis, used as a speculative draft for the next decode
};

}  // namespace tiro
