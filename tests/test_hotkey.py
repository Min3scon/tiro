import pytest

from tiro.hotkey import CHORD_WINDOW_SEC, DOUBLE_TAP_SEC, HotkeyCapture, HotkeyMachine
from tiro.keys import VK_ESCAPE, VK_LCONTROL, VK_LSHIFT, VK_RCONTROL, VK_SPACE, HotkeySpec

VK_C = 0x43


class Clock:
    def __init__(self):
        self.t = 100.0
        self.timers = []

    def __call__(self):
        return self.t

    def schedule(self, delay, fn):
        self.timers.append((self.t + delay, fn))

    def advance(self, dt):
        self.t += dt
        due = [fn for at, fn in self.timers if at <= self.t]
        self.timers = [(at, fn) for at, fn in self.timers if at > self.t]
        for fn in due:
            fn()


@pytest.fixture
def rig():
    def make(keys="rctrl", mode="hold", double_tap=True):
        clock = Clock()
        actions, replays = [], []
        m = HotkeyMachine(HotkeySpec.parse(keys), actions.append, replays.append, mode=mode,
                          double_tap_lock=double_tap, clock=clock, schedule=clock.schedule)
        return m, clock, actions, replays

    return make


def press(m, vk):
    return m.on_key(vk, True)


def release(m, vk):
    return m.on_key(vk, False)


def test_hold_to_talk(rig):
    m, clock, actions, _ = rig()
    assert press(m, VK_RCONTROL) is True  # swallowed
    clock.advance(0.1)
    assert press(m, VK_RCONTROL) is True  # auto-repeat swallowed
    clock.advance(1.5)
    assert release(m, VK_RCONTROL) is True
    assert actions == ["start", "stop"]
    assert not m.active


def test_other_keys_unaffected(rig):
    m, *_ = rig()
    assert press(m, VK_C) is False
    assert release(m, VK_C) is False
    assert press(m, VK_LCONTROL) is False  # left ctrl is not the hotkey
    assert release(m, VK_LCONTROL) is False


def test_chord_is_replayed_to_the_app(rig):
    m, clock, actions, replays = rig()
    press(m, VK_RCONTROL)
    clock.advance(0.08)
    assert press(m, VK_C) is True  # swallowed, then replayed together with Right Ctrl
    assert actions == ["start", "cancel"]
    assert [(vk, down) for vk, _s, _f, down in replays[0]] == [(VK_RCONTROL, True), (VK_C, True)]
    assert release(m, VK_C) is False
    assert release(m, VK_RCONTROL) is False  # the app saw our replayed press, so it gets the release
    assert m.state == m.IDLE


def test_late_keypress_while_dictating_passes_through(rig):
    m, clock, actions, replays = rig()
    press(m, VK_RCONTROL)
    clock.advance(CHORD_WINDOW_SEC + 0.2)
    assert press(m, VK_C) is False
    assert release(m, VK_C) is False
    assert actions == ["start"] and not replays


def test_late_keypress_before_any_speech_is_still_a_chord():
    # e.g. Shift as the hotkey: holding Shift a while before typing a capital must still give a capital
    clock = Clock()
    actions, replays = [], []
    m = HotkeyMachine(HotkeySpec.parse("lshift"), actions.append, replays.append, clock=clock,
                      schedule=clock.schedule, speech_started=lambda: False)
    press(m, VK_LSHIFT)
    clock.advance(CHORD_WINDOW_SEC + 0.8)
    assert press(m, VK_C) is True
    assert actions == ["start", "cancel"]
    assert [(vk, down) for vk, _s, _f, down in replays[0]] == [(VK_LSHIFT, True), (VK_C, True)]


def test_double_tap_locks_hands_free(rig):
    m, clock, actions, _ = rig()
    press(m, VK_RCONTROL)
    clock.advance(0.1)
    release(m, VK_RCONTROL)
    clock.advance(0.15)
    press(m, VK_RCONTROL)
    clock.advance(0.1)
    release(m, VK_RCONTROL)
    assert actions == ["start", "lock"]
    clock.advance(5.0)  # keeps listening
    assert actions == ["start", "lock"]
    assert press(m, VK_RCONTROL) is True
    assert release(m, VK_RCONTROL) is True
    assert actions == ["start", "lock", "stop"]
    assert m.state == m.IDLE


