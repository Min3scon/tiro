// Hotkey logic (port of tiro/hotkey.py's HotkeyMachine / ChordHotkey / HotkeyCapture). Pure C++, no OS calls:
// the Windows hook in win_hook.cc feeds it key events on its own thread, so a busy Python interpreter can never
// make Windows drop the hook. Keep the behaviour identical to the Python version (tests/test_hotkey.py and
// core/tests/test_hotkey.cc check the same cases).
#pragma once

#include <functional>
#include <map>
#include <string>
#include <vector>

namespace tiro {

constexpr double kTapMaxSec = 0.28;      // a press shorter than this counts as a tap
constexpr double kDoubleTapSec = 0.40;   // max gap between the two taps of a double-tap
constexpr double kChordWindowSec = 0.45; // another key this soon after the hotkey means "this was a shortcut"

constexpr int VK_ESCAPE_ = 0x1B;

// Every modifier virtual-key code (generic and left/right).
bool is_modifier(int vk);

// A hotkey: each element is a set of alternative virtual keys (e.g. "ctrl" = {LCtrl, RCtrl, Ctrl}).
using KeySets = std::vector<std::vector<int>>;

struct KeyEvent {
  int vk = 0, scan = 0, flags = 0;
  bool down = false;
};

class HotkeyMachine {
 public:
  enum State { IDLE, PRESSED, TAP_PENDING, LOCK_HELD, LOCKED, TOGGLE_HELD, TOGGLED, WAIT_RELEASE, CHORD };

  std::function<void(const std::string &)> on_action;            // "start" | "stop" | "cancel" | "lock"
  std::function<void(const std::vector<KeyEvent> &)> replay;     // hand swallowed keys back to the app
  std::function<double()> clock;                                 // seconds, monotonic
  std::function<void(double, std::function<void()>)> schedule;   // run fn after delay seconds (same thread)
  std::function<bool()> speech_started = [] { return true; };    // has this dictation heard speech yet?

  void configure(const KeySets &spec, const std::string &mode, bool double_tap_lock);
  bool on_key(int vk, bool down, int scan = 0, int flags = 0);  // true = swallow
  void force_idle();
  bool active() const { return state_ != IDLE && state_ != WAIT_RELEASE && state_ != CHORD; }
  State state() const { return state_; }
  const std::map<int, std::pair<int, int>> &down() const { return down_; }

 private:
  bool handle(int vk, bool down, bool repeat, int scan, int flags);
  bool complete() const;
  bool extra_keys_down() const;
  bool in_spec(int vk) const;
  void emit(const char *a) {
    if (on_action) on_action(a);
  }
  void tap_expired(int token);

  KeySets sets_;
  std::vector<int> spec_vks_;
  std::string mode_ = "hold";
  bool double_tap_lock_ = true;
  State state_ = IDLE;
  int trigger_ = -1;
  double t_press_ = 0;
  int tap_token_ = 0;
  std::map<int, std::pair<int, int>> down_;  // vk -> (scan, flags) of physically held keys
};

// A plain shortcut such as Ctrl + Alt + F: fires when its last key goes down while the rest are held (and no
// other modifier is). That key press and its release are swallowed; the modifiers pass through.
class ChordHotkey {
 public:
  std::function<void()> on_fire;
  void configure(const KeySets &spec);
  bool on_key(int vk, bool down);

 private:
  std::vector<std::vector<int>> mods_, keys_;
  std::vector<int> down_, swallowed_;
};

}  // namespace tiro
