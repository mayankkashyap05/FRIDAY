"""Shared fixtures for FRIDAY tests."""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_APP = None


@pytest.fixture(scope='session')
def qt_app():
    """Keep one QApplication alive for the whole test session."""
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
    from PySide6.QtWidgets import QApplication
    global _APP
    existing = QCoreApplication.instance()
    if existing is not None and not isinstance(existing, QApplication):
        raise RuntimeError('Qt widget tests require a QApplication')
    _APP = existing or QApplication([])
    _APP.setQuitOnLastWindowClosed(False)
    yield _APP
    QThreadPool.globalInstance().waitForDone(2000)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    _APP.processEvents()
    # Keep the Python wrapper alive until interpreter shutdown.


@pytest.fixture(autouse=True)
def ui_test_lifetime(request):
    name = request.node.path.name
    if not (name.startswith(('test_ui_', 'test_island_')) or
            name in ('test_dictation_paste.py', 'test_windows_bringup.py', 'test_main_ui_wiring.py')):
        yield
        return
    app = request.getfixturevalue('qt_app')
    yield
    from PySide6.QtCore import QCoreApplication, QEvent
    for widget in app.topLevelWidgets():
        shutdown = getattr(widget, 'shutdown', None)
        if shutdown:
            shutdown()
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


@pytest.fixture()
def headless_ui(tmp_path, monkeypatch, qt_app):
    """A real native controller with an offscreen Qt widget and isolated settings."""
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setattr('ui.API_FILE', tmp_path / 'api_keys.json')
    from ui.bridge.controller import UiController
    from ui.island.voice_hud import VoiceHUD
    from ui.settings_store import SettingsStore
    hud = VoiceHUD()
    controller = UiController(hud, store=SettingsStore(tmp_path / 'ui_settings.json'))
    try:
        yield controller
    finally:
        controller.shutdown()
        hud.shutdown()
        hud.close()
        hud.deleteLater()
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        qt_app.processEvents()
