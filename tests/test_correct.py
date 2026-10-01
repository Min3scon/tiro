"""The correction pass without audio (acoustic penalty estimated from spelling): tiers, rules, safety."""

import pytest

from tiro.asr import Word
from tiro.correct import knowledge
from tiro.correct.corrector import Change, Corrector, Knowledge, apply_changes, validate
from tiro.correct.history import Correction, Profile
from tiro.correct.lexicon import Lexicon

DICTIONARY = ["GeoGuessr", "Kubernetes", "Zettelkasten", "Claude", "Jira", "Tiro", "super bass -> Supabase", "GitHub"]
STARTER = ["Obsidian", "Figma", "Spotify", "Basecamp", "Yeti", "Nando's", "Siobhan", "New York", "PyTorch"]


class _FlatAudio:
    """Stands in for the acoustic scorer: every candidate fits the audio 1 nat worse than what was heard."""

    def deltas(self, words, candidates):
        return [1.0] * len(candidates)


def starter() -> Lexicon:
    lex = Lexicon()
    for t in STARTER:
        lex.add(t, "starter")
    return lex.freeze()


def make(dictionary=(), history=(), corrections=(), mode="balanced", with_starter=True) -> Corrector:
    profile = Profile.build([(0.0, "", "", t) for t in history]) if history else None
    k = knowledge.build(list(dictionary), list(corrections), profile, starter() if with_starter else None)
    return Corrector(k, mode=mode)


def words(text: str, conf: dict[str, float] | None = None) -> list[Word]:
    conf = conf or {}
    out = []
    for i, w in enumerate(text.split()):
        out.append(Word(w, i * 0.5, i * 0.5 + 0.4, conf.get(w, 0.99)))
    return out


def fix(c: Corrector, text: str, conf: dict[str, float] | None = None, **kw) -> str:
    return " ".join(w.text for w in c.correct(words(text, conf), **kw).words)


C = make(DICTIONARY)


# ---------------------------------------------------------------------- what it should fix
def test_near_misses_become_your_dictionary_word():
    assert fix(C, "I love playing Geo Guesser with friends.") == "I love playing GeoGuessr with friends."
    assert fix(C, "Have you tried GeoGuesser?", {"GeoGuesser?": 0.6}) == "Have you tried GeoGuessr?"
    assert fix(C, "We run it on Cuba Ernets now.", {"Ernets": 0.7}) == "We run it on Kubernetes now."
    assert fix(C, "check the Zettel Casten notes", {"Zettel": 0.5, "Casten": 0.3}) == "check the Zettelkasten notes"
    assert fix(C, "the Tyro app", {"Tyro": 0.6}) == "the Tiro app"


def test_rules_casing_and_joined_words():
    assert fix(C, "We use Tailwind and Super Bass.") == "We use Tailwind and Supabase."
    assert fix(C, "open the jira board") == "open the Jira board"
    assert fix(C, "push it to github today") == "push it to GitHub today"
    assert fix(C, "push it to Git Hub today") == "push it to GitHub today"


def test_possessives_and_punctuation_survive():
    assert fix(C, "the GeoGuesser's leaderboard,", {"GeoGuesser's": 0.5}) == "the GeoGuessr's leaderboard,"
    assert fix(C, '"Geo Guesser," she said.') == '"GeoGuessr," she said.'


def test_timestamps_are_merged():
    ws = [Word("Geo", 1.0, 1.2), Word("Guesser.", 1.25, 1.6), Word("Yes", 2.0, 2.2)]
    out = C.correct(ws).words
    assert [w.text for w in out] == ["GeoGuessr.", "Yes"]
    assert out[0].start == 1.0 and out[0].end == 1.6


# ---------------------------------------------------------------------- what it must leave alone
def test_neighbouring_words_are_not_swallowed():
    assert fix(C, "service on Kubernetes last night") == "service on Kubernetes last night"
    assert fix(C, "instead of the Zettelkasten") == "instead of the Zettelkasten"


