"""Render every settings page offscreen into dev/ui_settings_<page>.png for visual review."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

qapp = QApplication(sys.argv)
from tiro.ui import theme  # noqa: E402

theme.load_fonts()
from tiro.config import Settings  # noqa: E402
from tiro.correct.history import Correction  # noqa: E402
from tiro.correct.metrics import Metrics  # noqa: E402
from tiro.keys import HotkeySpec  # noqa: E402


class _LM:
    name, device, avg_ms, calls = "qwen2.5-1.5b-instruct", "cuda", 64.0, 18


class _Corrector:
    language = _LM()


class StubService:
    def __init__(self):
        self.metrics = Metrics()
        self.language_status = "Qwen2.5 1.5B on GPU"
        self.corrector = _Corrector()

        class R:
            def __init__(self, ms, tier, n):
                self.ms, self.tier, self.changes, self.flagged = ms, tier, [0] * n, 1

        for i in range(60):
            self.metrics.add(R(0.2 if i % 3 else 4.5, 0 if i % 3 else 1, 1 if i % 17 == 0 else 0))
            self.metrics.add_end(180 + i % 7, 1.2)

    def history_count(self):
        return 412

    def corrections(self):
        return [Correction("George asser", "GeoGuessr", 3, "", 0), Correction("Shivan", "Siobhan", 1, "", 0),
                Correction("super bass", "Supabase", 2, "", 0)]

    def history_terms(self):
        return [("GeoGuessr", 31), ("Kubernetes", 12), ("Siobhan", 9), ("Figma", 8), ("Shoreditch", 5),
                ("Obsidian", 4), ("Nando's", 3), ("PyTorch", 3)]


class _UpdService:
    def rollback_target(self):
        return "2.0.2"


class _Updates:
    def __init__(self):
        from tiro.update.service import Status

        self.status = Status(state="ready", version="2.0.4", summary="Text appears sooner after you stop talking.",
                             notes_url="https://github.com/Min3scon/tiro/releases", last_check="2026-10-01T12:00:00+00:00")
        self.service = _UpdService()

    def __getattr__(self, name):
        return lambda *a, **k: None


class StubApp:
    safe_mode = False
    dictating = False

    def __init__(self):
        self.updates = _Updates()
        self.settings = Settings()
        self.settings.show_latency = True
        self.settings.dictionary = ["GeoGuessr", "Kubernetes", "super bass -> Supabase"]
        self.last_text = "Hello there."
        self.correction = StubService()

    def _fix_spec(self):
        return HotkeySpec.parse(self.settings.fix_hotkey)

    def status_line(self):
        return "Ready \u00b7 hold Right Ctrl"

    def engine_description(self):
        return "Parakeet TDT 0.6B v2 running on NVIDIA GeForce RTX 3070 (CUDA)."

    def autostart_enabled(self):
        return True

    def data_folder(self):
        return Path(r"C:\Users\you\AppData\Local\Tiro")

    def __getattr__(self, name):
        return lambda *a, **k: None

    def update_setting(self, k, v):
        setattr(self.settings, k, v)

    def list_mics(self):
        return ["USB Audio Interface (Inputs 1+2)", "Headset Microphone"]

    def default_mic_name(self):
        return "USB Audio Interface (Inputs 1+2)"


from tiro.ui.settings import PAGES, SettingsWindow  # noqa: E402

w = SettingsWindow(StubApp())
w.resize(860, 760)
w.show()
for key, _label in PAGES:
    w.show_page(key)
    qapp.processEvents()
    w.grab().save(str(ROOT / "dev" / f"ui_settings_{key}.png"))
print("rendered", len(PAGES), "pages")

if "--update-states" in sys.argv:  # the Updates page in every state it can show
    from tiro.update.service import Status

    last = "2026-10-01T18:00:00+00:00"
    notes = "https://github.com/Min3scon/tiro/releases"
    for st in (Status(state="up-to-date", detail="up to date", last_check=last),
               Status(state="checking"),
               Status(state="downloading", version="2.0.4", detail="Downloading 12 of 48 MB",
                      summary="Text appears sooner after you stop talking."),
               Status(state="ready", version="2.0.4", summary="Text appears sooner after you stop talking.",
                      notes_url=notes, last_check=last),
               Status(state="available", version="2.0.4", detail="Download the new version from the website",
                      summary="Text appears sooner after you stop talking.", notes_url=notes, last_check=last),
               Status(state="error", detail="The download didn't finish; Tiro will try again later", last_check=last),
               Status(state="offline", detail="No internet connection", last_check=last),
               Status(state="skipped", detail="Metered connection: Tiro will check later", last_check=last)):
        w.app.updates.status = st
        w.refresh_updates()
        w.show_page("updates")
        qapp.processEvents()
        w.grab().save(str(ROOT / "dev" / f"ui_settings_updates_{st.state}.png"))
    print("rendered the Updates page in 8 states")

    # the tray: menu label, tooltip and notification for each update state
    from tiro.ui import tray as tray_mod

    app = w.app
    app.engine_state, app.last_session = "ready", None
    t = tray_mod.Tray(app)
    shown = []
    t.message = lambda title, text, action=None: shown.append((title, text, action))
    for st in (None, Status(state="ready", version="2.0.4", summary="Faster."),
               Status(state="available", version="2.0.4", summary="Faster.")):
        t.set_update(st)
        t._refresh()
        print(f"tray [{st.state if st else 'none'}]: menu '{t.actions['updates'].text()}', tip '{t.tray.toolTip()}'")
    t.notify_update(Status(state="ready", version="2.0.4", summary="Faster."))
    t.notify_available(Status(state="available", version="2.0.4", summary="Faster."))
    for title, text, action in shown:
        print(f"notification: {title!r} / {text!r} -> {action}")
