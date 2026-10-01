"""The Mac's entry for the update feed: Macs are told about a new version and open its download page; they don't
patch themselves in place (an ad-hoc signed app would lose its Microphone and Accessibility permissions on every
update until Tiro has an Apple Developer ID).

    python tools/mac_fragment.py release/Tiro-mac-arm64.dmg > update-fragment-mac-arm64.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tiro import GITHUB_REPO, __version__  # noqa: E402

from release_notes import section  # noqa: E402


def main() -> None:
    summary = next((line.strip().lstrip("-* ").strip() for line in section(__version__).splitlines()
                    if line.strip()), "")
    entry = {"version": __version__, "summary": summary, "critical": False, "min_os": "13.0",
             "notes_url": f"https://github.com/{GITHUB_REPO}/releases/tag/v{__version__}"}
    print(json.dumps({"platform": "mac-arm64", "entry": entry}, indent=1))


if __name__ == "__main__":
    main()