def test_ordinary_words_are_left_alone():
    assert fix(C, "my files are in the cloud") == "my files are in the cloud"
    assert fix(C, "the jury was out") == "the jury was out"
    assert fix(C, "a flat tire again") == "a flat tire again"
    assert fix(C, "we set up base camp by the lake") == "we set up base camp by the lake"
    assert fix(C, "I ate an apple") == "I ate an apple"


@pytest.mark.parametrize("text", [
    "ngl that was lowkey fire",
    "yeet it into the bin lol",
    "we gonna grab some food or nah",
    "Me and him was going down the shops innit",
    "The meeting is at 3 pm on the 5th floor.",
    "I reckon the vibes are off, bruv.",
    "Can you send me the spreadsheet by Friday?",
])
def test_do_no_harm(text):
    # even where the recogniser was unsure of every word, nothing changes without real evidence
    unsure = {w: 0.5 for w in text.split()}
    assert fix(C, text) == text
    assert fix(C, text, unsure) == text


# ---------------------------------------------------------------------- history decides
def test_history_turns_an_ambiguous_span_into_your_word():
    history = [
        "Just played a round of GeoGuessr and got the map wrong.",
        "GeoGuessr duels are so much fun, I guessed the country from the road signs.",
        "Want to play GeoGuessr tonight? I'll share the map link.",
        "My GeoGuessr rating went up after that round.",
    ] * 3
    with_history = make(history=history)
    without = make()
    text = "I played George asser last night and nailed the map."
    unsure = {"George": 0.7, "asser": 0.4}
    assert fix(with_history, text, unsure) == "I played GeoGuessr last night and nailed the map."
    assert fix(without, text, unsure) == text


def test_taught_fix_is_a_plain_lookup():
    taught = [Correction("George asser", "GeoGuessr", 1, "I played George asser", 0.0),
              Correction("cloud", "Claude", 1, "ask cloud about the refactor", 0.0)]
    c = make(corrections=taught)
    assert fix(c, "We played George asser again") == "We played GeoGuessr again"
    # a taught fix of one ordinary word only applies where it was taught to (or when the recogniser doubts it)
    assert fix(c, "my photos are in the cloud") == "my photos are in the cloud"
    assert fix(c, "ask cloud about the refactor") == "ask Claude about the refactor"
    assert fix(c, "send it to cloud", {"cloud": 0.4}) == "send it to Claude"


def test_modes_trade_reach_for_caution():
    history = ["Tiro is my dictation app.", "I fixed a bug in Tiro today."]  # weak evidence: seen twice
    text = "the Tyro app"
    for mode, expect in (("aggressive", "the Tiro app"), ("balanced", "the Tiro app"), ("strict", text)):
        assert fix(make(history=history, mode=mode), text, {"Tyro": 0.85}) == expect, mode


def test_tier0_skips_confident_ordinary_text_quickly():
    res = C.correct(words("this is a perfectly normal sentence about the weekend"))
    assert res.tier == 0 and res.flagged == 0 and not res.changes


# ---------------------------------------------------------------------- the validator
def test_validator_enforces_the_rules():
    k = C.knowledge
    ws = words("I love playing Geo Guesser with friends.")
    good = Change(3, 5, "Geo Guesser", "GeoGuessr", "dictionary")
    assert validate(ws, [good], k) == [good]
    bad = [
        Change(3, 5, "Geo Guesser", "GeoGuessr game", "dictionary"),  # adds a word
        Change(0, 5, "I love playing Geo Guesser", "GeoGuessr", "dictionary"),  # span too long
        Change(3, 5, "Geo Guesser", "Kubernetes", "dictionary"),  # doesn't sound alike
        Change(3, 5, "Geo Guesser", "Geography", "dictionary"),  # not something you use
        Change(5, 7, "with friends", "GeoGuessr", "dictionary"),  # neither
        Change(3, 5, "Geo Guessers", "GeoGuessr", "dictionary"),  # doesn't match what was recognised
        Change(3, 5, "Geo Guesser", "GeoGuessr.", "dictionary"),  # punctuation is not the corrector's
        Change(3, 5, "Geo Guesser", "Supabase", "rule"),  # no such rule
    ]
    for ch in bad:
        assert validate(ws, [ch], k) == [], ch
    # punctuation inside a span means it isn't one name
    ws2 = words("I said Geo. Guesser is fun")
    assert validate(ws2, [Change(2, 4, "Geo Guesser", "GeoGuessr", "dictionary")], k) == []
    # overlapping changes: only the first survives
    two = [Change(3, 5, "Geo Guesser", "GeoGuessr", "dictionary"), Change(4, 5, "Guesser", "GeoGuessr", "dictionary")]
    assert len(validate(ws, two, k)) == 1


