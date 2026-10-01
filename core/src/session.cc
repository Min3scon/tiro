#include "session.h"

#include <algorithm>
#include <chrono>

namespace tiro {
namespace {

constexpr int kWin = Vad::kWindow;
inline int windows(int ms) { return std::max(1, ms * 16 / kWin); }

std::string trim(const std::string &s) {
  size_t a = s.find_first_not_of(" \t\r\n");
  if (a == std::string::npos) return {};
  size_t b = s.find_last_not_of(" \t\r\n");
  return s.substr(a, b - a + 1);
}

}  // namespace

Session::Session(MoonshineAsr *asr, Vad *vad, const SessionOptions &opts, const Corrector *corrector)
    : asr_(asr), vad_(vad), corrector_(corrector), o_(opts), assembler_(opts.format, opts.needs_space, opts.mid_sentence) {
  if (vad_) vad_->reset();
}

void Session::feed(const float *pcm, size_t n) {
  pending_.insert(pending_.end(), pcm, pcm + n);
  size_t off = 0;
  while (pending_.size() - off >= static_cast<size_t>(kWin)) {
    const float *w = pending_.data() + off;
    float p = vad_ && vad_->loaded() ? vad_->prob(w) : 1.0f;
    on_window(w, p);
    clock_ += kWin;
    off += kWin;
  }
  pending_.erase(pending_.begin(), pending_.begin() + static_cast<long>(off));
}

void Session::push_asr(const float *pcm, size_t n) {
  asr_->feed(pcm, n);
  phrase_samples_ += static_cast<double>(n);
  since_partial_ += static_cast<double>(n);
  if (o_.partial_ms > 0 && since_partial_ >= o_.partial_ms * 16.0) {
    since_partial_ = 0;
    AsrResult r = asr_->partial();
    events_.push_back({Event::kPartial, trim(r.text), "", {}, phrase_, phrase_samples_ / 16.0, r.decode_ms, 0});
  }
}

void Session::open_phrase() {
  in_phrase_ = true;
  phrase_start_ = clock_ - static_cast<double>(preroll_.size());
  silence_ = 0;
  phrase_samples_ = phrase_speech_samples_ = since_partial_ = 0;
  held_.clear();
  asr_->begin();
  std::vector<float> pre(preroll_.begin(), preroll_.end());
  preroll_.clear();
  if (!pre.empty()) push_asr(pre.data(), pre.size());
}

void Session::close_phrase() {
  // keep a little of the trailing silence: the encoder looks 320 ms ahead and endings sound cut off without it
  size_t tail = std::min(held_.size(), static_cast<size_t>(o_.tail_ms * 16));
  if (tail) asr_->feed(held_.data(), tail);
  held_.clear();
  in_phrase_ = false;
  above_ = 0;
  auto t0 = std::chrono::steady_clock::now();
  AsrResult r = asr_->finish();
  double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
  std::string text = trim(r.text);
  if (phrase_speech_samples_ < o_.min_speech_ms * 16.0) text.clear();
  ++phrase_;
  std::string type;
  std::vector<Swap> swaps;
  if (!text.empty() && corrector_ && !corrector_->empty()) text = corrector_->correct(text, &swaps);
  if (!text.empty()) {
    committed_.push_back(text);
    double gap_ms = last_end_ < 0 ? 0.0 : std::max(0.0, (phrase_start_ - last_end_) / 16.0);
    type = assembler_.add_phrase(text, gap_ms);
    last_end_ = clock_ - static_cast<double>(silence_) * kWin;
  }
  events_.push_back({Event::kCommit, text, type, swaps, phrase_ - 1, phrase_samples_ / 16.0, r.decode_ms, ms});
}

void Session::on_window(const float *w, float p) {
  if (!in_phrase_) {
    preroll_.insert(preroll_.end(), w, w + kWin);
    size_t keep = static_cast<size_t>(o_.preroll_ms * 16 + kWin * windows(o_.start_ms));
    while (preroll_.size() > keep) preroll_.pop_front();
    above_ = p >= o_.vad_on ? above_ + 1 : 0;
    if (above_ >= windows(o_.start_ms)) {
      open_phrase();
      phrase_speech_samples_ += kWin * above_;
      speech_samples_ += kWin * above_;
    }
    return;
  }
  if (p >= o_.vad_off) {
    // speech (again): release anything held back during a short dip, then this window
    if (!held_.empty()) {
      push_asr(held_.data(), held_.size());
      held_.clear();
    }
    silence_ = 0;
    push_asr(w, kWin);
    phrase_speech_samples_ += kWin;
    speech_samples_ += kWin;
    if (phrase_samples_ >= o_.max_phrase_ms * 16.0 && p < o_.vad_on) close_phrase();
    return;
  }
  held_.insert(held_.end(), w, w + kWin);
  ++silence_;
  bool long_phrase = phrase_samples_ >= o_.max_phrase_ms * 16.0;
  if (silence_ >= windows(o_.pause_ms) || (long_phrase && silence_ >= 2)) close_phrase();
}

std::vector<Event> Session::take_events() {
  std::vector<Event> out;
  out.swap(events_);
  return out;
}

std::string Session::finish() {
  if (!pending_.empty() && in_phrase_) {  // the last partial window
    push_asr(pending_.data(), pending_.size());
    pending_.clear();
  }
  if (in_phrase_) close_phrase();
  std::string tail = assembler_.finish();
  if (!tail.empty()) events_.push_back({Event::kCommit, "", tail, {}, phrase_, 0, 0, 0});
  return assembler_.text();
}

}  // namespace tiro
