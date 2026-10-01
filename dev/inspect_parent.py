import psutil, datetime
for pid in (11972,):
    try:
        p = psutil.Process(pid)
        print(pid, p.name(), datetime.datetime.fromtimestamp(p.create_time()), p.cmdline()[:6])
        pp = p.parent(); print("  parent:", pp.pid, pp.name(), pp.cmdline()[:4] if pp else None)
    except Exception as e:
        print(pid, "gone:", e)
me = psutil.Process()
chain = []
while me:
    chain.append(f"{me.pid}:{me.name()}")
    me = me.parent()
print("my chain:", " <- ".join(chain))
import os
print("exe mtime:", datetime.datetime.fromtimestamp(os.path.getmtime(r"D:\dictation\dist\Tiro\Tiro.exe")))
