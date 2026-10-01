#include "text.h"

#include <algorithm>
#include <cctype>
#include <set>

namespace tiro {
namespace {

const std::set<std::string> &fillers() {
  static const std::set<std::string> s = {"um", "umm", "uhm", "uh", "uhh", "erm", "er"};
  return s;
}

// Words that are only capitalised because they start a sentence (same list as tiro/textproc.py).
const std::set<std::string> &common_lower() {
  static const std::set<std::string> s = {
      "a", "about", "after", "again", "all", "also", "although", "always", "an", "and", "another", "any", "anyway",
      "are", "as", "at", "be", "because", "been", "before", "being", "both", "but", "by", "can", "could", "did",
      "do", "does", "either", "even", "every", "for", "from", "had", "has", "have", "he", "her", "here", "his",
      "how", "however", "if", "in", "into", "is", "it", "its", "it's", "just", "let", "let's", "like", "maybe",
      "more", "most", "much", "my", "neither", "no", "nor", "not", "now", "of", "on", "once", "only", "or",
      "other", "our", "out", "over", "perhaps", "please", "probably", "really", "she", "should", "since", "so",
      "some", "still", "such", "than", "that", "that's", "the", "their", "them", "then", "there", "there's",
      "these", "they", "they're", "this", "those", "though", "through", "to", "too", "under", "unless", "until",
      "us", "very", "was", "we", "we're", "well", "were", "what", "when", "where", "whether", "which", "while",
      "who", "why", "will", "with", "would", "yet", "you", "you're", "your"};
  return s;
}

// After a short pause, a phrase starting with one of these continues the previous sentence.
const std::set<std::string> &continuations() {
  static const std::set<std::string> s = {"and",    "but",   "or",    "so",      "because", "which", "that",
                                          "then",   "if",    "when",  "while",   "though",  "although",
                                          "unless", "until", "since", "whereas", "as",      "than",  "nor",
                                          "to",     "of",    "for",   "with",    "like",    "who",   "where"};
  return s;
}

const char *kNewline = "\n";
const char *kParagraph = "\n\n";

bool is_break(const std::string &t) { return t == kNewline || t == kParagraph; }

bool is_terminal(char c) { return c == '.' || c == '?' || c == '!'; }
bool is_punct(char c) { return c == '.' || c == ',' || c == ';' || c == ':' || c == '?' || c == '!'; }

bool trailing_punct(const std::string &w) { return !w.empty() && is_punct(w.back()); }

// first ASCII letter index (non-ASCII letters are left alone)
int first_alpha(const std::string &w) {
  for (size_t i = 0; i < w.size(); ++i)
    if (std::isalpha(static_cast<unsigned char>(w[i]))) return static_cast<int>(i);
  return -1;
}

std::string capitalize(std::string w) {
  int i = first_alpha(w);
  if (i >= 0) w[i] = static_cast<char>(std::toupper(static_cast<unsigned char>(w[i])));
  return w;
}

std::string decapitalize(std::string w) {
  if (!common_lower().count(norm_word(w))) return w;
  int letters = 0, upper = 0;
  for (char c : w)
    if (std::isalpha(static_cast<unsigned char>(c))) {
      ++letters;
      upper += std::isupper(static_cast<unsigned char>(c)) ? 1 : 0;
    }
  if (letters > 1 && upper == letters) return w;  // acronym or shouting
  int i = first_alpha(w);
  if (i >= 0) w[i] = static_cast<char>(std::tolower(static_cast<unsigned char>(w[i])));
  return w;
}

bool needs_capital(const std::string &w) {
  bool first = true;
  for (char c : w) {
    if (!std::isalpha(static_cast<unsigned char>(c))) continue;
    if (first) {
      if (!std::islower(static_cast<unsigned char>(c))) return false;
      first = false;
    } else if (std::isupper(static_cast<unsigned char>(c))) {
      return false;  // deliberately mixed case: iPhone, eBay
    }
  }
  return !first;
}

std::vector<std::string> remove_fillers(const std::vector<std::string> &tokens, bool at_start) {
  std::vector<std::string> out;
  bool cap_next = false;
  for (const auto &tok : tokens) {
    if (fillers().count(norm_word(tok))) {
      std::string terminal;
      size_t k = tok.size();
      while (k > 0 && is_terminal(tok[k - 1])) --k;
      terminal = tok.substr(k);
      if (!terminal.empty() && !out.empty() && !trailing_punct(out.back())) out.back() += terminal;
      bool starts = (out.empty() && at_start) || (!out.empty() && ends_sentence(out.back()));
      if (starts || !terminal.empty()) cap_next = true;
      continue;
    }
    out.push_back(cap_next ? capitalize(tok) : tok);
    cap_next = false;
  }
  return out;
}

std::vector<std::string> voice_commands(const std::vector<std::string> &tokens, const std::string *prev) {
  static const std::vector<std::pair<std::vector<std::string>, const char *>> cmds = {
      {{"new", "paragraph"}, kParagraph}, {{"new", "line"}, kNewline}, {{"newline"}, kNewline},
      {{"next", "line"}, kNewline}};
  std::vector<std::string> out;
  bool cap_next = false;
  size_t i = 0;
  while (i < tokens.size()) {
    bool matched = false;
    for (const auto &[phrase, repl] : cmds) {
      size_t n = phrase.size();
      if (i + n > tokens.size()) continue;
      bool same = true;
      for (size_t j = 0; j < n && same; ++j) same = norm_word(tokens[i + j]) == phrase[j];
      if (!same) continue;
      const std::string *before = out.empty() ? prev : &out.back();
      bool at_boundary = !before || is_break(*before) || trailing_punct(*before);
      bool closes = i + n == tokens.size() || trailing_punct(tokens[i + n - 1]);
      bool inner_clean = true;
      for (size_t j = 0; j + 1 < n; ++j) inner_clean &= !trailing_punct(tokens[i + j]);
      if (at_boundary && closes && inner_clean) {
        out.emplace_back(repl);
        i += n;
        cap_next = true;
        matched = true;
        break;
      }
    }
    if (matched) continue;
    out.push_back(cap_next ? capitalize(tokens[i]) : tokens[i]);
    cap_next = false;
    ++i;
  }
  return out;
}

void normalize_titles(std::vector<std::string> &tokens) {
  for (size_t i = 0; i + 1 < tokens.size(); ++i) {
    std::string low = tokens[i];
    std::transform(low.begin(), low.end(), low.begin(), [](unsigned char c) { return std::tolower(c); });
    const char *title = low == "mister" ? "Mr." : low == "missus" ? "Mrs." : nullptr;
    int a = first_alpha(tokens[i + 1]);
    if (title && a >= 0 && std::isupper(static_cast<unsigned char>(tokens[i + 1][a]))) tokens[i] = title;
  }
}

}  // namespace

std::vector<std::string> split_words(const std::string &s) {
  std::vector<std::string> out;
  std::string cur;
  for (char c : s) {
    if (c == ' ' || c == '\t' || c == '\r' || c == '\n') {
      if (!cur.empty()) out.push_back(cur), cur.clear();
    } else {
      cur += c;
    }
  }
  if (!cur.empty()) out.push_back(cur);
  return out;
}

std::string norm_word(const std::string &w) {
  std::string out;
  for (unsigned char c : w) {
    if (std::isalnum(c) || c == '\'' || c >= 0x80) out += static_cast<char>(std::tolower(c));
  }
  return out;
}

bool ends_sentence(const std::string &w) {
  size_t k = w.size();
  while (k > 0 && (w[k - 1] == '"' || w[k - 1] == '\'' || w[k - 1] == ')' || w[k - 1] == ']')) --k;
  return k > 0 && is_terminal(w[k - 1]);
}

TextAssembler::TextAssembler(const FormatOptions &opts, bool needs_space, bool mid_sentence)
    : opts_(opts), needs_space_(needs_space), mid_sentence_(mid_sentence) {}

std::string TextAssembler::add_phrase(const std::string &phrase, double gap_ms) {
  std::vector<std::string> tokens = split_words(phrase);
  if (tokens.empty()) return "";
  std::string out;
  // Decide what the previous phrase's held-back punctuation was.
  if (!held_.empty()) {
    std::string first = norm_word(tokens[0]);
    bool continues = gap_ms < 1200 && continuations().count(first) > 0;
    if (continues) {
      tokens[0] = decapitalize(tokens[0]);
      // the sentence goes on: the held . is dropped; ? and ! are kept (they're rarely wrong)
      if (held_ != ".") {
        out += held_;
        last_token_ += held_;
        text_ += held_;
      }
    } else {
      out += held_;
      last_token_ += held_;
      text_ += held_;
    }
    held_.clear();
  }
  // Hold back this phrase's own final punctuation.
  std::string &last = tokens.back();
  size_t k = last.size();
  while (k > 0 && is_terminal(last[k - 1])) --k;
  if (k < last.size() && k > 0) {
    held_ = last.substr(k);
    last = last.substr(0, k);
  }
  out += add_tokens(std::move(tokens));  // appends its own part to text_
  if (has_last_ && is_break(last_token_)) held_.clear();  // "new paragraph." : the full stop belongs to the command
  return out;
}

std::string TextAssembler::finish() {
  std::string out = held_;
  held_.clear();
  if (!out.empty()) {
    text_ += out;
    last_token_ += out;
  }
  return out;
}

std::string TextAssembler::add_tokens(std::vector<std::string> tokens) {
  bool at_start = !has_last_ ? !mid_sentence_ : (is_break(last_token_) || ends_sentence(last_token_));
  if (opts_.remove_fillers) tokens = remove_fillers(tokens, at_start);
  if (opts_.voice_commands) tokens = voice_commands(tokens, has_last_ ? &last_token_ : nullptr);
  if (tokens.empty()) return "";
  normalize_titles(tokens);
  if (!has_last_ && mid_sentence_ && !is_break(tokens[0])) tokens[0] = decapitalize(tokens[0]);
  std::string out;
  bool have_prev = has_last_;
  std::string prev = last_token_;
  for (auto &tok : tokens) {
    bool brk = is_break(tok);
    if (!brk && needs_capital(tok)) {
      bool starts = (!have_prev && !mid_sentence_) || (have_prev && (is_break(prev) || ends_sentence(prev)));
      if (starts) tok = capitalize(tok);
    }
    std::string sep;
    if (!have_prev) {
      sep = (needs_space_ && !brk) ? " " : "";
    } else if (brk || is_break(prev)) {
      sep = "";
    } else {
      sep = " ";
    }
    out += sep + tok;
    prev = tok;
    have_prev = true;
  }
  last_token_ = prev;
  has_last_ = true;
  text_ += out;
  return out;
}

}  // namespace tiro
