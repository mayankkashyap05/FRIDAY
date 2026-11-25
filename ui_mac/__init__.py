"""Pure dynamic PEP 562 proxy to ``ui`` with macOS Cocoa bridge auto-install.

``ui_mac`` MUST NOT duplicate widgets, panels, themes, physics, FSM, or any
business logic from ``ui/``. Every attribute lookup is forwarded dynamically to
the live ``ui`` package so future additions to ``ui`` are picked up
automatically with zero edits here.
"""
from __future__ import annotations

import importlib
import sys
from typing import Any

_BRIDGE_INSTALLED = False


def _ensure_bridge() -> None:
    global _BRIDGE_INSTALLED
    if _BRIDGE_INSTALLED:
        return
    if sys.platform != "darwin":
        return
    try:
        from .mac_runtime import install_mac_bridge

        install_mac_bridge()
    except Exception:
        return
    _BRIDGE_INSTALLED = True


# Install eagerly on macOS so module-level consumers see the patched seams.
_ensure_bridge()


def _ui_module():
    return importlib.import_module("ui")


def __getattr__(name: str) -> Any:
    if name == "FridayUI":
        _ensure_bridge()
        ui = _ui_module()
        value = getattr(ui, name)
        globals()[name] = value
        return value
    ui = _ui_module()
    try:
        value = getattr(ui, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    if name.isupper() or name.startswith("_"):
        globals()[name] = value
    return value


def __dir__() -> list[str]:
    names = set(globals().keys())
    try:
        names.update(dir(_ui_module()))
    except Exception:
        pass
    return sorted(names)


# Convenience explicit re-export (also handled by __getattr__).
try:
    from ui import FridayUI as _FridayUI
    FridayUI = _FridayUI
except Exception:
    pass
