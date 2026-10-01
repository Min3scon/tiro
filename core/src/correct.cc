#include "correct.h"

#include <algorithm>
#include <cctype>
#include <regex>

#include "text.h"

namespace tiro {
namespace {

bool is_alnum(unsigned char c) { return std::isalnum(c) || c >= 0x80; }

// Split "word," into ("", "word", ",") and "(Word" into ("(", "Word", "").
void split_punct(const std::string &w, std::string *pre, std::string *core, std::string *post) {
  size_t a = 0, b = w.size();
  while (a < b && !is_alnum(static_cast<unsigned char>(w[a]))) ++a;
  while (b > a && !is_alnum(static_cast<unsigned char>(w[b - 1])) && w[b - 1] != '\'') --b;
  *pre = w.substr(0, a);
  *core = w.substr(a, b - a);
  *post = w.substr(b);
}

bool inner_punct(const std::vector<std::string> &words, int start, int count) {
  for (int i = start; i < start + count - 1; ++i) {
    std::string pre, core, post;
    split_punct(words[i], &pre, &core, &post);
    if (!post.empty()) return true;  // "guess, er" is two clauses, not one name
  }
  for (int i = start + 1; i < start + count; ++i) {
    std::string pre, core, post;
    split_punct(words[i], &pre, &core, &post);
    if (!pre.empty()) return true;
  }
  return false;
}

}  // namespace

std::string Corrector::letters(const std::string &text) {
  std::string out;
  for (unsigned char c : text)
    if (std::isalnum(c) || c >= 0x80) out += static_cast<char>(std::tolower(c));
  return out;
}

std::string Corrector::sound_key(const std::string &text) {
  // Port of Standard's tiro/correct/text.py sound_key (Metaphone-like consonant skeleton).
  std::string w;
  for (unsigned char c : text)
    if (std::isalpha(c)) w += static_cast<char>(std::tolower(c));
  if (w.empty()) return "";
  auto starts = [&](const char *p) { return w.rfind(p, 0) == 0; };
  if (starts("kn") || starts("gn")) w = "n" + w.substr(2);
  else if (starts("wr")) w = "r" + w.substr(2);
  else if (starts("ps")) w = "s" + w.substr(2);
  else if (starts("x")) w = "s" + w.substr(1);
  if (w.size() >= 2 && w.compare(w.size() - 2, 2, "mb") == 0) w = w.substr(0, w.size() - 1);
  static const std::vector<std::pair<std::regex, std::string>> rules = {
      {std::regex("sch"), "sk"}, {std::regex("tch"), "ch"}, {std::regex("ph"), "f"},  {std::regex("gh"), "g"},
      {std::regex("ck"), "k"},   {std::regex("sh"), "x"},   {std::regex("ch"), "x"},  {std::regex("th"), "0"},
      {std::regex("wh"), "w"},   {std::regex("dg"), "j"},   {std::regex("qu"), "kw"}, {std::regex("q"), "k"},
      {std::regex("x"), "ks"},   {std::regex("z"), "s"},    {std::regex("c(?=[eiy])"), "s"}, {std::regex("c"), "k"},
      {std::regex("g(?=[eiy])"), "j"}, {std::regex("v"), "f"}};
  for (const auto &[re, rep] : rules) w = std::regex_replace(w, re, rep);
  std::string out(1, w[0]);
  for (size_t i = 1; i < w.size(); ++i) {
    char c = w[i];
    if (std::string("aeiouywh").find(c) != std::string::npos) continue;
    out += c;
  }
  std::string dedup;
  for (char c : out)
    if (dedup.empty() || dedup.back() != c) dedup += c;
  return dedup;
}

double Corrector::ratio(const std::string &a, const std::string &b) {
  if (a.empty() && b.empty()) return 1.0;
  std::vector<int> prev(b.size() + 1), cur(b.size() + 1);
  for (size_t j = 0; j <= b.size(); ++j) prev[j] = static_cast<int>(j);
  for (size_t i = 1; i <= a.size(); ++i) {
    cur[0] = static_cast<int>(i);
    for (size_t j = 1; j <= b.size(); ++j) {
      int sub = prev[j - 1] + (a[i - 1] == b[j - 1] ? 0 : 1);
      cur[j] = std::min({prev[j] + 1, cur[j - 1] + 1, sub});
    }
    std::swap(prev, cur);
  }
  double d = prev[b.size()];
  return 1.0 - d / static_cast<double>(std::max(a.size(), b.size()));
}

void Corrector::set_dictionary(const std::vector<std::string> &terms) {
  terms_.clear();
  by_key_.clear();
  by_letters_.clear();
  for (const auto &t : terms) {
    Term term;
    term.written = t;
    term.letters = letters(t);
    term.key = sound_key(t);
    term.words = static_cast<int>(split_words(t).size());
    if (term.letters.size() < 3 || term.key.empty()) continue;  // too short to match safely
    int idx = static_cast<int>(terms_.size());
    terms_.push_back(term);
    by_key_[term.key].push_back(idx);
    by_letters_.emplace(term.letters, idx);
  }
}

void Corrector::set_rules(const std::vector<std::pair<std::string, std::string>> &rules) {
  rules_.clear();
  for (const auto &[heard, written] : rules) {
    std::string k = letters(heard);
    if (!k.empty() && !written.empty()) rules_[k] = written;
  }
}

void Corrector::set_common_words(const std::vector<std::string> &words) {
  common_.clear();
  for (size_t i = 0; i < words.size(); ++i) common_.emplace(letters(words[i]), static_cast<int>(i));
}

bool Corrector::is_common(const std::string &l) const { return common_.count(l) > 0; }

std::string Corrector::correct(const std::string &phrase, std::vector<Swap> *out_swaps) const {
  std::vector<std::string> words = split_words(phrase);
  if (words.empty() || empty()) return phrase;
  std::vector<Swap> swaps;
  std::vector<bool> used(words.size(), false);
  // Longest spans first, so "geo guesser" beats "guesser".
  for (int len = kMaxSpan; len >= 1; --len) {
    for (int i = 0; i + len <= static_cast<int>(words.size()); ++i) {
      bool overlap = false;
      for (int k = i; k < i + len; ++k) overlap |= used[k];
      if (overlap || inner_punct(words, i, len)) continue;
      std::string span, pre, post, core;
      std::vector<std::string> cores;
      for (int k = i; k < i + len; ++k) {
        std::string p, c, q;
        split_punct(words[k], &p, &c, &q);
        if (k == i) pre = p;
        if (k == i + len - 1) post = q;
        cores.push_back(c);
        span += (span.empty() ? "" : " ") + c;
      }
      std::string sl = letters(span);
      if (sl.size() < 3) continue;
      std::string written, why;
      if (auto r = rules_.find(sl); r != rules_.end()) {
        written = r->second;
        why = "taught";
      } else if (auto t = by_letters_.find(sl); t != by_letters_.end()) {
        // same letters, different spacing or case: "geo guessr" / "kubernetes" -> the user's spelling
        const Term &term = terms_[t->second];
        if (span != term.written) {
          bool all_common = true;
          for (const auto &c : cores) all_common &= is_common(letters(c));
          // "among us" stays two ordinary words unless the user taught otherwise (and without a common-word
          // list, multi-word changes are never made)
          if (len == 1 || (!common_.empty() && !all_common)) {
            written = term.written;
            why = "case";
          }
        }
      } else if (auto kt = by_key_.find(sound_key(span)); kt != by_key_.end() && !common_.empty()) {
        // sounds the same: only when something in the span is not an ordinary word (a likely mishearing)
        bool any_uncommon = false;
        for (const auto &c : cores) any_uncommon |= !is_common(letters(c));
        if (!any_uncommon) continue;
        double best = 0;
        for (int idx : kt->second) {
          const Term &term = terms_[idx];
          if (std::abs(term.words - len) > 1) continue;
          double r = ratio(sl, term.letters);
          if (r > best) {
            best = r;
            written = term.written;
          }
        }
        if (best < 0.6) written.clear();  // same consonants but clearly a different word
        why = "dictionary";
      }
      if (written.empty()) continue;
      words[i] = pre + written + post;
      for (int k = i + 1; k < i + len; ++k) words[k].clear();
      for (int k = i; k < i + len; ++k) used[k] = true;
      swaps.push_back({i, len, written, why});
    }
  }
  if (swaps.empty()) return phrase;
  std::vector<std::string> after;
  for (const auto &w : words)
    if (!w.empty()) after.push_back(w);
  std::sort(swaps.begin(), swaps.end(), [](const Swap &a, const Swap &b) { return a.start < b.start; });
  if (!validate(split_words(phrase), after, swaps)) return phrase;  // the rule is enforced, whatever happened above
  if (out_swaps) *out_swaps = swaps;
  std::string out;
  for (const auto &w : after) out += (out.empty() ? "" : " ") + w;
  return out;
}

bool Corrector::validate(const std::vector<std::string> &before, const std::vector<std::string> &after,
                         const std::vector<Swap> &swaps) {
  size_t bi = 0, ai = 0;
  for (const auto &s : swaps) {
    if (s.count < 1 || s.count > kMaxSpan || s.written.empty()) return false;
    if (static_cast<size_t>(s.start) < bi) return false;
    while (bi < static_cast<size_t>(s.start)) {  // untouched words must be identical
      if (ai >= after.size() || after[ai] != before[bi]) return false;
      ++ai, ++bi;
    }
    if (bi + s.count > before.size() || ai >= after.size()) return false;
    std::string pre_b, core_b, post_b, pre_a, core_a, post_a;
    split_punct(before[bi], &pre_b, &core_b, &post_b);
    split_punct(before[bi + s.count - 1], &pre_a, &core_a, &post_a);
    std::string expect = pre_b + s.written + post_a;  // the span's outer punctuation survives
    if (after[ai] != expect) return false;
    ++ai;
    bi += s.count;
  }
  while (bi < before.size()) {
    if (ai >= after.size() || after[ai] != before[bi]) return false;
    ++ai, ++bi;
  }
  return ai == after.size();
}

}  // namespace tiro