def test_apply_changes_touches_nothing_else():
    ws = words("I love playing Geo Guesser with friends.")
    out = apply_changes(ws, [Change(3, 5, "Geo Guesser", "GeoGuessr", "dictionary")])
    assert [w.text for w in out] == ["I", "love", "playing", "GeoGuessr", "with", "friends."]
    assert out[0] is ws[0] and out[-1] is ws[-1]


def test_empty_knowledge_changes_nothing():
    c = Corrector(Knowledge())
    text = "I love playing Geo Guesser with friends."
    assert fix(c, text, {w: 0.3 for w in text.split()}) == text


# ---------------------------------------------------------------------- your spelling, your names
RUST_DEV = ["I'm rewriting the parser in Rust this week.", "The Rust compiler caught it.",
            "Async Rust is hard.", "I love writing Rust at work."]
RUST_CAR = ["There's rust on the wheel arch again.", "The rust spread to the door.",
            "I sanded the rust off the bike.", "Spray it so the rust doesn't come back."]


def test_casing_follows_how_you_write_the_word():
    text = "I've been fighting with rust all weekend"
    assert fix(make(history=RUST_DEV), text) == "I've been fighting with Rust all weekend"
    assert fix(make(history=RUST_CAR), text) == text
    shouted = "I've been fighting with Rust all weekend"
    assert fix(make(history=RUST_CAR), shouted) == text
    assert fix(make(history=RUST_DEV), shouted) == shouted
    # never at the start of a sentence, and never without history
    assert fix(make(history=RUST_CAR), "Rust is everywhere.") == "Rust is everywhere."
    assert fix(make(), text) == text


def test_sound_alike_names_follow_your_history():
    shaun = ["Shaun is coming over later.", "I told Shaun about the gig.", "Shaun and I went climbing.",
             "Ask Shaun if he wants pizza."]
    sean = ["Sean is coming over later.", "I told Sean about the gig.", "Sean and I went climbing.",
            "Ask Sean if he wants pizza."]
    text = "Shawn said he'd bring the speakers round."

    def make_heard(**kw):  # pretend the audio fits every candidate equally well: history must decide
        c = make(**kw)
        c.scorer = _FlatAudio()
        return c

    assert fix(make_heard(history=shaun), text) == "Shaun said he'd bring the speakers round."
    assert fix(make_heard(history=sean), text) == "Sean said he'd bring the speakers round."
    assert fix(make_heard(), text) == text
    assert fix(make_heard(history=shaun[:1]), text) == text  # one mention isn't enough


class _FakeLM:
    """Stands in for the language model: a fixed preference for every option."""

    def __init__(self, gain):
        self.gain, self.avg_ms, self.calls = gain, 1.0, 0

    def cached(self, *a, **k):
        return True

    def compare(self, left, original, options, right, timeout=0.15, examples=None):
        self.calls += 1
        return [self.gain] * len(options)


def test_ordinary_words_that_spell_a_brand_need_the_language_model():
    c = make(with_starter=False)
    lex = Lexicon()
    for t in ("Among Us", "Dropbox"):
        lex.add(t, "starter")
    c.knowledge.starter = lex.freeze()
    unsure = {"among": 0.5, "us": 0.5, "drop": 0.5, "box": 0.5}
    for text in ("Whomst among us has not eaten cereal at midnight?", "Leave the keys in the drop box by the desk."):
        assert fix(c, text, unsure) == text  # no language model: leave it
        c.language = _FakeLM(0.3)
        assert fix(c, text, unsure) == text  # it doesn't clearly prefer the brand: leave it
        c.language = None
    c.language = _FakeLM(3.0)
    assert fix(c, "we played among us all night", unsure) == "we played Among Us all night"
