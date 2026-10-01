// Windows low-level keyboard hook on a native thread (see include/tiro_hook.h).
#include <windows.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "hotkey.h"
#include "nlohmann/json.hpp"
#include "tiro_hook.h"

using json = nlohmann::json;

namespace {

constexpr UINT WM_REINSTALL = WM_APP + 1;
constexpr UINT WM_REPLAY = WM_APP + 2;
constexpr UINT WM_CONFIGURE = WM_APP + 3;
constexpr UINT WM_FORCE_IDLE = WM_APP + 4;
constexpr UINT kReinstallMs = 60000;

const int kExtended[] = {0xA3, 0xA5, 0x5B, 0x5C, 0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x6F, 0x90};

struct Config {
  tiro::KeySets hotkey, fix;
  std::string mode = "hold";
  bool double_tap_lock = true;
};

bool parse_config(const char *text, Config &c, ULONG_PTR *mark, std::string &err) {
  try {
    json j = json::parse(text ? text : "{}");
    for (const auto &set : j.value("hotkey", json::array())) c.hotkey.push_back(set.get<std::vector<int>>());
    if (j.contains("fix") && j["fix"].is_array())
      for (const auto &set : j["fix"]) c.fix.push_back(set.get<std::vector<int>>());
    c.mode = j.value("mode", std::string("hold"));
    c.double_tap_lock = j.value("double_tap_lock", true);
    if (mark && j.contains("mark")) *mark = j["mark"].get<unsigned long long>();
    if (c.hotkey.empty()) {
      err = "no hotkey given";
      return false;
    }
    return true;
  } catch (const std::exception &e) {
    err = e.what();
    return false;
  }
}

char *dup(const std::string &s) {
  char *p = static_cast<char *>(std::malloc(s.size() + 1));
  std::memcpy(p, s.c_str(), s.size() + 1);
  return p;
}

double now_sec() {
  using namespace std::chrono;
  return duration<double>(steady_clock::now().time_since_epoch()).count();
}

}  // namespace

struct tiro_hook {
  std::thread thread;
  DWORD tid = 0;
  HHOOK hhook = nullptr;
  HMODULE module = nullptr;
  ULONG_PTR mark = 0;
  tiro::HotkeyMachine machine;
  tiro::ChordHotkey chord;
  std::atomic<bool> speech{false}, capture{false};
  std::atomic<int> state{0};
  std::map<UINT_PTR, std::function<void()>> timers;
  UINT_PTR reinstall_timer = 0;
  std::mutex mu;
  std::condition_variable cv;
  std::vector<std::string> events;
  std::mutex start_mu;
  std::condition_variable start_cv;
  bool started = false;
  std::string start_error;

  void push(json ev) {
    {
      std::lock_guard<std::mutex> lock(mu);
      events.push_back(ev.dump());
    }
    cv.notify_one();
  }

  int mods() const {
    int m = 0;
    for (const auto &[vk, _] : machine.down()) {
      if (vk == 0x11 || vk == 0xA2 || vk == 0xA3) m |= 1;
      if (vk == 0x10 || vk == 0xA0 || vk == 0xA1) m |= 2;
      if (vk == 0x12 || vk == 0xA4 || vk == 0xA5) m |= 4;
      if (vk == 0x5B || vk == 0x5C) m |= 8;
    }
    return m;
  }

  void apply(const Config &c) {
    machine.configure(c.hotkey, c.mode, c.double_tap_lock);
    chord.configure(c.fix);
    state = machine.state();
  }

  bool install() {
    hhook = SetWindowsHookExW(WH_KEYBOARD_LL, &tiro_hook::proc, module, 0);
    return hhook != nullptr;
  }

  void reinstall() {
    if (hhook) UnhookWindowsHookEx(hhook);
    hhook = nullptr;
    if (install()) push(json{{"reinstalled", true}});
  }

  static LRESULT CALLBACK proc(int code, WPARAM wp, LPARAM lp);
  LRESULT on_key(const KBDLLHOOKSTRUCT &kb, bool down);
  void run(Config cfg);
  void send_replay(const std::vector<tiro::KeyEvent> &keys);
};

static std::atomic<tiro_hook *> g_hook{nullptr};

