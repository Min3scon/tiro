// Turns recognised phrases into the exact text to type (a port of Standard Tiro's tiro/textproc.py, plus the
// phrase joining Lite needs): filler removal, "new line"/"new paragraph" commands, "mister" -> "Mr.", spacing,
// capitals at sentence starts, and continuing a sentence the user was already typing.
//
// Only stable text is ever typed. The recogniser ends every phrase as if it were a sentence ("... so I think."),
// but a pause is not always the end of a sentence, so a phrase's final . ? or ! is held back until the next
// phrase (or the end of the dictation) shows whether the sentence really ended. Nothing typed is ever rewritten.
#pragma once

#include <string>
#include <vector>

namespace tiro {

struct FormatOptions {
  bool remove_fillers = true;
  bool voice_commands = true;
};

class TextAssembler {
 public:
  // needs_space: the cursor follows a word (type a space first). mid_sentence: continuing an unfinished sentence.
  TextAssembler(const FormatOptions &opts, bool needs_space = false, bool mid_sentence = false);

  // A phrase was committed after `gap_ms` of silence since the previous one. Returns the text to type now.
  std::string add_phrase(const std::string &phrase, double gap_ms);
  // End of the dictation: returns the held-back final punctuation, if any.
  std::string finish();

  const std::string &text() const { return text_; }

 private:
  std::string add_tokens(std::vector<std::string> tokens);

  FormatOptions opts_;
  bool needs_space_;
  bool mid_sentence_;
  std::string text_;
  std::string last_token_;  // empty = nothing typed yet
  bool has_last_ = false;
  std::string held_;        // trailing . ? ! of the last phrase, not typed yet
};

// Helpers shared with the correction pass and tests.
std::vector<std::string> split_words(const std::string &s);
std::string norm_word(const std::string &w);
bool ends_sentence(const std::string &w);

}  // namespace tiro
