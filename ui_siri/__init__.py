"""FRIDAY ambient interface (Qt Quick).

Import policy: **importing this package must never import Qt.** ``ui.py`` is the
only module allowed to pull Qt in, and it does so inside ``FridayUI.__init__``.
That keeps ``python -c "import ui"`` (used by the test-suite and by tooling)
working on a machine with no Qt installed, and it keeps ``ui_legacy`` usable as
an escape hatch.

Submodules, in dependency order (each layer only imports the ones above it):

    event_bus        thread-safe typed bus + dataclass schemas   (stdlib only)
    state_machine    6-state lifecycle controller + timers        (stdlib only)
    card_mapper      legacy string / envelope -> typed cards      (stdlib only)
    win32_native     guarded ctypes wrappers for Win10/11        (ctypes only)
    audio_ux         earcons + pycaw ducking                     (stdlib + optional)
    image_provider   QQuickImageProvider for video frames        (Qt)
    bridge           QObject/QML bridge, models, thread dispatch (Qt)
    controller       multi-window orchestrator, theme, timers    (Qt)
"""

from __future__ import annotations

__all__ = [
    "event_bus",
    "state_machine",
    "card_mapper",
    "win32_native",
    "audio_ux",
    "image_provider",
    "bridge",
    "controller",
]

__version__ = "2.0.0"
