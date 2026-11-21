"""FRIDAY ambient UI — theme tokens and QML asset location.

Kept Qt-free on purpose: the theme table is plain data, so it can be imported by
the test-suite, by tooling, and by ``ui.py`` without dragging in PySide6.

The palette values mirror the legacy ``THEMES`` dict in ``ui_legacy.py``
byte-for-byte (asserted by ``tests/test_ui_siri_qt.py``) so the four classic
looks are preserved across the migration. The extra ``irid_*`` entries drive
the perimeter glow's iridescent sweep, which the Tk UI had no concept of.
"""

from __future__ import annotations

from pathlib import Path

#: Directory holding every ``.qml`` asset for the ambient interface.
QML_DIR = Path(__file__).resolve().parent / "qml"

#: QML module URI used by ``qmldir`` for local type + singleton resolution.
QML_MODULE = "ui_siri.qml"

THEMES: dict[str, dict] = {
    "cyber": {
        "name": "cyber", "bg": "#000000", "panel": "#010c10", "hdr": "#00080d",
        "input": "#000d12", "pri": "#00d4ff", "mid": "#007a99", "dim": "#003344",
        "dimmer": "#001520", "acc": "#ff6600", "acc2": "#ffcc00",
        "text": "#8ffcff", "green": "#00ff88", "red": "#ff3333",
        "mutcol": "#ff3366", "inp_border": "#003344",
        "irid_a": "#00d4ff", "irid_b": "#7d5cff", "irid_c": "#ff4fd8",
        "irid_d": "#ffcc00",
    },
    "ocean": {
        "name": "ocean", "bg": "#01060f", "panel": "#02101c", "hdr": "#020b16",
        "input": "#02111f", "pri": "#38bdf8", "mid": "#0e7490", "dim": "#0a3a55",
        "dimmer": "#031a2e", "acc": "#f97316", "acc2": "#fde047",
        "text": "#bae6fd", "green": "#4ade80", "red": "#f87171",
        "mutcol": "#fb7185", "inp_border": "#0a3a55",
        "irid_a": "#38bdf8", "irid_b": "#818cf8", "irid_c": "#22d3ee",
        "irid_d": "#fde047",
    },
    "aurora": {
        "name": "aurora", "bg": "#010b07", "panel": "#02150c", "hdr": "#02130a",
        "input": "#02170d", "pri": "#4ade80", "mid": "#16a34a", "dim": "#0b4f2c",
        "dimmer": "#032316", "acc": "#facc15", "acc2": "#fde047",
        "text": "#bbf7d0", "green": "#4ade80", "red": "#f87171",
        "mutcol": "#fb7185", "inp_border": "#0b4f2c",
        "irid_a": "#4ade80", "irid_b": "#22d3ee", "irid_c": "#a3e635",
        "irid_d": "#facc15",
    },
    "sunset": {
        "name": "sunset", "bg": "#0d0306", "panel": "#17070a", "hdr": "#15080a",
        "input": "#1c0a0c", "pri": "#fb923c", "mid": "#9a3412", "dim": "#5b1e0b",
        "dimmer": "#2a0d06", "acc": "#f472b6", "acc2": "#fde047",
        "text": "#fed7aa", "green": "#4ade80", "red": "#f87171",
        "mutcol": "#fb7185", "inp_border": "#5b1e0b",
        "irid_a": "#fb923c", "irid_b": "#f472b6", "irid_c": "#fde047",
        "irid_d": "#ff6600",
    },
}

THEME_NAMES: tuple[str, ...] = tuple(THEMES)
DEFAULT_THEME = "cyber"


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
    """Full theme dict handed to ``Theme.apply()`` in QML."""
    base = dict(THEMES.get(name, THEMES[DEFAULT_THEME]))
    base["lightMode"] = bool(light)
    base["reducedMotion"] = bool(reduced_motion)
    base["transparency"] = bool(transparency)
    base["uiScale"] = float(scale)
    return base


def qml_url(relative: str) -> str:
    """Absolute file URL for a QML asset (works under ``sys.frozen`` only when
    the ``qml`` directory ships next to the executable)."""
    return str(QML_DIR / relative)


__all__ = [
    "QML_DIR", "QML_MODULE", "THEMES", "THEME_NAMES", "DEFAULT_THEME",
    "theme_file", "build_theme", "qml_url",
]
