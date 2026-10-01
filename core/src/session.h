// One dictation: audio in, partial and committed text out.
//
// Voice-activity detection gates the recogniser: silence never reaches it (no hallucinated words on silence),
// and each pause ends a phrase, which is decoded once more in full and committed. While a phrase is in
// progress, a cheaper partial hypothesis is produced every `partial_ms` for the overlay.
#pragma once

#include <deque>
#include <string>
#include <vector>

#include "correct.h"
#include "moonshine_asr.h"
#include "text.h"
#include "vad.h"

namespace tiro {

struct SessionOptions {
  float vad_on = 0.5f;          // start of speech when the probability crosses this
  float vad_off = 0.35f;        // ... and it counts as silence again below this
  int start_ms = 64;            // speech must last this long to open a phrase
  int preroll_ms = 320;         // audio kept from before the detected start (soft first syllables)
  int pause_ms = 600;           // a pause this long commits the phrase
  int tail_ms = 160;            // silence kept after the last speech in a phrase
  int max_phrase_ms = 24000;    // long monologues are committed at the next short dip
  int partial_ms = 480;         // 0 disables partial results
  int min_speech_ms = 280;      // phrases with less speech than this are dropped (clicks, breaths)
  bool needs_space = false;     // the cursor follows a word: type a space before the first word
  bool mid_sentence = false;    // the cursor is inside an unfinished sentence: don't capitalise the first word
  FormatOptions format;
};

struct Event {
  enum Kind { kPartial, kCommit } kind;
  std::string text;   // what was recognised (partial or committed phrase)
  std::string type;   // commit only: the exact text to type now
  std::vector<Swap> swaps;  // commit only: vocabulary corrections made in this phrase
  int phrase = 0;
  double audio_ms = 0;    // audio in the phrase
  double decode_ms = 0;   // time spent on the final decode of the phrase
  double latency_ms = 0;  // from the end of speech being detected to the commit
};

class Session {
 public:
  Session(MoonshineAsr *asr, Vad *vad, const SessionOptions &opts, const Corrector *corrector = nullptr);
  void feed(const float *pcm, size_t n);
  std::vector<Event> take_events();
  // End of dictation (key released): commits whatever is in progress. Returns all committed text.
  std::string finish();

  int phrases() const { return phrase_; }
  double speech_ms() const { return speech_samples_ / 16.0; }

 private:
  void on_window(const float *w, float p);
  void open_phrase();
  void close_phrase();
  void push_asr(const float *pcm, size_t n);

  MoonshineAsr *asr_;
  Vad *vad_;
  const Corrector *corrector_;
  SessionOptions o_;
  std::vector<float> pending_;   // samples not yet a whole VAD window
  std::deque<float> preroll_;    // recent audio while idle
  bool in_phrase_ = false;
  int above_ = 0;                // consecutive windows above vad_on while idle
  int silence_ = 0;              // consecutive windows below vad_off in a phrase
  std::vector<float> held_;      // audio after the last speech, held back until speech resumes or the phrase ends
  TextAssembler assembler_;
  double last_end_ = -1;       // sample position where the previous phrase's speech ended
  double phrase_start_ = 0;
  double clock_ = 0;           // samples seen so far
  double phrase_samples_ = 0;
  double phrase_speech_samples_ = 0;
  double since_partial_ = 0;
  double speech_samples_ = 0;
  int phrase_ = 0;
  std::vector<Event> events_;
  std::vector<std::string> committed_;
};

}  // namespace tiro
