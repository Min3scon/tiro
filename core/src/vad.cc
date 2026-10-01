#include "vad.h"

#include <algorithm>
#include <cstring>
#include <filesystem>

#include "onnxruntime_c_api.h"

namespace tiro {

Vad::~Vad() {
  if (!api_) return;
  if (session_) api_->ReleaseSession(session_);
  if (mem_) api_->ReleaseMemoryInfo(mem_);
  if (env_) api_->ReleaseEnv(env_);
}

bool Vad::load(const std::string &path, std::string *error) {
  api_ = OrtGetApiBase()->GetApi(ORT_API_VERSION);
  auto fail = [&](OrtStatus *st) {
    if (error) *error = std::string("VAD: ") + api_->GetErrorMessage(st);
    api_->ReleaseStatus(st);
    return false;
  };
  OrtStatus *st = api_->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "tiro-vad", &env_);
  if (st) return fail(st);
  OrtSessionOptions *so = nullptr;
  if ((st = api_->CreateSessionOptions(&so))) return fail(st);
  api_->SetIntraOpNumThreads(so, 1);  // a tiny model: one thread, no pool to wake up
  api_->SetInterOpNumThreads(so, 1);
  api_->SetSessionExecutionMode(so, ORT_SEQUENTIAL);
  api_->AddSessionConfigEntry(so, "session.intra_op.allow_spinning", "0");
  api_->SetSessionGraphOptimizationLevel(so, ORT_ENABLE_ALL);
#ifdef _WIN32
  std::wstring wpath = std::filesystem::path(path).wstring();
  st = api_->CreateSession(env_, wpath.c_str(), so, &session_);
#else
  st = api_->CreateSession(env_, path.c_str(), so, &session_);
#endif
  api_->ReleaseSessionOptions(so);
  if (st) return fail(st);
  if ((st = api_->CreateCpuMemoryInfo(OrtArenaAllocator, OrtMemTypeDefault, &mem_))) return fail(st);
  reset();
  return true;
}

void Vad::reset() {
  state_.fill(0.0f);
  input_.fill(0.0f);
}

float Vad::prob(const float *window) {
  if (!session_) return 1.0f;  // no VAD: treat everything as speech
  std::memcpy(input_.data() + kContext, window, kWindow * sizeof(float));
  int64_t in_shape[2] = {1, kContext + kWindow};
  int64_t st_shape[3] = {2, 1, 128};
  int64_t sr = 16000;
  OrtValue *in = nullptr, *state = nullptr, *rate = nullptr;
  api_->CreateTensorWithDataAsOrtValue(mem_, input_.data(), input_.size() * sizeof(float), in_shape, 2,
                                       ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &in);
  api_->CreateTensorWithDataAsOrtValue(mem_, state_.data(), state_.size() * sizeof(float), st_shape, 3,
                                       ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &state);
  api_->CreateTensorWithDataAsOrtValue(mem_, &sr, sizeof(sr), nullptr, 0, ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64, &rate);
  const char *in_names[] = {"input", "state", "sr"};
  const char *out_names[] = {"output", "stateN"};
  const OrtValue *inputs[] = {in, state, rate};
  OrtValue *outputs[2] = {nullptr, nullptr};
  float p = 0.0f;
  OrtStatus *st = api_->Run(session_, nullptr, in_names, inputs, 3, out_names, 2, outputs);
  if (!st) {
    float *out = nullptr, *new_state = nullptr;
    api_->GetTensorMutableData(outputs[0], reinterpret_cast<void **>(&out));
    api_->GetTensorMutableData(outputs[1], reinterpret_cast<void **>(&new_state));
    p = out[0];
    std::memcpy(state_.data(), new_state, state_.size() * sizeof(float));
  } else {
    api_->ReleaseStatus(st);
  }
  for (OrtValue *v : outputs)
    if (v) api_->ReleaseValue(v);
  api_->ReleaseValue(in);
  api_->ReleaseValue(state);
  api_->ReleaseValue(rate);
  // context for the next window = last 64 samples of this one
  std::memmove(input_.data(), input_.data() + kWindow, kContext * sizeof(float));
  return p;
}

}  // namespace tiro
