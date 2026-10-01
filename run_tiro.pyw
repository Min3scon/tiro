"""Launch Tiro from a source checkout without a console window (pythonw run_tiro.pyw)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tiro.main import main  # noqa: E402

raise SystemExit(main())
