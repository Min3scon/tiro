"""Tiro visual identity: ink-dark surfaces, parchment text, a Tyrian-violet -> rose -> ember gradient."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QFont, QFontDatabase, QLinearGradient

from tiro.paths import asset

INK_950 = "#0B0A10"
INK_900 = "#121119"
INK_850 = "#17151F"
INK_800 = "#1E1B28"
INK_750 = "#252231"
INK_700 = "#2E2A3B"
LINE = "rgba(255,255,255,0.08)"
TEXT = "#F2EDE4"  # parchment
TEXT_2 = "#A9A2B6"
TEXT_3 = "#6F6980"
TYRIAN = "#8B5CF6"
ROSE = "#E6508E"
EMBER = "#FF8A4C"
ACCENT = ROSE
DANGER = "#FF6B6B"
OK = "#5CD6A2"

GRADIENT_STOPS = ((0.0, TYRIAN), (0.55, ROSE), (1.0, EMBER))

FONT_FAMILY = "Inter"
_fonts_loaded = False


def load_fonts() -> str:
    """Register the bundled Inter faces; returns the family to use."""
    global _fonts_loaded, FONT_FAMILY
    if not _fonts_loaded:
        families = set()
        for face in ("Regular", "Medium", "SemiBold", "Bold"):
            fid = QFontDatabase.addApplicationFont(str(asset("fonts", f"Inter-{face}.ttf")))
            if fid >= 0:
                families.update(QFontDatabase.applicationFontFamilies(fid))
        FONT_FAMILY = "Inter" if "Inter" in families else "Segoe UI"
        _fonts_loaded = True
    return FONT_FAMILY


def font(px: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setPixelSize(max(1, round(px)))
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def gradient(x0: float, y0: float, x1: float, y1: float, alpha: float = 1.0) -> QLinearGradient:
    g = QLinearGradient(QPointF(x0, y0), QPointF(x1, y1))
    for pos, hex_ in GRADIENT_STOPS:
        c = QColor(hex_)
        c.setAlphaF(alpha)
        g.setColorAt(pos, c)
    return g


def qcolor(hex_: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_)
    c.setAlphaF(alpha)
    return c


def menu_qss() -> str:
    return f"""
    QMenu {{
        background: {INK_850};
        border: 1px solid {LINE};
        border-radius: 12px;
        padding: 6px;
        color: {TEXT};
        font-family: "{FONT_FAMILY}";
        font-size: 13px;
    }}
    QMenu::item {{
        padding: 7px 28px 7px 12px;
        border-radius: 7px;
        margin: 1px 0px;
    }}
    QMenu::item:selected {{ background: {INK_750}; }}
    QMenu::item:disabled {{ color: {TEXT_3}; }}
    QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 8px; }}
    QMenu::indicator {{ width: 14px; height: 14px; left: 6px; }}
    QMenu::right-arrow {{ width: 8px; height: 8px; right: 10px; }}
    """
