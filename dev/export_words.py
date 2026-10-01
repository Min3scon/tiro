from wordfreq import top_n_list
import re
words = []
seen = set()
for w in top_n_list("en", 60000):
    w = w.lower()
    if re.fullmatch(r"[a-z][a-z']*", w) and w not in seen:
        seen.add(w)
        words.append(w)
    if len(words) >= 40000:
        break
open(r"D:\dictation\assets\words\common-en.txt", "w", encoding="utf-8").write("\n".join(words) + "\n")
print(len(words), words[:10], words[-5:])
