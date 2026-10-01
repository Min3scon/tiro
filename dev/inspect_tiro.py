import psutil
for p in psutil.process_iter(["pid", "name", "create_time", "ppid"]):
    if p.info["name"] and p.info["name"].lower() == "tiro.exe":
        import datetime
        print("pid", p.pid, "ppid", p.info["ppid"], "started", datetime.datetime.fromtimestamp(p.info["create_time"]))
        try:
            parent = psutil.Process(p.info["ppid"]); print("parent:", parent.name(), parent.exe())
        except Exception as e:
            print("parent: ?", e)
        env = p.environ()
        for k in ("LOCALAPPDATA", "APPDATA", "USERNAME", "TIRO_CONFIG_DIR", "TIRO_TEST_WAV", "TIRO_TEST_TARGET_CLASS"):
            print(f"  {k} = {env.get(k)}")
        for f in p.open_files():
            if "Tiro" in f.path or "dictation" in f.path:
                print("  open:", f.path)
        print("  threads:", p.num_threads(), "cpu%:", p.cpu_percent(interval=2.0))
