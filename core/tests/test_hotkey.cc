// The native hotkey machine must behave exactly like tiro/hotkey.py (same cases as tests/test_hotkey.py).
#include <string>
#include <vector>

#include "doctest/doctest.h"
#include "hotkey.h"

using tiro::HotkeyMachine;

namespace {
constexpr int RCTRL = 0xA3, LCTRL = 0xA2, LSHIFT = 0xA0, SPACE = 0x20, ESC = 0x1B, C = 0x43;
const std::vector<int> CTRL = {0xA2, 0xA3, 0x11}, SHIFT = {0xA0, 0xA1, 0x10}, ALT = {0xA4, 0xA5, 0x12};

struct Rig {
  HotkeyMachine m;
  double t = 100.0;
  std::vector<std::pair<double, std::function<void()>>> timers;
  std::vector<std::string> actions;
  std::vector<std::vector<tiro::KeyEvent>> replays;
  bool speech = true;

  explicit Rig(tiro::KeySets keys, const std::string &mode = "hold", bool double_tap = true) {
    m.clock = [this] { return t; };
    m.schedule = [this](double d, std::function<void()> fn) { timers.push_back({t + d, std::move(fn)}); };
    m.on_action = [this](const std::string &a) { actions.push_back(a); };
    m.replay = [this](const std::vector<tiro::KeyEvent> &k) { replays.push_back(k); };
    m.speech_started = [this] { return speech; };
    m.configure(keys, mode, double_tap);
  }
  void advance(double dt) {
    t += dt;
    std::vector<std::function<void()>> due;
    std::vector<std::pair<double, std::function<void()>>> keep;
    for (auto &x : timers) {
      if (x.first <= t)
        due.push_back(x.second);
      else
        keep.push_back(x);
    }
    timers = keep;
    for (auto &fn : due) fn();
  }
  bool press(int vk) { return m.on_key(vk, true); }
  bool release(int vk) { return m.on_key(vk, false); }
  std::vector<std::string> acts(std::initializer_list<const char *> a) { return {a.begin(), a.end()}; }
};
}  // namespace

TEST_CASE("hold to talk") {
  Rig r({{RCTRL}});
  CHECK(r.press(RCTRL));
  r.advance(0.1);
  CHECK(r.press(RCTRL));  // auto-repeat swallowed
  r.advance(1.5);
  CHECK(r.release(RCTRL));
  CHECK(r.actions == r.acts({"start", "stop"}));
  CHECK_FALSE(r.m.active());
}

TEST_CASE("other keys unaffected") {
  Rig r({{RCTRL}});
  CHECK_FALSE(r.press(C));
  CHECK_FALSE(r.release(C));
  CHECK_FALSE(r.press(LCTRL));
  CHECK_FALSE(r.release(LCTRL));
}

TEST_CASE("chord is replayed to the app") {
  Rig r({{RCTRL}});
  r.press(RCTRL);
  r.advance(0.08);
  CHECK(r.press(C));
  CHECK(r.actions == r.acts({"start", "cancel"}));
  REQUIRE(r.replays.size() == 1);
  REQUIRE(r.replays[0].size() == 2);
  CHECK(r.replays[0][0].vk == RCTRL);
  CHECK(r.replays[0][0].down);
  CHECK(r.replays[0][1].vk == C);
  CHECK_FALSE(r.release(C));
  CHECK_FALSE(r.release(RCTRL));
  CHECK(r.m.state() == HotkeyMachine::IDLE);
}

TEST_CASE("late keypress while dictating passes through") {
  Rig r({{RCTRL}});
  r.press(RCTRL);
  r.advance(tiro::kChordWindowSec + 0.2);
  CHECK_FALSE(r.press(C));
  CHECK_FALSE(r.release(C));
  CHECK(r.actions == r.acts({"start"}));
  CHECK(r.replays.empty());
}

TEST_CASE("late keypress before any speech is still a chord") {
  Rig r({{LSHIFT}});
  r.speech = false;
  r.press(LSHIFT);
  r.advance(tiro::kChordWindowSec + 0.8);
  CHECK(r.press(C));
  CHECK(r.actions == r.acts({"start", "cancel"}));
  REQUIRE(r.replays.size() == 1);
  CHECK(r.replays[0][0].vk == LSHIFT);
  CHECK(r.replays[0][1].vk == C);
}

