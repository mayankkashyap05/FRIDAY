"""FRIDAY Siri UI — design tokens.

A single, canonical theme: Apple Siri. The legacy cyber/ocean/aurora/sunset
variants were removed with ui_legacy.py; Siri is the sole palette of the
application.

Kept Qt-free on purpose: the theme table is plain data, so it can be imported
by the test-suite, by tooling, and by ``ui.py`` without dragging in PySide6.

The colour science mirrors Apple's Siri orb — luminous cyan/aqua, deep
sapphire/cobalt blue, radiant magenta/rose, soft iridescent violet, and pure
additive white-hot highlights — over a dark frosted-glass surface that reads
cleanly against both pure white and pitch-black desktop backgrounds.
"""

from __future__ import annotations

from pathlib import Path

#: Directory holding every ``.qml`` asset for the Siri interface.
QML_DIR = Path(__file__).resolve().parent / "qml"

#: QML module URI used by ``qmldir`` for local type + singleton resolution.
QML_MODULE = "ui_siri.qml"

# ── Apple Siri palette ──────────────────────────────────────────────────────
#
# pri   : primary ribbon / accent (luminous cyan-aqua)
# mid   : deep sapphire / cobalt blue
# dim   : recessed dark-glass depth
# acc   : radiant magenta / rose (confirmation, attention)
# acc2  : soft iridescent violet
# irid_*: the five iridescent ribbon hues that drift inside the orb
# text  : near-white additive glow for labels and metrics
SIRI = {
    "name": "siri",
    # surfaces (dark frosted glass)
    "bg": "#000000",
    "panel": "#0A0E14",
    "hdr": "#060A0F",
    "input": "#0E141C",
    # accents
    "pri": "#06F7FF",   # luminous electric aqua / cyan — primary ribbon
    "mid": "#1E5FFF",   # deep sapphire / cobalt — ribbon mid
    "dim": "#1A2430",
    "dimmer": "#05080C",
    "acc": "#FF2D9F",   # radiant magenta / rose — attention
    "acc2": "#B030FF",  # iridescent violet / amethyst — reasoning
    "text": "#FFFFFF",  # pure additive white for labels
    "green": "#34D399",
    "red": "#FB7185",
    "mutcol": "#F43F5E",
    "inp_border": "#2A3644",
    # iridescent ribbon hues (cyan → sapphire → magenta → violet → white-hot)
    "irid_a": "#06F7FF",
    "irid_b": "#1E5FFF",
    "irid_c": "#FF2D9F",
    "irid_d": "#B030FF",
    "irid_e": "#FFFFFF",
}

THEMES: dict[str, dict] = {"siri": SIRI}

THEME_NAMES: tuple[str, ...] = tuple(THEMES)
DEFAULT_THEME = "siri"


def theme_file(root: Path) -> Path:
    return root / "config" / "ui_theme.json"


def build_theme(
    name: str,
    *,
    light: bool = False,
    reduced_motion: bool = False,
    transparency: bool = True,
    scale: float = 1.0,
) -> dict:
    """Full theme dict handed to ``Theme.apply()`` in QML.

    Siri is dark-only; ``light`` is accepted for API parity but the palette
    stays the dark Siri glass (a light Siri does not exist in Apple's design).
    """
    base = dict(THEMES.get(name, THEMES[DEFAULT_THEME]))
    base["lightMode"] = False
    base["reducedMotion"] = bool(reduced_motion)
    base["transparency"] = bool(transparency)
    base["uiScale"] = float(scale)
    return base


def qml_url(relative: str) -> str:
    """Absolute ``QUrl.fromLocalFile``-ready path for a QML asset."""
    return str((QML_DIR / relative).resolve())


__all__ = [
    "QML_DIR", "QML_MODULE", "THEMES", "THEME_NAMES", "DEFAULT_THEME",
    "SIRI", "theme_file", "build_theme", "qml_url",
]