LRESULT CALLBACK tiro_hook::proc(int code, WPARAM wp, LPARAM lp) {
  tiro_hook *h = g_hook.load();
  if (code == HC_ACTION && h) {
    const auto *kb = reinterpret_cast<const KBDLLHOOKSTRUCT *>(lp);
    bool down = wp == WM_KEYDOWN || wp == WM_SYSKEYDOWN;
    if (kb->dwExtraInfo != h->mark && h->on_key(*kb, down)) return 1;
  }
  return CallNextHookEx(nullptr, code, wp, lp);
}

LRESULT tiro_hook::on_key(const KBDLLHOOKSTRUCT &kb, bool down) {
  int vk = static_cast<int>(kb.vkCode), scan = static_cast<int>(kb.scanCode), flags = static_cast<int>(kb.flags);
  if (capture) {
    push(json{{"capture", {vk, down ? 1 : 0}}});
    return 1;
  }
  bool swallow = chord.on_key(vk, down);
  if (!swallow) swallow = machine.on_key(vk, down, scan, flags);
  state = machine.state();
  if (down && !swallow) push(json{{"key", {vk, scan, mods()}}});
  return swallow ? 1 : 0;
}

void tiro_hook::send_replay(const std::vector<tiro::KeyEvent> &keys) {
  std::vector<INPUT> in;
  for (const auto &k : keys) {
    INPUT i{};
    i.type = INPUT_KEYBOARD;
    i.ki.wVk = static_cast<WORD>(k.vk);
    i.ki.wScan = static_cast<WORD>(k.scan ? k.scan : MapVirtualKeyW(k.vk, 0));
    bool ext = (k.flags & 1) != 0;
    for (int e : kExtended) ext = ext || e == k.vk;
    i.ki.dwFlags = (k.down ? 0 : KEYEVENTF_KEYUP) | (ext ? KEYEVENTF_EXTENDEDKEY : 0);
    i.ki.dwExtraInfo = mark;
    in.push_back(i);
  }
  if (!in.empty()) SendInput(static_cast<UINT>(in.size()), in.data(), sizeof(INPUT));
  int m = mods();
  for (const auto &k : keys)
    if (k.down && !tiro::is_modifier(k.vk)) push(json{{"key", {k.vk, k.scan, m}}});
}

void tiro_hook::run(Config cfg) {
  tid = GetCurrentThreadId();
  MSG msg;
  PeekMessageW(&msg, nullptr, WM_USER, WM_USER, PM_NOREMOVE);  // create this thread's message queue
  machine.clock = now_sec;
  machine.speech_started = [this] { return speech.load(); };
  machine.on_action = [this](const std::string &a) {
    if (a == "start") speech = false;  // a new dictation hasn't heard anything yet
    push(json{{"action", a}});
  };
  machine.schedule = [this](double delay, std::function<void()> fn) {
    UINT_PTR id = SetTimer(nullptr, 0, static_cast<UINT>(delay * 1000 + 0.5), nullptr);
    if (id) timers[id] = std::move(fn);
  };
  machine.replay = [this](const std::vector<tiro::KeyEvent> &keys) {
    PostThreadMessageW(tid, WM_REPLAY, 0, reinterpret_cast<LPARAM>(new std::vector<tiro::KeyEvent>(keys)));
  };
  chord.on_fire = [this] { push(json{{"fire", "fix"}}); };
  apply(cfg);
  GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                     reinterpret_cast<LPCWSTR>(&tiro_hook::proc), &module);
  bool ok = install();
  {
    std::lock_guard<std::mutex> lock(start_mu);
    started = true;
    if (!ok) start_error = "SetWindowsHookEx failed (error " + std::to_string(GetLastError()) + ")";
  }
  start_cv.notify_all();
  if (!ok) return;
  reinstall_timer = SetTimer(nullptr, 0, kReinstallMs, nullptr);
  while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
    switch (msg.message) {
      case WM_TIMER: {
        if (msg.wParam == reinstall_timer) {
          // Windows silently drops a low-level hook that was ever slow to answer; registering again is cheap
          if (!machine.active() && machine.down().empty()) reinstall();
          break;
        }
        auto it = timers.find(msg.wParam);
        if (it != timers.end()) {
          KillTimer(nullptr, msg.wParam);
          auto fn = std::move(it->second);
          timers.erase(it);
          fn();
          state = machine.state();
        }
        break;
      }
      case WM_REINSTALL:
        reinstall();
        break;
      case WM_REPLAY: {
        auto *keys = reinterpret_cast<std::vector<tiro::KeyEvent> *>(msg.lParam);
        send_replay(*keys);
        delete keys;
        break;
      }
      case WM_CONFIGURE: {
        auto *c = reinterpret_cast<Config *>(msg.lParam);
        apply(*c);
        delete c;
        break;
      }
      case WM_FORCE_IDLE:
        machine.force_idle();
        state = machine.state();
        break;
      default:
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
  }
  for (auto &[id, _] : timers) KillTimer(nullptr, id);
  if (reinstall_timer) KillTimer(nullptr, reinstall_timer);
  if (hhook) UnhookWindowsHookEx(hhook);
  hhook = nullptr;
}

