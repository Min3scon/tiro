"""Acoustic deltas for true fixes (rare-word clips) and for wrong candidates (LibriSpeech words)."""

import json
import sys
import time
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tiro.asr import ParakeetEngine  # noqa: E402
from tiro.correct.acoustic import AcousticScorer  # noqa: E402
from tiro.correct.text import letters, split_punct  # noqa: E402

device = sys.argv[1] if len(sys.argv) > 1 else "cuda"
eng = ParakeetEngine(ROOT / "models" / "parakeet-tdt-0.6b-v2", device=device)
eng.load()
sc = AcousticScorer(eng)
print("device", eng.device_label)

rare = json.loads((ROOT / "dev" / "rare_words.json").read_text())
TRUE = {
    "GeoGuessr": ["GeoGuessr", "George", "Geography", "Gesser"],
    "Kubernetes": ["Kubernetes", "Cuban", "Coverness"],
    "Siobhan": ["Siobhan", "Shivan", "Sheehan"],
    "PyTorch": ["PyTorch", "Pie Torch", "Python"],
    "Zettelkasten": ["Zettelkasten", "Zettel", "Castle"],
    "Tiro": ["Tiro", "Tyre", "Hero"],
    "Supabase": ["Supabase", "Super Bass", "Superb"],
    "Jira": ["Jira", "Gira", "Jura"],
    "Niamh": ["Niamh", "Neve", "Name"],
    "Nuitka": ["Nuitka", "Nautica", "Nuke"],
    "Nando's": ["Nando's", "Nandos", "Nando"],
}

timings = []
for i, item in enumerate(rare):
    for voice in ("hazel", "zira"):
        wav, _ = sf.read(ROOT / "tests" / "data" / "rare" / f"{i:02d}_{voice}.wav", dtype="float32")
        words = eng.transcribe(wav)
        text = " ".join(w.text for w in words)
        target = item["expect"]
        cands = TRUE[target]
        print(f"\n[{i:02d} {voice}] {text}")
        # every span of 1..3 words: report the span whose best candidate is the target with the lowest delta
        best = None
        for a in range(len(words)):
            for b in range(a + 1, min(a + 3, len(words)) + 1):
                span = words[a:b]
                lead = split_punct(span[0].text)[0]
                trail = split_punct(span[-1].text)[2]
                full = [lead + c + trail for c in cands]
                t0 = time.perf_counter()
                d = sc.deltas(span, full)
                timings.append((time.perf_counter() - t0) * 1000)
                if d is None:
                    continue
                if best is None or d[0] < best[1][0]:
                    best = (" ".join(w.text for w in span), d, [round(w.conf, 2) for w in span], span, full)
        if best:
            heard, d, confs, span, full = best
            d3 = sc.deltas(span, full, ilm_weight=0.3)
            d6 = sc.deltas(span, full, ilm_weight=0.6)
            print(f"   span '{heard}' conf={confs}")
            for c, x, y, z in zip(cands, d, d3, d6):
                print(f"      {c:14s} {x:6.1f} | ilm.3 {y:6.1f} | ilm.6 {z:6.1f}")

timings.sort()
print(f"\nscoring time: median {timings[len(timings)//2]:.1f} ms, p95 {timings[int(len(timings)*.95)]:.1f} ms")
