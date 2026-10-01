#include "ort_env.h"

#include <mutex>
#include <string>

#include "onnxruntime_c_api.h"
#include "ort-utils.h"

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <psapi.h>
#elif defined(__APPLE__)
#include <mach/mach.h>
#include <sys/resource.h>
#elif defined(__linux__) || defined(__ANDROID__)
#include <cstdio>
#include <sys/resource.h>
#endif

namespace tiro {
namespace {

std::mutex g_mutex;
RuntimePolicy g_policy;

void apply_policy(const OrtApi *api, OrtSessionOptions *options) {
  RuntimePolicy p;
  {
    std::lock_guard<std::mutex> lock(g_mutex);
    p = g_policy;
  }
  if (p.intra_threads > 0) {
    api->SetIntraOpNumThreads(options, p.intra_threads);
  }
  api->SetInterOpNumThreads(options, 1);
  api->SetSessionExecutionMode(options, ORT_SEQUENTIAL);
  const char *spin = p.allow_spinning ? "1" : "0";
  api->AddSessionConfigEntry(options, "session.intra_op.allow_spinning", spin);
  api->AddSessionConfigEntry(options, "session.inter_op.allow_spinning", spin);
#ifdef _WIN32
  // Developer aid: TIRO_ORT_PROFILE=<prefix> writes ONNX Runtime per-operator timings for every session.
  wchar_t prefix[512];
  if (GetEnvironmentVariableW(L"TIRO_ORT_PROFILE", prefix, 512) > 0) api->EnableProfiling(options, prefix);
#endif
}

}  // namespace

void set_runtime_policy(const RuntimePolicy &policy) {
  std::lock_guard<std::mutex> lock(g_mutex);
  g_policy = policy;
  g_tiro_session_hook = &apply_policy;
  g_tiro_disable_prepacking = !policy.prepack;
}

RuntimePolicy runtime_policy() {
  std::lock_guard<std::mutex> lock(g_mutex);
  return g_policy;
}

int64_t process_rss() {
#if defined(_WIN32)
  PROCESS_MEMORY_COUNTERS pmc;
  if (GetProcessMemoryInfo(GetCurrentProcess(), &pmc, sizeof(pmc))) return static_cast<int64_t>(pmc.WorkingSetSize);
  return 0;
#elif defined(__APPLE__)
  mach_task_basic_info info;
  mach_msg_type_number_t count = MACH_TASK_BASIC_INFO_COUNT;
  if (task_info(mach_task_self(), MACH_TASK_BASIC_INFO, reinterpret_cast<task_info_t>(&info), &count) ==
      KERN_SUCCESS)
    return static_cast<int64_t>(info.resident_size);
  return 0;
#elif defined(__linux__) || defined(__ANDROID__)
  long pages = 0, resident = 0;
  FILE *f = std::fopen("/proc/self/statm", "r");
  if (!f) return 0;
  if (std::fscanf(f, "%ld %ld", &pages, &resident) != 2) resident = 0;
  std::fclose(f);
  return static_cast<int64_t>(resident) * 4096;
#else
  return 0;
#endif
}

int64_t process_peak_rss() {
#if defined(_WIN32)
  PROCESS_MEMORY_COUNTERS pmc;
  if (GetProcessMemoryInfo(GetCurrentProcess(), &pmc, sizeof(pmc)))
    return static_cast<int64_t>(pmc.PeakWorkingSetSize);
  return 0;
#elif defined(__APPLE__)
  rusage ru{};
  getrusage(RUSAGE_SELF, &ru);
  return static_cast<int64_t>(ru.ru_maxrss);  // bytes on macOS
#elif defined(__linux__) || defined(__ANDROID__)
  rusage ru{};
  getrusage(RUSAGE_SELF, &ru);
  return static_cast<int64_t>(ru.ru_maxrss) * 1024;  // kilobytes on Linux
#else
  return 0;
#endif
}

}  // namespace tiro
