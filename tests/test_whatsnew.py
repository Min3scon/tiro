"""'What's new' after an update: this version's CHANGELOG section, and only that one."""
from tiro import __version__
from tiro.update import whatsnew


def test_this_version_has_notes():
    text = whatsnew.section(__version__)
    assert text
    assert "\n## " not in "\n" + text  # stops at the next version's heading


def test_a_longer_version_number_is_not_matched():
    assert whatsnew.section(__version__ + "0") == ""


def test_unknown_version_has_none():
    assert whatsnew.section("0.0.1") == ""
