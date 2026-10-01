#include <chrono>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>

#include <fstream>

#include "correct.h"
#include "moonshine_asr.h"
#include "nlohmann/json.hpp"
#include "ort_env.h"
#include "session.h"
#include "tiro_core.h"
#include "vad.h"

using json = nlohmann::json;

struct tiro_model {
  tiro::MoonshineAsr asr;
  tiro::Vad vad;
  tiro::Corrector corrector;
};

struct tiro_session {
  tiro_model *model;
  std::unique_ptr<tiro::Session> session;
};

namespace {

char *dup(const std::string &s) {
  char *p = static_cast<char *>(std::malloc(s.size() + 1));
  if (p) std::memcpy(p, s.c_str(), s.size() + 1);
  return p;
}

void set_error(char **error, const std::string &msg) {
  if (error) *error = dup(msg);
}

json parse_or_empty(const char *s) {
  if (!s || !*s) return json::object();
  try {
    return json::parse(s);
  } catch (...) {
    return json::object();
  }
}

json swaps_json(const std::vector<tiro::Swap> &swaps) {
  json arr = json::array();
  for (const auto &w : swaps) {
    arr.push_back({{"start", w.start}, {"count", w.count}, {"written", w.written}, {"why", w.why}});
  }
  return arr;
}

json to_json_events(const std::vector<tiro::Event> &events) {
  json arr = json::array();
  for (const auto &e : events) {
    arr.push_back({{"type", e.kind == tiro::Event::kPartial ? "partial" : "commit"},
                   {"text", e.text},
                   {"type", e.type},
                   {"swaps", swaps_json(e.swaps)},
                   {"phrase", e.phrase},
                   {"audio_ms", e.audio_ms},
                   {"decode_ms", e.decode_ms},
                   {"latency_ms", e.latency_ms}});
  }
  return arr;
}

}  // namespace

extern "C" {

const char *tiro_version(void) { return "0.1.0"; }

tiro_model *tiro_model_load(const char *model_dir, const char *options_json, char **error) {
  if (error) *error = nullptr;
  json opts;
  try {
    opts = options_json && *options_json ? json::parse(options_json) : json::object();
  } catch (const std::exception &e) {
    set_error(error, std::string("bad options: ") + e.what());
    return nullptr;
  }
  tiro::RuntimePolicy policy;
  policy.intra_threads = opts.value("threads", 0);
  policy.allow_spinning = opts.value("spin", false);
  policy.prepack = opts.value("prepack", false);
  tiro::set_runtime_policy(policy);

  auto m = std::make_unique<tiro_model>();
  std::string err;
  if (!m->asr.load(model_dir ? model_dir : "", &err)) {
    set_error(error, err);
    return nullptr;
  }
  if (opts.contains("keyterms")) {
    m->asr.set_keyterms(opts["keyterms"].get<std::vector<std::string>>(), opts.value("keyterm_boost", 0.0f));
  }
  std::string vad = opts.value("vad", std::string());
  if (!vad.empty() && !m->vad.load(vad, &err)) {
    set_error(error, err);
    return nullptr;
  }
  return m.release();
}

void tiro_model_free(tiro_model *model) { delete model; }

int tiro_model_set_vocabulary(tiro_model *model, const char *vocabulary_json) {
  if (!model) return TIRO_ERROR;
  json v = parse_or_empty(vocabulary_json);
  std::vector<std::string> terms = v.value("terms", std::vector<std::string>{});
  std::vector<std::pair<std::string, std::string>> rules;
  if (v.contains("rules")) {
    for (const auto &r : v["rules"]) rules.emplace_back(r.value("heard", ""), r.value("written", ""));
  }
  std::vector<std::string> bias = terms;
  for (const auto &rule : rules) bias.push_back(rule.second);
  model->asr.set_keyterms(bias, v.value("boost", 0.0f));
  model->corrector.set_dictionary(terms);
  model->corrector.set_rules(rules);
  std::string file = v.value("common_words_file", std::string());
  if (!file.empty()) {
    std::ifstream f(file);
    std::vector<std::string> words;
    for (std::string line; std::getline(f, line);) {
      if (!line.empty() && line.back() == '\r') line.pop_back();
      if (!line.empty()) words.push_back(line);
    }
    model->corrector.set_common_words(words);
  } else if (v.contains("common_words")) {
    model->corrector.set_common_words(v["common_words"].get<std::vector<std::string>>());
  }
  return TIRO_OK;
}

int tiro_model_transcribe(tiro_model *model, const float *pcm, size_t samples, char **result_json) {
  if (!model || !result_json) return TIRO_ERROR;
  auto t0 = std::chrono::steady_clock::now();
  tiro::AsrResult r = model->asr.transcribe(pcm, samples);
  double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
  json out = {{"text", r.text},
              {"tokens", r.tokens.size()},
              {"ms", ms},
              {"encode_ms", r.encode_ms},
              {"decode_ms", r.decode_ms}};
  *result_json = dup(out.dump());
  return TIRO_OK;
}

tiro_session *tiro_session_begin(tiro_model *model, const char *options_json) {
  if (!model) return nullptr;
  json o = parse_or_empty(options_json);
  tiro::SessionOptions so;
  so.vad_on = o.value("vad_on", so.vad_on);
  so.vad_off = o.value("vad_off", so.vad_off);
  so.start_ms = o.value("start_ms", so.start_ms);
  so.preroll_ms = o.value("preroll_ms", so.preroll_ms);
  so.pause_ms = o.value("pause_ms", so.pause_ms);
  so.tail_ms = o.value("tail_ms", so.tail_ms);
  so.max_phrase_ms = o.value("max_phrase_ms", so.max_phrase_ms);
  so.partial_ms = o.value("partial_ms", so.partial_ms);
  so.min_speech_ms = o.value("min_speech_ms", so.min_speech_ms);
  so.needs_space = o.value("needs_space", so.needs_space);
  so.mid_sentence = o.value("mid_sentence", so.mid_sentence);
  so.format.remove_fillers = o.value("remove_fillers", so.format.remove_fillers);
  so.format.voice_commands = o.value("voice_commands", so.format.voice_commands);
  auto *s = new tiro_session{model, nullptr};
  s->session = std::make_unique<tiro::Session>(&model->asr, model->vad.loaded() ? &model->vad : nullptr, so,
                                               &model->corrector);
  return s;
}

int tiro_session_feed(tiro_session *s, const float *pcm, size_t samples) {
  if (!s || !s->session) return TIRO_ERROR;
  s->session->feed(pcm, samples);
  return TIRO_OK;
}

int tiro_session_poll(tiro_session *s, char **events_json) {
  if (!s || !s->session || !events_json) return TIRO_ERROR;
  *events_json = dup(to_json_events(s->session->take_events()).dump());
  return TIRO_OK;
}

int tiro_session_finish(tiro_session *s, char **result_json) {
  if (!s || !s->session || !result_json) return TIRO_ERROR;
  std::string text = s->session->finish();
  json out = {{"text", text},
              {"phrases", s->session->phrases()},
              {"speech_ms", s->session->speech_ms()},
              {"events", to_json_events(s->session->take_events())}};
  *result_json = dup(out.dump());
  return TIRO_OK;
}

void tiro_session_free(tiro_session *s) { delete s; }

int64_t tiro_process_peak_rss(void) { return tiro::process_peak_rss(); }
int64_t tiro_process_rss(void) { return tiro::process_rss(); }

void tiro_free(void *ptr) { std::free(ptr); }

}  // extern "C"
