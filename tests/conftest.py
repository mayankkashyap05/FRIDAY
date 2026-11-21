"""Shared fixtures for the FRIDAY test-suite.

The UI has no GUI toolkit dependency any more: the Python side is pure stdlib +
``websockets``, and the renderer is Electron (built separately). These fixtures
therefore only provide isolated filesystem state and a headless UI controller.

Use ``headless_ui`` for anything that needs the *real* bridge + state machine +
WebSocket server without launching Electron.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


@pytest.fixture()
def headless_ui(tmp_path, monkeypatch):
    """A fully wired ``UiController`` with no native window.

    Covers the WebSocket server, the dispatcher loop, the state machine, the
    bridge stores and the frame pump — everything observable without Electron.
    """
    from ui.controller import UiController

    monkeypatch.setenv("FRIDAY_UI_HEADLESS", "1")
    monkeypatch.setattr("ui.API_FILE", tmp_path / "api_keys.json")
    controller = UiController(enable_overlay=False, token="test-token")
    controller.start()
    try:
        yield controller
    finally:
        controller.shutdown()
