#include "doctest/doctest.h"
#include "ladder.h"

using tiro::Ladder;
using tiro::Resources;
using tiro::ResourceMode;
using tiro::Rung;

namespace {
// illustrative numbers in the shape of the measured table (the shipped table lives in rungs.json)
std::vector<Rung> ladder() {
  return {
      {"tiny", "tiny", 12.0, 110, 45, 0.010, true, true},
      {"small", "small", 7.8, 240, 142, 0.035, false, true},
      {"medium", "medium", 6.6, 420, 270, 0.070, false, true},
      {"large", "large", 6.0, 900, 650, 0.120, false, true},
  };
}
Resources desktop(double free_gb, int cores = 6) {
  Resources r;
  r.total_ram_mb = 16384;
  r.free_ram_mb = free_gb * 1024;
  r.physical_cores = cores;
  r.logical_cores = cores * 2;
  return r;
}
}  // namespace

TEST_CASE("a 2 GB phone with little free memory gets the smallest rung") {
  Ladder l(ladder());
  Resources r = desktop(0.7, 4);
  r.app_limit_mb = 400;
  r.core_speed = 0.35;
  auto c = l.choose(r, ResourceMode::Balanced);
  REQUIRE(c.rung >= 0);
  CHECK(l.rungs()[c.rung].id == "tiny");
}

TEST_CASE("more free memory means a more accurate rung") {
  Ladder l(ladder());
  CHECK(l.rungs()[l.choose(desktop(1.2), ResourceMode::Balanced).rung].id == "small");
  CHECK(l.rungs()[l.choose(desktop(2.5), ResourceMode::Balanced).rung].id == "medium");
  CHECK(l.rungs()[l.choose(desktop(6.0), ResourceMode::Balanced).rung].id == "large");
}

TEST_CASE("Light mode uses less, Max accuracy uses more") {
  Ladder l(ladder());
  CHECK(l.rungs()[l.choose(desktop(2.5), ResourceMode::Light).rung].id == "small");
  CHECK(l.rungs()[l.choose(desktop(3.0), ResourceMode::MaxAccuracy).rung].id == "large");  // 40% of 3 GB
  CHECK(l.rungs()[l.choose(desktop(3.0), ResourceMode::Balanced).rung].id == "medium");     // 25% of 3 GB
}

TEST_CASE("threads stay at about half the cores, one when the CPU is busy") {
  Ladder l(ladder());
  CHECK(l.choose(desktop(8, 8), ResourceMode::Balanced).threads == 4);
  Resources busy = desktop(8, 8);
  busy.cpu_busy = 0.9;
  CHECK(l.choose(busy, ResourceMode::Balanced).threads == 1);
}

TEST_CASE("Auto steps down on battery saver and heat") {
  Ladder l(ladder());
  Resources r = desktop(2.5);
  CHECK(l.rungs()[l.choose(r, ResourceMode::Auto).rung].id == "medium");
  r.power_saver = true;
  CHECK(l.rungs()[l.choose(r, ResourceMode::Auto).rung].id == "small");
}

TEST_CASE("stepping up waits for calm, stepping down doesn't") {
  Ladder l(ladder());
  auto cur = l.choose(desktop(1.2), ResourceMode::Balanced);  // small
  auto c = l.reconsider(desktop(6.0), ResourceMode::Balanced, cur, 0);
  CHECK(c.rung == cur.rung);  // not yet
  c = l.reconsider(desktop(6.0), ResourceMode::Balanced, cur, 61);
  CHECK(l.rungs()[c.rung].id == "large");
  auto down = l.reconsider(desktop(0.8), ResourceMode::Balanced, c, 62);
  CHECK(l.rungs()[down.rung].id != "large");
}

TEST_CASE("nothing installed fits: fall back to the smallest") {
  Ladder l(ladder());
  Resources r = desktop(0.1, 2);
  auto c = l.choose(r, ResourceMode::Balanced);
  REQUIRE(c.rung >= 0);
  CHECK(l.rungs()[c.rung].id == "tiny");
}
