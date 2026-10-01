"""Turn committed ASR words into the exact text to type: fillers, voice commands, spacing and casing."""

from __future__ import annotations

import re
from dataclasses import dataclass

FILLERS = frozenset({"um", "umm", "uhm", "uh", "uhh", "erm", "er"})
_SENTENCE_END = re.compile(r"[.?!][\"')\]]*$")
_TRAILING_TERMINAL = re.compile(r"[.?!]+$")
_ANY_TRAILING_PUNCT = re.compile(r"[.,;:?!]+$")
_NON_WORD = re.compile(r"[^\w']+")

# Words that are only capitalised because they start a sentence. Used when a dictation continues a sentence
# the user was already typing, so "I think" + "We should" becomes "I think we should".
COMMON_LOWER = frozenset(
    """a about after again all also although always an and another any anyway are as at be because been before
    being both but by can could did do does either even every for from had has have he her here his how however if
    in into is it its it's just let let's like maybe more most much my neither no nor not now of on once only or
    other our out over perhaps please probably really she should since so some still such than that that's the
    their them then there there's these they they're this those though through to too under unless until us very
    was we we're well were what when where whether which while who why will with would yet you you're your""".split()
)

NEWLINE = "\n"
PARAGRAPH = "\n\n"
_COMMANDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("new", "paragraph"), PARAGRAPH),
    (("new", "line"), NEWLINE),
    (("newline",), NEWLINE),
    (("next", "line"), NEWLINE),
)


def norm_word(word: str) -> str:
    return _NON_WORD.sub("", word.lower())


def ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.rstrip()))


@dataclass
class FormatOptions:
    remove_fillers: bool = True
    voice_commands: bool = True


def hold_back(words: list[str], opts: FormatOptions) -> int:
    """How many trailing words to keep uncommitted because they may begin a multi-word voice command."""
    if opts.voice_commands and words and norm_word(words[-1]) in ("new", "next"):
        return 1
    return 0


def _capitalize(word: str) -> str:
    for i, ch in enumerate(word):
        if ch.isalpha():
            return word[:i] + ch.upper() + word[i + 1 :]
    return word


def _decapitalize(word: str) -> str:
    """Lowercase a sentence-initial common word ("The" -> "the"); leaves "I", names and acronyms alone."""
    core = norm_word(word)
    if core not in COMMON_LOWER:
        return word
    letters = [c for c in word if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):  # acronym / shouting
        return word
    for i, ch in enumerate(word):
        if ch.isalpha():
            return word[:i] + ch.lower() + word[i + 1 :]
    return word


def remove_fillers(tokens: list[str], at_sentence_start: bool) -> list[str]:
    out: list[str] = []
    capitalize_next = False
    for tok in tokens:
        if norm_word(tok) in FILLERS:
            terminal = _TRAILING_TERMINAL.search(tok)
            if terminal and out and not _ANY_TRAILING_PUNCT.search(out[-1]):
                out[-1] += terminal.group()
            starts_sentence = (not out and at_sentence_start) or (bool(out) and ends_sentence(out[-1]))
            if starts_sentence or terminal:
                capitalize_next = True
            continue
        if capitalize_next:
            tok = _capitalize(tok)
            capitalize_next = False
        out.append(tok)
    return out


def apply_voice_commands(tokens: list[str], prev_token: str | None) -> list[str]:
    """Replace spoken "new line" / "new paragraph" (said as their own phrase) with line breaks."""
    out: list[str] = []
    i = 0
    capitalize_next = False
    while i < len(tokens):
        matched = None
        for phrase, replacement in _COMMANDS:
            n = len(phrase)
            if tuple(norm_word(t) for t in tokens[i : i + n]) != phrase:
                continue
            before = out[-1] if out else prev_token
            at_boundary = before is None or before in (NEWLINE, PARAGRAPH) or bool(_ANY_TRAILING_PUNCT.search(before))
            last = tokens[i + n - 1]
            closes = i + n == len(tokens) or bool(_ANY_TRAILING_PUNCT.search(last))
            # the phrase must not carry inner punctuation ("new, line")
            inner_clean = all(not _ANY_TRAILING_PUNCT.search(t) for t in tokens[i : i + n - 1])
            if at_boundary and closes and inner_clean:
                matched = (n, replacement)
                break
        if matched:
            n, replacement = matched
            out.append(replacement)
            i += n
            capitalize_next = True
            continue
        tok = tokens[i]
        if capitalize_next:
            tok = _capitalize(tok)
            capitalize_next = False
        out.append(tok)
        i += 1
    return out


_TITLES = {"mister": "Mr.", "missus": "Mrs."}


def normalize_titles(tokens: list[str]) -> list[str]:
    """ "mister Smith" -> "Mr. Smith" (the model writes the spoken form now and then)."""
    out = list(tokens)
    for i in range(len(out) - 1):
        title = _TITLES.get(out[i].lower())
        nxt = out[i + 1]
        if title and nxt[:1].isupper():
            out[i] = title
    return out


def _needs_capital(word: str) -> bool:
    """Starts lowercase and isn't deliberately mixed-case like 'iPhone' or 'eBay'."""
    letters = [c for c in word if c.isalpha()]
    return bool(letters) and letters[0].islower() and not any(c.isupper() for c in letters[1:])


class TextAssembler:
    """Accumulates committed words for one dictation session and returns the text to type for each commit."""

    def __init__(self, opts: FormatOptions, *, needs_space: bool = False, mid_sentence: bool = False):
        self.opts = opts
        self._needs_space = needs_space
        self._mid_sentence = mid_sentence
        self.text = ""
        self._last_token: str | None = None

    def _at_sentence_start(self) -> bool:
        if self._last_token is None:
            return not self._mid_sentence
        return self._last_token in (NEWLINE, PARAGRAPH) or ends_sentence(self._last_token)

    def add(self, words: list[str]) -> str:
        tokens = [w for w in words if w]
        if self.opts.remove_fillers:
            tokens = remove_fillers(tokens, self._at_sentence_start())
        if self.opts.voice_commands:
            tokens = apply_voice_commands(tokens, self._last_token)
        if not tokens:
            return ""
        tokens = normalize_titles(tokens)
        if self._last_token is None and self._mid_sentence and tokens[0] not in (NEWLINE, PARAGRAPH):
            tokens[0] = _decapitalize(tokens[0])

        pieces: list[str] = []
        prev = self._last_token
        for i, tok in enumerate(tokens):
            is_break = tok in (NEWLINE, PARAGRAPH)
            if not is_break and _needs_capital(tok):
                starts = (prev is None and not self._mid_sentence) or (
                    prev is not None and (prev in (NEWLINE, PARAGRAPH) or ends_sentence(prev))
                )
                if starts:
                    tok = _capitalize(tok)
                    tokens[i] = tok
            if prev is None:
                sep = " " if (self._needs_space and not is_break) else ""
            elif is_break or prev in (NEWLINE, PARAGRAPH):
                sep = ""
            else:
                sep = " "
            pieces.append(sep + tok)
            prev = tok
        self._last_token = prev
        out = "".join(pieces)
        self.text += out
        return out
