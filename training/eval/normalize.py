"""Text normalisation for scoring.

`leaderboard()` is the Open ASR leaderboard's normaliser: Whisper's EnglishTextNormalizer (MIT, OpenAI) with its
British->American spelling map (english_spelling.json, copied from openai/whisper). Both reference and hypothesis
go through it, so case, punctuation, number formatting and spelling variants don't count as errors.

`formatted()` keeps case and punctuation (punctuation marks become their own tokens) for the "dictation quality"
score, where "Hello, world." and "hello world" do differ.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_SPELLING = Path(__file__).with_name("english_spelling.json")


@lru_cache(maxsize=1)
def _whisper():
    from training.eval.whisper_normalizer import EnglishTextNormalizer

    return EnglishTextNormalizer(json.loads(_SPELLING.read_text(encoding="utf-8")))


def leaderboard(text: str) -> str:
    return _whisper()(text or "")


_PUNCT = re.compile(r"([.,!?;:\"()\[\]{}…—–])")
_SPACES = re.compile(r"\s+")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def formatted(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_QUOTES)
    text = _PUNCT.sub(r" \1 ", text)
    return _SPACES.sub(" ", text).strip()