extern "C" {

TIRO_HOOK_API tiro_hook *tiro_hook_start(const char *config_json, char **error) {
  Config cfg;
  ULONG_PTR mark = 0;
  std::string err;
  if (!parse_config(config_json, cfg, &mark, err)) {
    if (error) *error = dup(err);
    return nullptr;
  }
  tiro_hook *expected = nullptr;
  auto *h = new tiro_hook();
  h->mark = mark;
  if (!g_hook.compare_exchange_strong(expected, h)) {
    delete h;
    if (error) *error = dup("a hook is already running in this process");
    return nullptr;
  }
  h->thread = std::thread([h, cfg] { h->run(cfg); });
  std::unique_lock<std::mutex> lock(h->start_mu);
  h->start_cv.wait_for(lock, std::chrono::seconds(5), [h] { return h->started; });
  if (!h->started || !h->start_error.empty()) {
    std::string msg = h->started ? h->start_error : "the hook thread did not start";
    lock.unlock();
    if (h->tid) PostThreadMessageW(h->tid, WM_QUIT, 0, 0);
    if (h->thread.joinable()) h->thread.join();
    g_hook = nullptr;
    delete h;
    if (error) *error = dup(msg);
    return nullptr;
  }
  return h;
}

TIRO_HOOK_API int tiro_hook_configure(tiro_hook *h, const char *config_json) {
  if (!h) return -1;
  auto *c = new Config();
  std::string err;
  if (!parse_config(config_json, *c, nullptr, err)) {
    delete c;
    return -1;
  }
  if (!PostThreadMessageW(h->tid, WM_CONFIGURE, 0, reinterpret_cast<LPARAM>(c))) {
    delete c;
    return -1;
  }
  return 0;
}

TIRO_HOOK_API void tiro_hook_set_speech(tiro_hook *h, int heard) {
  if (h) h->speech = heard != 0;
}

TIRO_HOOK_API void tiro_hook_set_capture(tiro_hook *h, int on) {
  if (h) h->capture = on != 0;
}

TIRO_HOOK_API void tiro_hook_force_idle(tiro_hook *h) {
  if (h) PostThreadMessageW(h->tid, WM_FORCE_IDLE, 0, 0);
}

TIRO_HOOK_API void tiro_hook_reinstall(tiro_hook *h) {
  if (h) PostThreadMessageW(h->tid, WM_REINSTALL, 0, 0);
}

TIRO_HOOK_API int tiro_hook_state(tiro_hook *h) { return h ? h->state.load() : 0; }

TIRO_HOOK_API int tiro_hook_wait(tiro_hook *h, int timeout_ms, char **events_json) {
  if (!h || !events_json) return 0;
  std::vector<std::string> got;
  {
    std::unique_lock<std::mutex> lock(h->mu);
    h->cv.wait_for(lock, std::chrono::milliseconds(timeout_ms), [h] { return !h->events.empty(); });
    got.swap(h->events);
  }
  if (got.empty()) {
    *events_json = nullptr;
    return 0;
  }
  std::string out = "[";
  for (size_t i = 0; i < got.size(); ++i) out += (i ? "," : "") + got[i];
  out += "]";
  *events_json = dup(out);
  return 1;
}

TIRO_HOOK_API void tiro_hook_stop(tiro_hook *h) {
  if (!h) return;
  PostThreadMessageW(h->tid, WM_QUIT, 0, 0);
  if (h->thread.joinable()) h->thread.join();
  tiro_hook *expected = h;
  g_hook.compare_exchange_strong(expected, nullptr);
  h->cv.notify_all();
  delete h;
}

TIRO_HOOK_API void tiro_hook_free(void *p) { std::free(p); }

}  // extern "C"
