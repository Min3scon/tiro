import psutil, os
for p in psutil.process_iter(["name"]):
    if (p.info["name"] or "").lower() == "tiro.exe":
        print("pid", p.pid, "cwd", p.cwd())
        env = p.environ()
        for k in ("LOCALAPPDATA", "APPDATA", "TEMP", "USERPROFILE", "PYTHONHOME", "PYTHONPATH", "TIRO_CONFIG_DIR"):
            print(f"  {k} = {env.get(k)}")
        for f in p.open_files():
            if f.path.lower().endswith((".log", ".json", ".txt")) or "Tiro" in f.path:
                print("  open:", f.path, "mode", getattr(f, "mode", "?"))
