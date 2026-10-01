#include "hotkey.h"

#include <algorithm>

namespace tiro {

namespace {
const int kModifiers[] = {0xA2, 0xA3, 0x11, 0xA0, 0xA1, 0x10, 0xA4, 0xA5, 0x12, 0x5B, 0x5C};

bool contains(const std::vector<int> &v, int x) { return std::find(v.begin(), v.end(), x) != v.end(); }
}  // namespace

bool is_modifier(int vk) { return std::find(std::begin(kModifiers), std::end(kModifiers), vk) != std::end(kModifiers); }

// ------------------------------------------------------------------------------------------------ HotkeyMachine
void HotkeyMachine::configure(const KeySets &spec, const std::string &mode, bool double_tap_lock) {
  sets_ = spec;
  spec_vks_.clear();
  for (const auto &s : sets_)
    for (int vk : s)
      if (!contains(spec_vks_, vk)) spec_vks_.push_back(vk);
  mode_ = mode;
  double_tap_lock_ = double_tap_lock;
  state_ = IDLE;
  trigger_ = -1;
  t_press_ = 0;
  tap_token_ = 0;
}

bool HotkeyMachine::in_spec(int vk) const { return contains(spec_vks_, vk); }

bool HotkeyMachine::complete() const {
  for (const auto &s : sets_) {
    bool any = false;
    for (int vk : s) any = any || down_.count(vk);
    if (!any) return false;
  }
  return true;
}

bool HotkeyMachine::extra_keys_down() const {
  for (const auto &[vk, _] : down_)
    if (!in_spec(vk)) return true;
  return false;
}

bool HotkeyMachine::on_key(int vk, bool down, int scan, int flags) {
  bool repeat = down && down_.count(vk);
  if (down)
    down_[vk] = {scan, flags};
  else
    down_.erase(vk);
  return handle(vk, down, repeat, scan, flags);
}

bool HotkeyMachine::handle(int vk, bool down, bool repeat, int scan, int flags) {
  double now = clock ? clock() : 0.0;
  State st = state_;
  bool is_trigger = vk == trigger_;

  if (st == CHORD) {
    bool any = false;
    for (int v : spec_vks_) any = any || down_.count(v);
    if (!any) {
      state_ = IDLE;
      trigger_ = -1;
    }
    return false;
  }
  if (st == WAIT_RELEASE) {
    if (is_trigger) {
      if (!down) {
        state_ = IDLE;
        trigger_ = -1;
      }
      return true;
    }
    return vk == VK_ESCAPE_;
  }
  if (st == IDLE) {
    if (down && !repeat && in_spec(vk) && complete() && !extra_keys_down()) {
      trigger_ = vk;
      t_press_ = now;
      state_ = mode_ == "hold" ? PRESSED : TOGGLE_HELD;
      emit("start");
      return true;
    }
    return false;
  }
  // From here on a dictation is running (or a tap is pending).
  if (down && vk == VK_ESCAPE_ && !is_trigger) {
    emit("cancel");
    state_ = down_.count(trigger_) ? WAIT_RELEASE : IDLE;
    if (state_ == IDLE) trigger_ = -1;
    return true;
  }
  if (st == PRESSED || st == TOGGLE_HELD) {
    if (is_trigger) {
      if (down) return true;  // auto-repeat
      double held = now - t_press_;
      if (st == TOGGLE_HELD) {
        state_ = TOGGLED;
      } else if (double_tap_lock_ && held < kTapMaxSec) {
        state_ = TAP_PENDING;
        int token = ++tap_token_;
        if (schedule) schedule(kDoubleTapSec, [this, token] { tap_expired(token); });
      } else {
        state_ = IDLE;
        trigger_ = -1;
        emit("stop");
      }
      return true;
    }
    if (!down && in_spec(vk) && !complete()) {
      // a modifier of a multi-key hotkey was released: finish, but swallow the trigger's release
      emit(mode_ == "hold" ? "stop" : "cancel");
      state_ = WAIT_RELEASE;
      return false;
    }
    bool chord = now - t_press_ < kChordWindowSec || !(speech_started && speech_started());
    if (down && !repeat && !in_spec(vk) && chord) {
      // It was a shortcut such as Right Ctrl + C (or Shift + letter when Shift is the hotkey): undo the
      // dictation and hand the keys to the app. Once the user is talking, keys pass through.
      emit("cancel");
      state_ = CHORD;
      auto it = down_.find(trigger_);
      KeyEvent trig{trigger_, it != down_.end() ? it->second.first : 0, it != down_.end() ? it->second.second : 0, true};
      if (replay) replay({trig, KeyEvent{vk, scan, flags, true}});
      return true;
    }
    return false;
  }
  if (st == TAP_PENDING) {
    if (is_trigger && down && !repeat) {
      state_ = LOCK_HELD;
      ++tap_token_;
      emit("lock");
      return true;
    }
    if (down && !repeat) {
      ++tap_token_;
      state_ = IDLE;
      trigger_ = -1;
      emit("cancel");
    }
    return false;
  }
  if (st == LOCK_HELD) {
    if (is_trigger) {
      if (!down) state_ = LOCKED;
      return true;
    }
    return false;
  }
  if (st == LOCKED || st == TOGGLED) {
    if (is_trigger || (in_spec(vk) && sets_.size() == 1)) {
      if (down && !repeat) {
        trigger_ = vk;
        state_ = WAIT_RELEASE;
        emit("stop");
      }
      return true;
    }
    if (down && !repeat && in_spec(vk) && complete()) {
      trigger_ = vk;
      state_ = WAIT_RELEASE;
      emit("stop");
      return true;
    }
    return false;
  }
  return false;
}

void HotkeyMachine::tap_expired(int token) {
  if (state_ == TAP_PENDING && token == tap_token_) {
    state_ = IDLE;
    trigger_ = -1;
    emit("cancel");
  }
}

void HotkeyMachine::force_idle() {
  if (state_ == PRESSED || state_ == TOGGLE_HELD || state_ == LOCK_HELD) {
    state_ = WAIT_RELEASE;
  } else if (state_ != WAIT_RELEASE) {
    state_ = IDLE;
    trigger_ = -1;
  }
}

// -------------------------------------------------------------------------------------------------- ChordHotkey
void ChordHotkey::configure(const KeySets &spec) {
  mods_.clear();
  keys_.clear();
  for (const auto &s : spec) {
    bool all_mods = !s.empty() && std::all_of(s.begin(), s.end(), is_modifier);
    (all_mods ? mods_ : keys_).push_back(s);
  }
}

bool ChordHotkey::on_key(int vk, bool down) {
  if (!down) {
    down_.erase(std::remove(down_.begin(), down_.end(), vk), down_.end());
    auto it = std::find(swallowed_.begin(), swallowed_.end(), vk);
    if (it != swallowed_.end()) {
      swallowed_.erase(it);
      return true;
    }
    return false;
  }
  bool repeat = contains(down_, vk);
  if (!repeat) down_.push_back(vk);
  if (keys_.empty() || !contains(keys_.back(), vk)) return false;
  if (repeat) return contains(swallowed_, vk);
  auto held = [&](const std::vector<int> &set) {
    return std::any_of(set.begin(), set.end(), [&](int v) { return contains(down_, v); });
  };
  for (const auto &m : mods_)
    if (!held(m)) return false;
  for (int v : down_) {
    if (!is_modifier(v)) continue;
    bool allowed = std::any_of(mods_.begin(), mods_.end(), [&](const std::vector<int> &m) { return contains(m, v); });
    if (!allowed) return false;
  }
  for (size_t i = 0; i + 1 < keys_.size(); ++i)
    if (!held(keys_[i])) return false;
  swallowed_.push_back(vk);
  if (on_fire) on_fire();
  return true;
}

}  // namespace tiro
