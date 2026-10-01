// Process-wide ONNX Runtime policy: how many threads a model may use and how sessions behave.
#pragma once

#include <cstdint>

namespace tiro {

struct RuntimePolicy {
  int intra_threads = 0;   // 0 = let ONNX Runtime decide (one per physical core)
  bool allow_spinning = false;  // false: threads sleep between runs (near-zero idle CPU)
  bool prepack = true;  // prepacked weights: much faster int8 MatMul for a second copy of the weights
};

// Applies to every session created after the call (the vendored runtime creates its own sessions).
void set_runtime_policy(const RuntimePolicy &policy);
RuntimePolicy runtime_policy();

int64_t process_rss();
int64_t process_peak_rss();

}  // namespace tiro
