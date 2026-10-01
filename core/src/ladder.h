// The model ladder and the resource budget: pick the most accurate model that fits what the device has FREE
// right now, without slowing anything else down.
//
// The policy (identical on every platform; the apps only report the numbers):
//   * RAM: at most a share of the free memory (Light 15%, Balanced 25%, Max accuracy 40%), never more than the
//     operating system's per-app limit (phones), and a model's measured peak must fit with 20% to spare.
//   * CPU: about half the physical cores while transcribing (Light 1-2, Max all but one); the predicted delay
//     after you stop speaking must stay under the mode's target, measured on this device by the setup benchmark.
//   * Step down on memory pressure, a busy CPU, battery saver, low battery or heat; step back up only after
//     resources have been free for a while (hysteresis), and only between dictations.
#pragma once

#include <string>
#include <vector>

namespace tiro {

struct Rung {
  std::string id;          // "moonshine-tiny-q4"
  std::string dir;         // model folder (relative to the models root)
  double wer = 0;          // measured mean WER on the dev sets (lower = better)
  double peak_mb = 0;      // measured peak RAM of the engine with this model (incl. buffers)
  double size_mb = 0;      // download size
  double cost = 0;         // measured CPU-seconds per second of speech on the reference core (1 thread)
  bool bundled = false;    // ships with the installer (always available)
  bool installed = true;   // files present on this device
};

struct Resources {
  double total_ram_mb = 0;
  double free_ram_mb = 0;       // available without paging (Windows "available", macOS free+inactive, Linux MemAvailable)
  double app_limit_mb = 0;      // per-app limit if the OS has one (iOS/Android), else 0
  double cpu_busy = 0;          // 0..1, other programs' CPU use, recent average
  int physical_cores = 1;
  int logical_cores = 1;
  double core_speed = 1.0;      // this device's core speed relative to the reference core (setup benchmark)
  bool on_battery = false;
  double battery = 1.0;         // 0..1
  bool power_saver = false;
  int thermal = 0;              // 0 nominal, 1 fair, 2 serious, 3 critical
};

enum class ResourceMode { Light, Balanced, MaxAccuracy, Auto };

struct Choice {
  int rung = -1;       // index into the ladder, -1 = nothing fits (use the smallest bundled one)
  int threads = 1;
  double budget_mb = 0;
  double predicted_ms = 0;  // delay after a 5 s phrase
  std::string why;          // plain-English reason, shown in Settings
};

class Ladder {
 public:
  explicit Ladder(std::vector<Rung> rungs);
  const std::vector<Rung> &rungs() const { return rungs_; }

  // Decide from scratch (start-up, or after the user changes the mode).
  Choice choose(const Resources &r, ResourceMode mode) const;
  // Re-evaluate while running: steps down at once under pressure, steps up only after `calm_seconds` of the
  // better choice being stable. Call between dictations, every few seconds.
  Choice reconsider(const Resources &r, ResourceMode mode, const Choice &current, double now_seconds);

  static ResourceMode parse_mode(const std::string &s);

 private:
  std::vector<Rung> rungs_;
  int pending_up_ = -1;
  double pending_since_ = 0;
};

}  // namespace tiro
