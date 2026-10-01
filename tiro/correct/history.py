"""Your dictation history (local SQLite) and the vocabulary profile built from it.

Stored only on this computer, in the Tiro data folder. Nothing is recorded when learning is switched off
or when the text went into a password / secure field. The profile is rebuilt in the background.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from tiro.correct.text import common_words, letters, words_of

log = logging.getLogger(__name__)

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_STOP = frozenset(
    "a an and are as at be but by for from had has have he her his i if in is it its me my no not of on or our "
    "she so that the their them then there they this to too was we were what when which who will with you your "
    "just like yeah ok okay um uh".split()
)


@dataclass
class Correction:
    heard: str
    written: str
    count: int
    context: str  # a few words from the sentence it was taught in
    ts: float


class HistoryStore:
    """SQLite store: past dictations and taught corrections."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        with self._lock:
            self._db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS dictation (
                    id INTEGER PRIMARY KEY, ts REAL NOT NULL, app TEXT, title TEXT, text TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS correction (
                    heard TEXT PRIMARY KEY, written TEXT NOT NULL, context TEXT, count INTEGER DEFAULT 1, ts REAL);
                """
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------------ dictations
    def add(self, text: str, app: str = "", title: str = "") -> None:
        text = text.strip()
        if not text:
            return
        with self._lock:
            self._db.execute("INSERT INTO dictation (ts, app, title, text) VALUES (?, ?, ?, ?)",
                             (time.time(), app, title[:200], text))
            self._db.commit()

    def recent(self, limit: int = 5000) -> list[tuple[float, str, str, str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, app, title, text FROM dictation ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return rows[::-1]

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM dictation").fetchone()[0]

    def clear(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM dictation")
            self._db.commit()
            self._db.execute("VACUUM")

    # ------------------------------------------------------------------ corrections
    def corrections(self) -> list[Correction]:
        with self._lock:
            rows = self._db.execute(
                "SELECT heard, written, count, COALESCE(context, ''), COALESCE(ts, 0) FROM correction "
                "ORDER BY ts DESC").fetchall()
        return [Correction(*r) for r in rows]

    def add_correction(self, heard: str, written: str, context: str = "") -> None:
        heard = heard.strip()
        written = written.strip()
        if not heard or not written or letters(heard) == letters(written) and heard == written:
            return
        with self._lock:
            self._db.execute(
                "INSERT INTO correction (heard, written, context, count, ts) VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(heard) DO UPDATE SET written=excluded.written, context=excluded.context, "
                "count=count+1, ts=excluded.ts",
                (heard, written, context, time.time()))
            self._db.commit()

    def update_correction(self, old_heard: str, heard: str, written: str) -> None:
        with self._lock:
            self._db.execute("UPDATE correction SET heard=?, written=? WHERE heard=?", (heard, written, old_heard))
            self._db.commit()

    def delete_correction(self, heard: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM correction WHERE heard=?", (heard,))
            self._db.commit()

    def clear_corrections(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM correction")
            self._db.commit()

    # ------------------------------------------------------------------ export
    def export(self, path: Path, profile: Profile | None, dictionary: list[str], include_history: bool) -> None:
        """Everything Tiro has learned, as JSON (or CSV of corrections if the path ends in .csv)."""
        path = Path(path)
        if path.suffix.lower() == ".csv":
            with path.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["kind", "heard", "written", "count"])
                for c in self.corrections():
                    w.writerow(["correction", c.heard, c.written, c.count])
                for d in dictionary:
                    w.writerow(["dictionary", "", d, ""])
                for written, count in (profile.notable_terms() if profile else []):
                    w.writerow(["history_word", "", written, count])
            return
        data = {
            "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
            "dictionary": dictionary,
            "corrections": [c.__dict__ for c in self.corrections()],
            "history_words": [{"word": w, "count": n} for w, n in (profile.notable_terms() if profile else [])],
        }
        if include_history:
            data["dictations"] = [{"ts": ts, "app": app, "title": title, "text": text}
                                  for ts, app, title, text in self.recent(100000)]
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class Profile:
    """What your history says about your vocabulary: frequencies, names, phrases, co-occurrence."""

    SNIPPETS = 3

    def __init__(self):
        self.counts: Counter[str] = Counter()
        self.forms: dict[str, Counter[str]] = defaultdict(Counter)  # key -> spellings used mid-sentence
        self.first_forms: dict[str, Counter[str]] = defaultdict(Counter)  # ... and at the start of a sentence
        self.capitalised: Counter[str] = Counter()  # key -> times written capitalised mid-sentence
        self.phrases: Counter[str] = Counter()  # multi-word names ("Elden Ring")
        self.cooc: dict[str, Counter[str]] = defaultdict(Counter)
        self.sentences: list[str] = []  # each distinct sentence once
        self._sentence_ids: dict[str, int] = {}
        self.where: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=Profile.SNIPPETS))  # key -> sentences
        self.apps: dict[str, Counter[str]] = defaultdict(Counter)
        self.dictations = 0

    def add(self, text: str, app: str = "") -> None:
        self.dictations += 1
        common = common_words()
        for sentence in _SENT_SPLIT.split(text):
            toks = words_of(sentence)
            if not toks:
                continue
            notable: list[str] = []
            for i, tok in enumerate(toks):
                key = letters(tok)
                if len(key) < 2:
                    continue
                self.counts[key] += 1
                (self.forms if i > 0 else self.first_forms)[key][tok] += 1
                mid_cap = i > 0 and tok[:1].isupper() and not tok.isupper()
                if mid_cap or any(c.isupper() for c in tok[1:]):
                    self.capitalised[key] += 1
                if key not in common or mid_cap or any(c.isupper() for c in tok[1:]) or (tok.isupper() and len(tok) > 1):
                    notable.append(key)
            # runs of capitalised words (not at the sentence start): multi-word names
            run: list[str] = []
            for i, tok in enumerate(toks + ["."]):
                if i > 0 and tok[:1].isupper() and i < len(toks):
                    run.append(tok)
                    continue
                if 2 <= len(run) <= 4:
                    self.phrases[" ".join(run)] += 1
                    notable.append(letters(" ".join(run)))
                run = []
            content = {letters(t) for t in toks if letters(t) not in _STOP and len(letters(t)) > 2}
            text = sentence.strip()[:200]
            sid = self._sentence_ids.get(text)
            if sid is None:
                sid = self._sentence_ids[text] = len(self.sentences)
                self.sentences.append(text)
            for key in content | set(notable):
                if not self.where[key] or self.where[key][-1] != sid:
                    self.where[key].append(sid)
            for key in set(notable):
                self.cooc[key].update(content - {key})
                if app:
                    self.apps[key][app] += 1

    def display(self, key: str) -> str:
        forms = self.forms.get(key) or self.first_forms.get(key)
        return forms.most_common(1)[0][0] if forms else key

    def notable_terms(self, min_count: int = 2) -> list[tuple[str, int]]:
        """(spelling, count) for the words and names that characterise your vocabulary."""
        common = common_words()
        out = []
        for key, n in self.counts.items():
            if n < min_count:
                continue
            spelled = self.display(key)
            proper = self.capitalised[key] >= max(1, n // 2)
            if key not in common or proper or any(c.isupper() for c in spelled[1:]):
                out.append((spelled, n))
        for phrase, n in self.phrases.items():
            if n >= min_count:
                out.append((phrase, n))
        out.sort(key=lambda x: (-x[1], x[0].lower()))
        return out

    def context_words(self, key: str, n: int = 40) -> set[str]:
        return {w for w, _ in self.cooc.get(key, Counter()).most_common(n)}

    def examples(self, key: str) -> list[str]:
        """A few recent sentences of yours that contain this word or name."""
        return [self.sentences[i] for i in self.where.get(key, ())]

    @classmethod
    def build(cls, rows: list[tuple[float, str, str, str]]) -> Profile:
        p = cls()
        for _ts, app, _title, text in rows:
            p.add(text, app or "")
        return p
