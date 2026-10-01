from tiro.asr import segmentations
from tiro.correct.text import sound_key
from tiro.learn import learnable_correction
from tiro.vocab import Vocabulary

VOCAB = Vocabulary.from_lines(
    ["GeoGuessr", "Kubernetes", "Zettelkasten", "Claude", "Jira", "Tiro", "super bass -> Supabase", "# comment", ""]
)


def test_rule_entries_are_parsed():
    assert ("super bass", "Supabase") in VOCAB.rules
    assert "Supabase" in VOCAB.words and "# comment" not in VOCAB.words


def test_surface_forms_include_lowercase_variants_and_extras():
    v = Vocabulary.from_lines(["GeoGuessr", "geoguessr"], extra=["Obsidian", "GeoGuessr"])
    assert v.words == ["GeoGuessr"]  # duplicates (any case) removed
    assert v.extra == ["Obsidian"]
    assert v.surface_forms() == ["GeoGuessr", "geoGuessr", "Obsidian", "obsidian"]


def test_sound_key_groups_similar_spellings():
    assert sound_key("Kubernetes") == sound_key("cubaernets")
    assert sound_key("geoguessr") == sound_key("geoguesser")


def test_segmentations_spell_the_text():
    pieces = {" G": 1, " Ge": 2, "o": 3, "G": 4, "ue": 5, "ss": 6, "r": 7, "e": 8, "u": 9, "s": 10}
    paths = segmentations(" GeoGuessr", pieces)
    inv = {v: k for k, v in pieces.items()}
    assert paths and all("".join(inv[t] for t in p) == " GeoGuessr" for p in paths)
    assert len(paths[0]) == min(len(p) for p in paths)


def test_learning_a_retyped_word():
    assert learnable_correction("I love playing geogasser", "I love playing GeoGuessr") == "GeoGuessr"
    assert learnable_correction("ask George about it", "ask GeoGuessr about it") == "GeoGuessr"
    assert learnable_correction("grab the jury ticket", "grab the Jira ticket") == "Jira"
    assert learnable_correction("open the tiro app", "open the Tiro app") == "Tiro"  # capitalisation only
    assert learnable_correction("open jira now", "open Jira now") == "Jira"


def test_not_learning_rewording_or_typos():
    assert learnable_correction("a big house", "a large house") is None
    assert learnable_correction("see teh car", "see the car") is None
    assert learnable_correction("it was apple pie", "it was Apple pie") is None  # common word recased
    assert learnable_correction("hello there", "hello there") is None
