"""Shared Qt application fixture for all UI tests.

Ensures a single QGuiApplication (or QCoreApplication on headless hosts where
QtGui cannot be imported) exists per test session. This prevents the
QCoreApplication vs QGuiApplication collision that occurs when
``test_ui_siri_core.py`` creates a plain ``QCoreApplication`` in the same
process as ``test_ui_siri_qt.py`` which needs ``QGuiApplication`` for
QPixmap/QML window tests.

Use the ``qt_app`` fixture instead of constructing ``QCoreApplication`` or
``QGuiApplication`` inline in any test.
"""

from __future__ import annotations

import sys

import pytest


def _create_qt_app():
    """Create QGuiApplication if QtGui is importable, else QCoreApplication."""
    try:
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
        return app, "gui"
    except Exception:
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance() or QCoreApplication([])
        return app, "core"


@pytest.fixture(scope="session")
def qt_app():
    """Shared Qt application instance for the whole test session.

    On hosts with QtGui (Windows, macOS, Linux with GL), this is a
    ``QGuiApplication`` so QPixmap/QML window tests work. On headless hosts
    without QtGui, it falls back to ``QCoreApplication``.
    """
    app, _kind = _create_qt_app()
    yield app
    # Intentionally do not quit: other tests in the session may still need it.


@pytest.fixture
def app(qt_app):
    """Function-scoped alias of the shared ``qt_app`` fixture.

    Kept for backward compatibility with existing test files that reference
    ``app``. All tests in the session share the same underlying application.
    """
    return qt_app
