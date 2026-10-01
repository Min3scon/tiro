// Personal vocabulary for Lite: swap a likely-misheard span for one of the user's own terms, and nothing else.
//
// The same rule as Standard Tiro, enforced in code: a correction may only replace a span of at most 3 words with
// a term from the user's dictionary or a fix the user taught ("heard -> written"). Every other word, its
// punctuation and the word order are left exactly as recognised; validate() checks the result and the original
// text is returned whenever it doesn't hold. There is no language model on Lite, so ordinary words are only ever
// replaced by a term the user explicitly taught.
#pragma once

#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace tiro {

struct Term {
  std::string written;   // as the user writes it: "GeoGuessr"
  std::string letters;   // lowercase letters/digits only: "geoguessr"
  std::string key;       // sound key: "jgsr"
  int words = 1;
};

struct Swap {
  int start = 0;         // first replaced word
  int count = 0;         // words replaced (1..3)
  std::string written;   // the term that replaced them
  std::string why;       // "taught" | "dictionary" | "case"
};

class Corrector {
 public:
  static constexpr int kMaxSpan = 3;

  void set_dictionary(const std::vector<std::string> &terms);
  void set_rules(const std::vector<std::pair<std::string, std::string>> &heard_to_written);
  void set_common_words(const std::vector<std::string> &words);  // most frequent words, most common first
  bool empty() const { return terms_.empty() && rules_.empty(); }

  // Returns the corrected phrase (or the original); `swaps` lists what changed.
  std::string correct(const std::string &phrase, std::vector<Swap> *swaps = nullptr) const;

  // True when `after` differs from `before` only by the listed swaps (the strict rule, checked independently).
  static bool validate(const std::vector<std::string> &before, const std::vector<std::string> &after,
                       const std::vector<Swap> &swaps);

  static std::string sound_key(const std::string &text);
  static std::string letters(const std::string &text);
  static double ratio(const std::string &a, const std::string &b);  // 1 - normalised edit distance

 private:
  bool is_common(const std::string &letters) const;

  std::vector<Term> terms_;
  std::unordered_map<std::string, std::vector<int>> by_key_;   // sound key -> terms
  std::unordered_map<std::string, int> by_letters_;            // letters -> term (spacing/case variants)
  std::unordered_map<std::string, std::string> rules_;         // heard letters -> written
  std::unordered_map<std::string, int> common_;                // letters -> rank
};

}  // namespace tiro
