"""Tiro: fast, private, local dictation for Windows and macOS (Apple Silicon)."""

__version__ = "2.0.3"
try:  # test builds only (tools/build.ps1 -TestVersion writes tiro/_build.py); never present in a release
    from tiro._build import VERSION as __version__  # noqa: F401
except ImportError:
    pass
APP_NAME = "Tiro"
GITHUB_REPO = "Min3scon/tiro"
WEBSITE = "https://min3scon.github.io/tiro/"
