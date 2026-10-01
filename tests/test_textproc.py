from tiro.textproc import FormatOptions, TextAssembler, hold_back


def assemble(chunks, **kw):
    opts = FormatOptions(**{k: kw.pop(k) for k in list(kw) if k in ("remove_fillers", "voice_commands")})
    asm = TextAssembler(opts, **kw)
    typed = [asm.add(c.split()) for c in chunks]
    return asm.text, typed


def test_commits_are_joined_with_single_spaces():
    text, typed = assemble(["Hello there.", "How are", "you?"])
    assert text == "Hello there. How are you?"
    assert typed == ["Hello there.", " How are", " you?"]


def test_leading_space_when_continuing_after_a_word():
    text, _ = assemble(["We should go."], needs_space=True, mid_sentence=True)
    assert text == " we should go."


def test_names_and_I_keep_capitals_when_continuing():
    assert assemble(["I agree."], needs_space=True, mid_sentence=True)[0] == " I agree."
    assert assemble(["Microsoft said so."], needs_space=True, mid_sentence=True)[0] == " Microsoft said so."


def test_new_sentence_after_period_keeps_capital():
    assert assemble(["The end."], needs_space=True, mid_sentence=False)[0] == " The end."


def test_sentence_start_is_capitalised():
    assert assemble(["mister Quilter is here."])[0] == "Mr. Quilter is here."
    assert assemble(["done.", "and then more."])[0] == "Done. And then more."
    assert assemble(["iPhone sales rose."])[0] == "iPhone sales rose."


def test_filler_words_are_removed():
    assert assemble(["Um, so we went there."])[0] == "So we went there."
    assert assemble(["I think, uh, we should go."])[0] == "I think, we should go."
    assert assemble(["We left uh."])[0] == "We left."
    assert assemble(["Um, so we went."], remove_fillers=False)[0] == "Um, so we went."


def test_voice_commands_insert_line_breaks():
    assert assemble(["Hello. New line. How are you?"])[0] == "Hello.\nHow are you?"
    assert assemble(["Dear John, new paragraph. Thanks for the note."])[0] == "Dear John,\n\nThanks for the note."
    assert assemble(["Hi, new line, how are you?"])[0] == "Hi,\nHow are you?"


def test_voice_command_needs_a_phrase_boundary():
    assert assemble(["We need a new line of products."])[0] == "We need a new line of products."
    assert assemble(["Hello. New line."], voice_commands=False)[0] == "Hello. New line."


def test_voice_command_split_across_commits_is_held_back():
    opts = FormatOptions()
    assert hold_back(["Hello.", "New"], opts) == 1
    assert hold_back(["Hello.", "there"], opts) == 0
    assert hold_back(["Hello.", "New"], FormatOptions(voice_commands=False)) == 0


def test_no_space_around_line_breaks_across_commits():
    text, typed = assemble(["First line. New line.", "Second line."])
    assert text == "First line.\nSecond line."
    assert typed[1] == "Second line."
