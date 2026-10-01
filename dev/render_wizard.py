"""Render each setup-wizard page offscreen into dev/ui_wizard_<page>.png (no downloads or benchmark run)."""
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


class StubApp:
    def __init__(self):
        self.settings = Settings()
        self.settings.dictionary = ["GeoGuessr"]

    def __getattr__(self, name):
        return lambda *a, **k: None

    def update_setting(self, k, v):
        setattr(self.settings, k, v)

    def list_mics(self):
        return ["USB Audio Interface (Inputs 1+2)"]

    def default_mic_name(self):
        return "USB Audio Interface (Inputs 1+2)"


from tiro.ui import setup_wizard  # noqa: E402

setup_wizard.SetupWizard._enter = lambda self, key: None  # don't start downloads/benchmarks while rendering
w = setup_wizard.SetupWizard(StubApp())
w.resize(900, 640)
w.show()
for i, (key, _label, _page) in enumerate(w.pages):
    w._go(i)
    if key == "download":
        from PySide6.QtWidgets import QLabel, QProgressBar

        for r, (title, frac, text) in enumerate((("Speech model · Parakeet TDT 0.6B v2", 0.46,
                                                  "1.12 of 2.42 GB · 38 MB/s · 34 s left"),
                                                 ("AI check · Qwen2.5 1.5B", 0.0, "waiting…"))):
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(int(frac * 1000))
            w.dl_grid.addWidget(QLabel(title), r * 2, 0)
            info = QLabel(text)
            info.setObjectName("stat")
            w.dl_grid.addWidget(info, r * 2, 1)
            w.dl_grid.addWidget(bar, r * 2 + 1, 0, 1, 2)
    if key == "bench":
        w.bench_rows["load"].setText("Ready on NVIDIA GeForce RTX 3070 (CUDA) in 7 s")
        w.bench_rows["speech"].setText("6 s of speech understood in 52 ms, 113× faster than you talk ✓")
        w.bench_rows["ai"].setText("64 ms per check on the GPU ✓ (limit 150 ms)")
    qapp.processEvents()
    w.grab().save(str(ROOT / "dev" / f"ui_wizard_{i:02d}_{key}.png"))
print("rendered", len(w.pages))