def test_single_short_tap_cancels(rig):
    m, clock, actions, _ = rig()
    press(m, VK_RCONTROL)
    clock.advance(0.1)
    release(m, VK_RCONTROL)
    clock.advance(DOUBLE_TAP_SEC + 0.05)
    assert actions == ["start", "cancel"]
    assert m.state == m.IDLE


def test_short_tap_without_double_tap_lock_stops(rig):
    m, clock, actions, _ = rig(double_tap=False)
    press(m, VK_RCONTROL)
    clock.advance(0.1)
    release(m, VK_RCONTROL)
    assert actions == ["start", "stop"]


def test_escape_cancels(rig):
    m, clock, actions, _ = rig()
    press(m, VK_RCONTROL)
    clock.advance(1.0)
    assert press(m, VK_ESCAPE) is True
    assert release(m, VK_ESCAPE) is True
    assert release(m, VK_RCONTROL) is True
    assert actions == ["start", "cancel"]
    assert m.state == m.IDLE


def test_toggle_mode(rig):
    m, clock, actions, _ = rig(mode="toggle")
    press(m, VK_RCONTROL)
    clock.advance(0.1)
    release(m, VK_RCONTROL)
    clock.advance(3.0)
    assert actions == ["start"]
    press(m, VK_RCONTROL)
    release(m, VK_RCONTROL)
    assert actions == ["start", "stop"]


def test_combo_hotkey_hold(rig):
    m, clock, actions, _ = rig("ctrl+shift+space")
    assert press(m, VK_LCONTROL) is False
    assert press(m, VK_LSHIFT) is False
    assert press(m, VK_SPACE) is True
    clock.advance(1.0)
    assert release(m, VK_SPACE) is True
    assert actions == ["start", "stop"]
    release(m, VK_LSHIFT)
    release(m, VK_LCONTROL)
    assert m.state == m.IDLE


def test_combo_needs_all_keys(rig):
    m, _clock, actions, _ = rig("ctrl+shift+space")
    press(m, VK_LCONTROL)
    assert press(m, VK_SPACE) is False
    assert actions == []


def test_combo_modifier_release_finishes(rig):
    m, clock, actions, _ = rig("ctrl+shift+space")
    press(m, VK_LCONTROL)
    press(m, VK_LSHIFT)
    press(m, VK_SPACE)
    clock.advance(1.0)
    release(m, VK_LSHIFT)
    assert actions == ["start", "stop"]
    assert release(m, VK_SPACE) is True  # trigger release still swallowed
    assert m.state == m.IDLE


def test_capture_single_and_combo():
    got = []
    cap = HotkeyCapture(got.append)
    cap(VK_RCONTROL, True)
    cap(VK_RCONTROL, False)
    assert str(got[0]) == "rctrl"

    got.clear()
    cap = HotkeyCapture(got.append)
    for vk in (VK_LCONTROL, VK_LSHIFT, VK_SPACE):
        cap(vk, True)
    for vk in (VK_SPACE, VK_LSHIFT, VK_LCONTROL):
        cap(vk, False)
    assert str(got[0]) == "ctrl+shift+space"

    got.clear()
    cap = HotkeyCapture(got.append)
    cap(VK_ESCAPE, True)
    assert got == [None]


def test_chord_hotkey_fires_and_swallows_only_its_key():
    from tiro.hotkey import ChordHotkey

    fired = []
    ch = ChordHotkey(HotkeySpec.parse("ctrl+alt+f"), lambda: fired.append(1))
    F, LCTRL, LALT, LSHIFT = 0x46, 0xA2, 0xA4, 0xA0
    assert ch.on_key(LCTRL, True) is False and ch.on_key(LALT, True) is False
    assert ch.on_key(F, True) is True and fired == [1]
    assert ch.on_key(F, True) is True and fired == [1]  # auto-repeat: swallowed, fires once
    assert ch.on_key(F, False) is True
    assert ch.on_key(LALT, False) is False and ch.on_key(LCTRL, False) is False
    # F alone, or with an extra modifier, is left alone
    assert ch.on_key(F, True) is False and ch.on_key(F, False) is False
    for vk in (LCTRL, LALT, LSHIFT):
        ch.on_key(vk, True)
    assert ch.on_key(F, True) is False and fired == [1]
