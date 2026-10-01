"""Dictation-style sentences for synthetic training audio, built around Tiro's vocabulary list.

    python -m training.synth.sentences --count 120000 --out work/data/synth/sentences.jsonl

Each line: {"id", "text" (how it should be written), "say" (what the TTS voice reads), "terms", "category"}.

* Terms come from assets/words/starter-dictionary.txt (the same list Tiro ships). 15% of them, picked by a fixed
  hash, are HELD OUT: they never appear in synthetic training sentences, so accuracy on them measures
  generalisation rather than memorisation (training/synth/heldout_terms.txt lists them).
* The 72 sentences of the personal-vocabulary test set (tests/accuracy/phrases.json) are never generated: any
  sentence sharing 6+ consecutive words with one of them is dropped (and counted).
* `say` differs from `text` only where a term needs a pronunciation hint (training/synth/respell.tsv) or a
  number/symbol needs to be read out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path

from training.common import REPO, write_jsonl

DICT = REPO / "assets" / "words" / "starter-dictionary.txt"
HERE = Path(__file__).parent
HELD_OUT_SHARE = 0.15

FIRST_NAMES = ["Sarah", "James", "Priya", "Tom", "Aisha", "Daniel", "Mei", "Liam", "Sofia", "Oliver", "Chloe",
               "Ethan", "Grace", "Noah", "Hannah", "Lucas", "Emma", "Jack", "Zara", "Ben", "Maya", "Sam", "Leo",
               "Ruby", "Adam", "Isla", "Ryan", "Nina", "Owen", "Amelia", "Kai", "Lily", "Max", "Ella", "Jamal",
               "Fatima", "Carlos", "Yuki", "Anna", "Mark", "Rachel", "Josh", "Megan", "Chris", "Laura", "Dev"]
PLACES = ["the office", "the gym", "the station", "town", "the airport", "the shop", "the library", "home",
          "the café", "the park", "the hospital", "school", "the cinema", "the pub", "the supermarket"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "tomorrow", "tonight",
        "this weekend", "next week", "later today", "this afternoon", "first thing in the morning"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
FOODS = ["pizza", "sushi", "tacos", "curry", "a burger", "pasta", "noodles", "a salad", "fish and chips", "ramen"]
OBJECTS = ["laptop", "charger", "keys", "headphones", "jacket", "passport", "notebook", "umbrella", "wallet",
           "water bottle", "phone", "glasses", "backpack", "receipt", "ticket"]
ORDINAL = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth",
           9: "ninth", 10: "tenth", 11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth",
           15: "fifteenth", 16: "sixteenth", 17: "seventeenth", 18: "eighteenth", 19: "nineteenth",
           20: "twentieth", 21: "twenty first", 22: "twenty second", 23: "twenty third", 24: "twenty fourth",
           25: "twenty fifth", 26: "twenty sixth", 27: "twenty seventh", 28: "twenty eighth", 29: "twenty ninth",
           30: "thirtieth", 31: "thirty first"}

# {T} = the term. Other slots: {N} name, {N2} second name, {P} place, {D} day, {F} food, {O} object,
# {TIME}, {MONEY}, {NUM}, {DATE}, {PCT}.
TEMPLATES = {
    "Games": [
        "Anyone want to play {T} {D}?", "I've been playing {T} all week and I can't stop.",
        "{N}, are you still up for {T} after work?", "The new {T} update completely broke my build.",
        "Can we do a couple of rounds of {T} before dinner?", "I finally hit a new rank in {T} last night.",
        "Honestly {T} is the best thing I've played this year.", "Is {T} on sale right now or did I miss it?",
        "My brother keeps beating me at {T} and it's driving me mad.", "Let's stream some {T} {D}.",
        "I spent {NUM} hours on {T} this weekend, which is a bit embarrassing.",
        "Do you know if {T} has crossplay with my friends on console?", "We should try the {T} co-op mode.",
        "I lost my whole save file in {T}.", "{N} says {T} is way easier than it looks.",
        "Remind me to download the {T} patch tonight.", "The {T} tournament starts at {TIME}.",
    ],
    "Apps and brands": [
        "Can you send it to me on {T}?", "I'll share the file through {T} in a minute.",
        "My {T} subscription renews on {DATE}.", "{N} just switched everything over to {T}.",
        "Does {T} have a student discount?", "I can't log into {T} again, it keeps timing out.",
        "Order it from {T}, it'll be here {D}.", "The {T} app updated and now I can't find anything.",
        "We're moving the whole team to {T} next month.", "I paid {MONEY} for it on {T}.",
        "Have you tried the new {T} feature yet?", "Message me on {T} when you get to {P}.",
        "I think {T} is down for everyone right now.", "Can you check whether {T} delivers here?",
    ],
    "Tech and programming": [
        "The build fails because {T} isn't installed on the server.", "We need to upgrade {T} before the release.",
        "I spent the whole afternoon debugging {T}.", "Can you review my pull request for the {T} migration?",
        "Let's set up {T} for the new project.", "The {T} cluster went down at {TIME} last night.",
        "Is there a good tutorial for {T} anywhere?", "I'm rewriting the backend to use {T}.",
        "Our {T} costs went up by {PCT} this month.", "{N} is the expert on {T}, ask her.",
        "Make sure the {T} config is committed before you deploy.", "The tests pass locally but {T} keeps failing.",
        "I added {T} to the roadmap for next quarter.", "Has anyone benchmarked {T} against the old version?",
    ],
    "People names": [
        "I'm meeting {T} for coffee {D}.", "Can you ask {T} to call me back?", "{T} said the report is nearly done.",
        "Tell {T} I'll be about ten minutes late.", "I got a lovely message from {T} this morning.",
        "Is {T} coming to the party on {DAY}?", "Send the invoice to {T} and copy me in.",
        "{T} and {N} are presenting at {TIME}.", "Happy birthday, {T}! Hope you have a great day.",
        "I haven't heard from {T} since {MONTH}.", "Could you book a table for me and {T}?",
        "Thanks so much, {T}, that really helped.", "{T}'s flight lands at {TIME}.",
    ],
    "Places": [
        "We're flying to {T} on {DATE}.", "Have you ever been to {T}?", "The conference is in {T} this year.",
        "I grew up near {T}, actually.", "It's raining again in {T}.", "How long is the drive to {T}?",
        "My cousin just moved to {T} for work.", "Book me a hotel in {T} for three nights.",
        "The train to {T} leaves at {TIME}.", "I'd love to visit {T} next summer.",
        "There's a great little restaurant in {T} we should try.", "{N} is working from {T} this week.",
    ],
    "Slang and internet speak": [
        "Honestly that was so {T}.", "Lol {T}, I can't believe he said that.", "That new song is {T}, no cap.",
        "Ngl the ending was a bit {T}.", "We were all like {T} when it happened.", "Stop being so {T} about it.",
        "Bro that's {T}.", "The vibes at the party were {T}.",
    ],
    "Units and measurements": [
        "The box is about {NUM} {T} wide.", "Add {NUM} {T} of sugar and stir.", "It's roughly {NUM} {T} from here.",
        "The file is {NUM} {T} so it might take a while.", "Set the oven to {NUM} {T}.",
        "I ran {NUM} {T} this morning.", "We need {NUM} {T} of cable for the new room.",
    ],
    "Acronyms and initialisms": [
        "Can you send me the {T} by {D}?", "The {T} needs to be signed before {DATE}.",
        "I'm not sure what the {T} stands for.", "We should loop in {N} from {T}.",
        "The {T} meeting moved to {TIME}.", "Please double check the {T} before you submit it.",
        "Our {T} is way too high this quarter.", "FYI the {T} has been updated.",
    ],
    "Science, medicine and niche terms": [
        "The doctor mentioned {T} at my appointment.", "I read a paper about {T} last night.",
        "Is {T} something I should be worried about?", "We're running another experiment on {T} {D}.",
        "The lecture on {T} was surprisingly interesting.", "Can you look up the dosage for {T}?",
        "{N} is writing her thesis on {T}.",
    ],
}

GENERAL = [
    "Hey, just checking you got home okay.", "I'll be there in about {NUM} minutes.",
    "Can you pick up {F} on the way back?", "Don't forget your {O}, it's on the kitchen table.",
    "Meeting moved to {TIME} on {DAY}, see you then.", "Thanks for yesterday, it was really good to catch up.",
    "I think I left my {O} at {P}.", "Running a bit late, start without me.",
    "Can we push the call to {TIME}?", "The rent is {MONEY} this month.", "Remind me to call {N} at {TIME}.",
    "Let's get {F} {D}.", "I've attached the slides, let me know what you think.",
    "Sorry I missed your call, I was driving.", "What time does {P} close {D}?",
    "Just landed, I'll text you when I'm out of the airport.", "Could you send me the address again?",
    "The package should arrive on {DATE}.", "I'm so tired, I'm going to bed early tonight.",
    "Dear {N}, thank you for your email. I'll get back to you by {DAY}.",
    "Hi {N}, quick reminder that the report is due on {DATE}.", "Note to self: buy milk, eggs and bread.",
    "Our budget for the trip is {MONEY} each.", "Prices went up {PCT} since last year.",
    "Can you believe it's already {MONTH}?", "{N} and {N2} are coming over at {TIME}.",
    "Okay, so the plan is to leave at {TIME} and be back by dinner.", "I'll transfer you {MONEY} tonight.",
    "The kids have a dentist appointment on {DATE} at {TIME}.", "I owe you one, seriously.",
]


def terms_by_category() -> dict[str, list[str]]:
    cats, cur = {}, None
    for line in DICT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            cur = line.lstrip("# ").strip()
            cats[cur] = []
        elif cur:
            cats[cur].append(line)
    return cats


def is_held_out(term: str) -> bool:
    h = int(hashlib.sha1(term.lower().encode("utf-8")).hexdigest(), 16)
    return (h % 1000) < HELD_OUT_SHARE * 1000


def load_respell() -> dict[str, str]:
    path = HERE / "respell.tsv"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "\t" in line:
            k, v = line.split("\t", 1)
            out[k.strip()] = v.strip()
    return out


def number_words(n: int) -> str:
    ones = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
           "seventeen eighteen nineteen".split()
    tens = "twenty thirty forty fifty sixty seventy eighty ninety".split()
    if n < 20:
        return ones[n]
    if n < 100:
        return tens[n // 10 - 2] + ("" if n % 10 == 0 else " " + ones[n % 10])
    if n < 1000:
        rest = n % 100
        return ones[n // 100] + " hundred" + ("" if rest == 0 else " and " + number_words(rest))
    if n < 1_000_000:
        rest = n % 1000
        return number_words(n // 1000) + " thousand" + ("" if rest == 0 else " " + number_words(rest))
    return str(n)


def fill(template: str, rng: random.Random) -> tuple[str, str]:
    """Fill the non-term slots. Returns (written, spoken)."""
    written, spoken = template, template
    def put(slot, w, s=None):
        nonlocal written, spoken
        written = written.replace(slot, w, 1)
        spoken = spoken.replace(slot, s if s is not None else w, 1)
    while "{N}" in written:
        n = rng.choice(FIRST_NAMES)
        put("{N}", n)
    while "{N2}" in written:
        put("{N2}", rng.choice(FIRST_NAMES))
    for slot, pool in (("{P}", PLACES), ("{D}", DAYS), ("{F}", FOODS), ("{O}", OBJECTS), ("{MONTH}", MONTHS)):
        while slot in written:
            put(slot, rng.choice(pool))
    while "{DAY}" in written:
        put("{DAY}", rng.choice(DAYS[:7]))
    while "{TIME}" in written:
        h, m = rng.randint(1, 12), rng.choice([0, 0, 15, 30, 45, 10, 20, 50])
        ap = rng.choice(["a.m.", "p.m."])
        w = f"{h}:{m:02d} {ap}" if m else f"{h} {ap}"
        s = f"{number_words(h)} {('oh ' + number_words(m)) if 0 < m < 10 else number_words(m) if m else ''} " \
            f"{'a m' if ap == 'a.m.' else 'p m'}"
        put("{TIME}", w, re.sub(r"\s+", " ", s))
    while "{MONEY}" in written:
        d = rng.choice([5, 12, 20, 45, 60, 99, 150, 240, 500, 1200])
        c = rng.choice([0, 0, 0, 50, 99])
        w = f"${d}" + (f".{c:02d}" if c else "")
        s = f"{number_words(d)} dollars" + (f" {number_words(c)}" if c else "")
        put("{MONEY}", w, s)
    while "{NUM}" in written:
        n = rng.choice([2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 100])
        put("{NUM}", str(n) if n > 10 else number_words(n), number_words(n))
    while "{PCT}" in written:
        n = rng.choice([3, 5, 8, 10, 12, 15, 20, 25, 30, 40])
        put("{PCT}", f"{n}%", f"{number_words(n)} percent")
    while "{DATE}" in written:
        mon, day = rng.choice(MONTHS), rng.randint(1, 28)
        put("{DATE}", f"{mon} {day}", f"{mon} {ORDINAL[day]}")
    return written, spoken


def leak_grams(path: Path, n: int = 6) -> set[tuple[str, ...]]:
    grams = set()
    for item in json.loads(path.read_text(encoding="utf-8")):
        words = re.findall(r"[a-z0-9']+", item["expect"].lower())
        grams.update(tuple(words[i:i + n]) for i in range(len(words) - n + 1))
    return grams


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=120000)
    p.add_argument("--general-share", type=float, default=0.25)
    p.add_argument("--out", type=Path, default=REPO / "work" / "data" / "synth" / "sentences.jsonl")
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    rng = random.Random(a.seed)
    cats = terms_by_category()
    held = sorted(t for ts in cats.values() for t in ts if is_held_out(t))
    (HERE / "heldout_terms.txt").write_text("\n".join(held) + "\n", encoding="utf-8")
    train_terms = {c: [t for t in ts if not is_held_out(t)] for c, ts in cats.items()}
    respell = load_respell()
    grams = leak_grams(REPO / "tests" / "accuracy" / "phrases.json")
    rows, dropped, seen = [], 0, set()
    attempts = 0
    while len(rows) < a.count and attempts < a.count * 4:
        attempts += 1
        if rng.random() < a.general_share:
            cat, term = "general", None
            template = rng.choice(GENERAL)
        else:
            cat = rng.choice([c for c in train_terms if train_terms[c] and c in TEMPLATES])
            term = rng.choice(train_terms[cat])
            template = rng.choice(TEMPLATES[cat])
        written, spoken = fill(template, rng)
        if term:
            written = written.replace("{T}", term)
            spoken = spoken.replace("{T}", respell.get(term, term))
        if written in seen:
            continue
        words = re.findall(r"[a-z0-9']+", written.lower())
        if any(tuple(words[i:i + 6]) in grams for i in range(len(words) - 5)):
            dropped += 1
            continue
        seen.add(written)
        rows.append({"id": f"synth-{len(rows):06d}", "text": written, "say": spoken,
                     "terms": [term] if term else [], "category": cat})
    n = write_jsonl(a.out, rows)
    print(f"wrote {n} sentences to {a.out}; {len(held)} held-out terms; dropped {dropped} overlapping the test set")


if __name__ == "__main__":
    main()
