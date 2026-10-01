#include "ladder.h"

#include <algorithm>
#include <cmath>
#include <sstream>

namespace tiro {
namespace {

struct ModeRules {
  double ram_share;     // of free RAM
  double target_ms;     // delay after a 5 s phrase
  double cpu_share;     // of physical cores
};

ModeRules rules_for(ResourceMode m, const Resources &r) {
  switch (m) {
    case ResourceMode::Light:
      return {0.15, 900, 0.25};
    case ResourceMode::MaxAccuracy:
      return {0.40, 1500, 0.75};
    case ResourceMode::Balanced:
    case ResourceMode::Auto:
    default: {
      ModeRules b{0.25, 600, 0.5};
      if (m == ResourceMode::Auto) {
        // Auto = Balanced, made lighter whenever the device is under pressure
        if (r.on_battery) b = {0.20, 700, 0.35};
        if (r.power_saver || (r.on_battery && r.battery < 0.2) || r.thermal >= 2) b = {0.15, 900, 0.25};
        if (r.cpu_busy > 0.7) b.cpu_share = std::min(b.cpu_share, 0.25);
      }
      return b;
    }
  }
}

std::string fmt_mb(double mb) {
  std::ostringstream s;
  if (mb >= 1024) {
    s.precision(1);
    s << std::fixed << mb / 1024 << " GB";
  } else {
    s << static_cast<int>(mb) << " MB";
  }
  return s.str();
}

}  // namespace

Ladder::Ladder(std::vector<Rung> rungs) : rungs_(std::move(rungs)) {
  // most accurate first
  std::stable_sort(rungs_.begin(), rungs_.end(), [](const Rung &a, const Rung &b) { return a.wer < b.wer; });
}

ResourceMode Ladder::parse_mode(const std::string &s) {
  if (s == "light") return ResourceMode::Light;
  if (s == "max" || s == "max_accuracy") return ResourceMode::MaxAccuracy;
  if (s == "auto") return ResourceMode::Auto;
  return ResourceMode::Balanced;
}

Choice Ladder::choose(const Resources &r, ResourceMode mode) const {
  ModeRules m = rules_for(mode, r);
  Choice c;
  double budget = r.free_ram_mb * m.ram_share;
  if (r.app_limit_mb > 0) budget = std::min(budget, r.app_limit_mb * 0.6);
  c.budget_mb = budget;
  int cores = std::max(1, r.physical_cores);
  int threads = std::max(1, static_cast<int>(std::floor(cores * m.cpu_share + 0.5)));
  if (mode == ResourceMode::MaxAccuracy) threads = std::max(1, std::min(threads, cores - 1));
  if (r.cpu_busy > 0.85) threads = 1;  // something else needs the CPU: stay out of its way
  c.threads = threads;
  // effective throughput: threads help the encoder, not the per-token decoder; count them at 60%
  double speed = std::max(0.05, r.core_speed) * (1.0 + 0.6 * (threads - 1)) * std::max(0.15, 1.0 - r.cpu_busy);
  for (size_t i = 0; i < rungs_.size(); ++i) {
    const Rung &g = rungs_[i];
    if (!g.installed) continue;
    double ms = 5000.0 * g.cost / speed * 0.35;  // ~35% of the phrase is still to compute when speech stops
    bool ram_ok = g.peak_mb * 1.2 <= budget;
    bool fast_ok = ms <= m.target_ms;
    if (ram_ok && fast_ok) {
      c.rung = static_cast<int>(i);
      c.predicted_ms = ms;
      std::ostringstream why;
      why << g.id << ": needs about " << fmt_mb(g.peak_mb) << " of the " << fmt_mb(budget)
          << " Tiro may use (" << static_cast<int>(m.ram_share * 100) << "% of the " << fmt_mb(r.free_ram_mb)
          << " free), and should finish about " << static_cast<int>(ms) << " ms after you stop talking on "
          << threads << (threads == 1 ? " core" : " cores") << ".";
      c.why = why.str();
      return c;
    }
  }
  // nothing fits comfortably: the smallest bundled model is always available
  int smallest = -1;
  for (size_t i = 0; i < rungs_.size(); ++i) {
    if (!rungs_[i].installed) continue;
    if (smallest < 0 || rungs_[i].peak_mb < rungs_[smallest].peak_mb) smallest = static_cast<int>(i);
  }
  c.rung = smallest;
  c.threads = 1;
  if (smallest >= 0) {
    c.predicted_ms = 5000.0 * rungs_[smallest].cost / std::max(0.05, r.core_speed) * 0.35;
    c.why = rungs_[smallest].id + ": the smallest model, because this device is short on free memory or CPU "
            "right now (" + fmt_mb(r.free_ram_mb) + " free).";
  } else {
    c.why = "no speech model is installed";
  }
  return c;
}

Choice Ladder::reconsider(const Resources &r, ResourceMode mode, const Choice &current, double now) {
  Choice best = choose(r, mode);
  if (current.rung < 0 || best.rung < 0) return best;
  const Rung &cur = rungs_[current.rung];
  bool pressure = cur.peak_mb * 1.05 > best.budget_mb || r.thermal >= 3 || r.power_saver;
  if (best.rung == current.rung) {
    pending_up_ = -1;
    Choice keep = best;  // same model; thread count may change
    return keep;
  }
  bool better = rungs_[best.rung].wer < cur.wer;
  if (!better) {  // stepping down: immediately (pressure) or once the worse choice persists briefly
    pending_up_ = -1;
    if (pressure || r.cpu_busy > 0.85) return best;
    return best;
  }
  // stepping up: only after resources have been calm for a while
  const double calm_seconds = 60;
  if (pending_up_ != best.rung) {
    pending_up_ = best.rung;
    pending_since_ = now;
  }
  if (now - pending_since_ >= calm_seconds) {
    pending_up_ = -1;
    return best;
  }
  Choice keep = current;
  keep.why = cur.id + " (a more accurate model fits now; switching if it stays that way for a minute)";
  return keep;
}

}  // namespace tiro
