from tiro.asr import tokens_to_words
from tiro.context import END, MID, WORD, classify_key
from tiro.keys import HotkeySpec, hotkey_problem


def test_hotkey_specs_parse_and_label():
    assert HotkeySpec.parse("rctrl").label == "Right Ctrl"
    assert HotkeySpec.parse("ctrl+shift+space").label == "Ctrl + Shift + Space"
    assert HotkeySpec.parse(["ctrl", "win"]).label == "Ctrl + Win"
    assert HotkeySpec.from_captured([0xA3]).keys == ("rctrl",)
    assert HotkeySpec.from_captured([0xA2, 0x5B]).keys == ("ctrl", "win")


def test_unsafe_hotkeys_are_rejected():
    assert hotkey_problem(HotkeySpec.parse("rctrl")) is None
    assert hotkey_problem(HotkeySpec.parse("f8")) is None
    assert hotkey_problem(HotkeySpec.parse("ctrl+space")) is None
    assert hotkey_problem(HotkeySpec.parse("a")) is not None
    assert hotkey_problem(HotkeySpec.parse("space")) is not None
    assert hotkey_problem(HotkeySpec.parse("a+b")) is not None


def test_tokens_group_into_words_with_times():
    words = tokens_to_words([" Hel", "lo", ",", " world", "."], [0.0, 0.08, 0.16, 0.4, 0.64])
    assert [w.text for w in words] == ["Hello,", "world."]
    assert words[0].start == 0.0 and abs(words[0].end - 0.24) < 1e-9
    assert abs(words[1].end - 0.72) < 1e-9


def test_key_classification():
    assert classify_key(0x41, False) == WORD
    assert classify_key(0xBE, False) == END
    assert classify_key(0xBF, True) == END  # ?
    assert classify_key(0xBC, False) == MID
    assert classify_key(0xA0, False) is None  # shift alone
