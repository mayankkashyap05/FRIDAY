"""Shared fixtures for FRIDAY tests."""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_APP = None


@pytest.fixture()
def headless_ui(tmp_path, monkeypatch):
    """A real native controller with an offscreen Qt widget and isolated settings."""
    global _APP
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setattr('ui.API_FILE', tmp_path / 'api_keys.json')
    from PySide6.QtWidgets import QApplication
    from ui.bridge.controller import UiController
    from ui.island.voice_hud import VoiceHUD
    from ui.settings_store import SettingsStore
    _APP = QApplication.instance() or QApplication([])
    hud = VoiceHUD()
    controller = UiController(hud, store=SettingsStore(tmp_path / 'ui_settings.json'))
    try:
        yield controller
    finally:
        controller.shutdown()
        hud.close()
