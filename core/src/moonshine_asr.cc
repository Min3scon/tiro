#include "moonshine_asr.h"

#include <chrono>
#include <cstdlib>
#include <filesystem>

#include "context-biaser.h"
#include "moonshine-streaming-model.h"

namespace tiro {
namespace {

double ms_since(std::chrono::steady_clock::time_point t0) {
  return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
}

// The frontend's two stride-2 convolutions carry state between calls, so every chunk must hold a whole number
// of 4-frame groups (4 x 80 samples) or the stride phase slips and every later frame is wrong. 80 ms chunks
// (16 frames) satisfy that and keep ONNX Runtime calls few; the remainder waits for the next feed.
constexpr size_t kChunk = 1280;
constexpr size_t kAlign = 320;

}  // namespace

MoonshineAsr::MoonshineAsr() : biaser_(std::make_unique<ContextBiaser>()) {}

MoonshineAsr::~MoonshineAsr() {
  delete state_;
}

bool MoonshineAsr::load(const std::string &dir, std::string *error) {
  namespace fs = std::filesystem;
  fs::path root(dir);
  std::string tokenizer = (root / "tokenizer.bin").string();
  if (!fs::exists(root / "streaming_config.json") || !fs::exists(tokenizer)) {
    if (error) *error = "not a Moonshine Streaming model folder: " + dir;
    return false;
  }
  model_ = std::make_unique<MoonshineStreamingModel>();
  if (model_->load(dir.c_str(), tokenizer.c_str(), 0) != 0) {
    model_.reset();
    if (error) *error = "failed to load the model in " + dir;
    return false;
  }
  return true;
}

void MoonshineAsr::set_keyterms(const std::vector<std::string> &terms, float boost) {
  biaser_->clear();
  biaser_->set_boost(boost > 0 ? boost : ContextBiaser::kDefaultBoost);
  biaser_->set_start_scale(0.0f);  // help finish a term, never push the decoder into starting one
  for (const auto &term : terms) {
    for (const auto &variant : ContextBiaser::variants_for_term(term)) {
      auto ids = model_->text_to_tokens(variant);
      if (!ids.empty()) biaser_->add_token_sequence(ids);
    }
  }
}

void MoonshineAsr::begin() {
  delete state_;
  state_ = model_->create_state();
  last_tokens_.clear();
  pending_.clear();
}

bool MoonshineAsr::feed(const float *pcm, size_t samples) {
  if (!state_) begin();
  pending_.insert(pending_.end(), pcm, pcm + samples);
  size_t whole = pending_.size() / kChunk * kChunk;
  if (whole == 0) return true;
  for (size_t off = 0; off < whole; off += kChunk) {
    if (model_->process_audio_chunk(state_, pending_.data() + off, kChunk, nullptr) != 0) return false;
  }
  pending_.erase(pending_.begin(), pending_.begin() + static_cast<long>(whole));
  return model_->encode(state_, false, nullptr) == 0;
}

void MoonshineAsr::flush() {
  // the last few milliseconds: pad with silence to a whole 4-frame group
  if (pending_.empty()) return;
  size_t n = (pending_.size() + kAlign - 1) / kAlign * kAlign;
  pending_.resize(n, 0.0f);
  model_->process_audio_chunk(state_, pending_.data(), n, nullptr);
  pending_.clear();
}

AsrResult MoonshineAsr::decode(bool final_pass) {
  AsrResult r;
  auto t0 = std::chrono::steady_clock::now();
  if (final_pass) flush();
  if (model_->encode(state_, final_pass, nullptr) != 0) return r;
  r.encode_ms = ms_since(t0);
  auto t1 = std::chrono::steady_clock::now();
  int *tokens = nullptr;
  int count = 0;
  model_->decoder_reset(state_);
  ContextBiaser *biaser = biaser_->empty() ? nullptr : biaser_.get();
  const int *draft = last_tokens_.empty() ? nullptr : last_tokens_.data();
  if (model_->decode_full(state_, draft, static_cast<int>(last_tokens_.size()), &tokens, &count, biaser) == 0 &&
      tokens) {
    r.tokens.assign(tokens, tokens + count);
    std::free(tokens);
  }
  r.decode_ms = ms_since(t1);
  last_tokens_ = r.tokens;
  std::vector<int64_t> ids(r.tokens.begin(), r.tokens.end());
  r.text = ids.empty() ? std::string() : model_->tokens_to_text(ids);
  return r;
}

AsrResult MoonshineAsr::partial() {
  if (!state_) return {};
  return decode(false);
}

AsrResult MoonshineAsr::finish() {
  if (!state_) return {};
  AsrResult r = decode(true);
  return r;
}

AsrResult MoonshineAsr::transcribe(const float *pcm, size_t samples) {
  begin();
  auto t0 = std::chrono::steady_clock::now();
  feed(pcm, samples);
  double feed_ms = ms_since(t0);
  AsrResult r = finish();
  r.encode_ms += feed_ms;
  return r;
}

}  // namespace tiro