TEST_CASE("double tap locks hands free") {
  Rig r({{RCTRL}});
  r.press(RCTRL);
  r.advance(0.1);
  r.release(RCTRL);
  r.advance(0.15);
  r.press(RCTRL);
  r.advance(0.1);
  r.release(RCTRL);
  CHECK(r.actions == r.acts({"start", "lock"}));
  r.advance(5.0);
  CHECK(r.actions == r.acts({"start", "lock"}));
  CHECK(r.press(RCTRL));
  CHECK(r.release(RCTRL));
  CHECK(r.actions == r.acts({"start", "lock", "stop"}));
  CHECK(r.m.state() == HotkeyMachine::IDLE);
}

TEST_CASE("single short tap cancels") {
  Rig r({{RCTRL}});
  r.press(RCTRL);
  r.advance(0.1);
  r.release(RCTRL);
  r.advance(tiro::kDoubleTapSec + 0.05);
  CHECK(r.actions == r.acts({"start", "cancel"}));
  CHECK(r.m.state() == HotkeyMachine::IDLE);
}

TEST_CASE("short tap without double tap lock stops") {
  Rig r({{RCTRL}}, "hold", false);
  r.press(RCTRL);
  r.advance(0.1);
  r.release(RCTRL);
  CHECK(r.actions == r.acts({"start", "stop"}));
}

TEST_CASE("escape cancels") {
  Rig r({{RCTRL}});
  r.press(RCTRL);
  r.advance(1.0);
  CHECK(r.press(ESC));
  CHECK(r.release(ESC));
  CHECK(r.release(RCTRL));
  CHECK(r.actions == r.acts({"start", "cancel"}));
  CHECK(r.m.state() == HotkeyMachine::IDLE);
}

TEST_CASE("toggle mode") {
  Rig r({{RCTRL}}, "toggle");
  r.press(RCTRL);
  r.advance(0.1);
  r.release(RCTRL);
  r.advance(3.0);
  CHECK(r.actions == r.acts({"start"}));
  r.press(RCTRL);
  r.release(RCTRL);
  CHECK(r.actions == r.acts({"start", "stop"}));
}

TEST_CASE("combo hotkey hold") {
  Rig r({CTRL, SHIFT, {SPACE}});
  CHECK_FALSE(r.press(LCTRL));
  CHECK_FALSE(r.press(LSHIFT));
  CHECK(r.press(SPACE));
  r.advance(1.0);
  CHECK(r.release(SPACE));
  CHECK(r.actions == r.acts({"start", "stop"}));
  r.release(LSHIFT);
  r.release(LCTRL);
  CHECK(r.m.state() == HotkeyMachine::IDLE);
}

TEST_CASE("combo needs all keys") {
  Rig r({CTRL, SHIFT, {SPACE}});
  r.press(LCTRL);
  CHECK_FALSE(r.press(SPACE));
  CHECK(r.actions.empty());
}

TEST_CASE("combo modifier release finishes") {
  Rig r({CTRL, SHIFT, {SPACE}});
  r.press(LCTRL);
  r.press(LSHIFT);
  r.press(SPACE);
  r.advance(1.0);
  r.release(LSHIFT);
  CHECK(r.actions == r.acts({"start", "stop"}));
  CHECK(r.release(SPACE));  // trigger release still swallowed
  CHECK(r.m.state() == HotkeyMachine::IDLE);
}

TEST_CASE("force idle waits for the hotkey's release") {
  Rig r({{RCTRL}});
  r.press(RCTRL);
  r.m.force_idle();
  CHECK(r.m.state() == HotkeyMachine::WAIT_RELEASE);
  CHECK(r.release(RCTRL));
  CHECK(r.m.state() == HotkeyMachine::IDLE);
}

TEST_CASE("chord hotkey fires and swallows only its key") {
  tiro::ChordHotkey ch;
  int fired = 0;
  ch.on_fire = [&] { ++fired; };
  constexpr int F = 0x46, LALT = 0xA4;
  ch.configure({CTRL, ALT, {F}});
  CHECK_FALSE(ch.on_key(LCTRL, true));
  CHECK_FALSE(ch.on_key(LALT, true));
  CHECK(ch.on_key(F, true));
  CHECK(fired == 1);
  CHECK(ch.on_key(F, true));  // auto-repeat: swallowed, fires once
  CHECK(fired == 1);
  CHECK(ch.on_key(F, false));
  CHECK_FALSE(ch.on_key(LALT, false));
  CHECK_FALSE(ch.on_key(LCTRL, false));
  CHECK_FALSE(ch.on_key(F, true));  // F alone is left alone
  CHECK_FALSE(ch.on_key(F, false));
  for (int vk : {LCTRL, LALT, LSHIFT}) ch.on_key(vk, true);
  CHECK_FALSE(ch.on_key(F, true));  // with an extra modifier too
  CHECK(fired == 1);
}
