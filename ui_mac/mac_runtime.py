"""Zero-maintenance macOS runtime bridge for FRIDAY UI.

Installs in-memory patches so the existing Qt ``ui`` package works natively on
macOS without modifying any file under ``ui/``.
"""
from __future__ import annotations

import sys
from typing import Any

_BRIDGE_INSTALLED = False
_ORIGINAL_WIN32_SYMBOLS: dict[str, Any] = {}
_ORIGINAL_DICTATION_SYMBOLS: dict[str, Any] = {}
_EVENT_FILTER: Any = None
_APP_INIT_PATCHED = False
_ORIG_QAPP_INIT = None


def _is_darwin_or_force(force: bool) -> bool:
    return bool(force or sys.platform == "darwin")


def install_mac_bridge(force: bool = False) -> bool:
    """Install the macOS bridge. Idempotent.

    On non-macOS platforms this is a no-op unless ``force=True`` (for tests).
    """
    global _BRIDGE_INSTALLED
    if _BRIDGE_INSTALLED:
        return True
    if not _is_darwin_or_force(force):
        return False

    try:
        _install_win32_overlay_bridge()
    except Exception:
        pass
    try:
        _install_dictation_paste_bridge()
    except Exception:
        pass
    try:
        _install_qt_app_bridge()
    except Exception:
        pass

    _BRIDGE_INSTALLED = True
    return True


def uninstall_mac_bridge() -> bool:
    """Restore all patched symbols. Intended for clean unit testing."""
    global _BRIDGE_INSTALLED, _EVENT_FILTER, _APP_INIT_PATCHED, _ORIG_QAPP_INIT

    # Restore win32_overlay
    try:
        import ui.island.win32_overlay as wo

        for name, orig in _ORIGINAL_WIN32_SYMBOLS.items():
            try:
                if orig is _MISSING:
                    if hasattr(wo, name):
                        delattr(wo, name)
                else:
                    setattr(wo, name, orig)
            except Exception:
                pass
        # Remove any injected-only names we added that weren't tracked.
        for name in ("IS_MACOS", "is_macos_cocoa_available", "set_accessory_activation_policy",
                     "ns_window_from_wid", "configure_cocoa_overlay", "CocoaTopmostGuard"):
            if name not in _ORIGINAL_WIN32_SYMBOLS and hasattr(wo, name):
                try:
                    delattr(wo, name)
                except Exception:
                    pass
    except Exception:
        pass
    _ORIGINAL_WIN32_SYMBOLS.clear()

    # Restore dictation_paste
    try:
        import ui.island.dictation_paste as dp

        for name, orig in _ORIGINAL_DICTATION_SYMBOLS.items():
            try:
                if orig is _MISSING:
                    if hasattr(dp, name):
                        delattr(dp, name)
                else:
                    setattr(dp, name, orig)
            except Exception:
                pass
        for name in ("CocoaPasteAdapter", "get_production_paste_adapter",
                     "ProductionPasteAdapter", "_PRODUCTION_ADAPTER_CLS",
                     "is_accessibility_trusted"):
            if name not in _ORIGINAL_DICTATION_SYMBOLS and hasattr(dp, name):
                try:
                    delattr(dp, name)
                except Exception:
                    pass
    except Exception:
        pass
    _ORIGINAL_DICTATION_SYMBOLS.clear()

    # Remove event filter
    try:
        if _EVENT_FILTER is not None:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                try:
                    app.removeEventFilter(_EVENT_FILTER)
                except Exception:
                    pass
    except Exception:
        pass
    _EVENT_FILTER = None

    # Restore QApplication.__init__ patch
    try:
        if _APP_INIT_PATCHED and _ORIG_QAPP_INIT is not None:
            from PySide6.QtWidgets import QApplication

            QApplication.__init__ = _ORIG_QAPP_INIT  # type: ignore[method-assign]
    except Exception:
        pass
    _APP_INIT_PATCHED = False
    _ORIG_QAPP_INIT = None

    _BRIDGE_INSTALLED = False
    return True


# --- Win32 overlay injection ---

def _install_win32_overlay_bridge() -> None:
    from ui_mac import cocoa_overlay as co
    import ui.island.win32_overlay as wo

    # Mirror all public names from cocoa_overlay, but preserve originals.
    names = [
        # Cocoa-specific helpers
        "IS_MACOS",
        "is_macos_cocoa_available",
        "set_accessory_activation_policy",
        "ns_window_from_wid",
        "configure_cocoa_overlay",
        "CocoaTopmostGuard",
        # Win32-overlay compatible names
        "configure_overlay",
        "set_accepts_focus",
        "ensure_topmost",
        "enforce_topmost_on_windowposchanging",
        "Win32TopmostGuard",
        "hit_test",
        "foreground_is_fullscreen",
        "foreground_window",
        "clipboard_sequence",
        "send_paste",
    ]
    # Also mirror constants that exist in cocoa_overlay for completeness.
    for const_name in (
        "GWL_EXSTYLE", "WS_EX_TOPMOST", "WS_EX_TOOLWINDOW", "WS_EX_NOACTIVATE",
        "SWP_NOSIZE", "SWP_NOMOVE", "SWP_NOZORDER", "SWP_NOREDRAW",
        "SWP_NOACTIVATE", "SWP_FRAMECHANGED", "SWP_NOOWNERZORDER",
        "GW_HWNDPREV", "WM_WINDOWPOSCHANGING",
        "EVENT_SYSTEM_FOREGROUND", "EVENT_SYSTEM_SWITCHEND", "EVENT_SYSTEM_MINIMIZEEND",
        "WINEVENT_OUTOFCONTEXT", "WINEVENT_SKIPOWNPROCESS",
        "HWND_TOPMOST", "HWND_NOTOPMOST", "HWND_BOTTOM",
        "wintypes", "user32", "WINEVENTPROC", "WINDOWPOS",
        "_get_window_long_ptr", "_set_window_long_ptr", "_has_visible_external_above",
    ):
        if hasattr(co, const_name):
            names.append(const_name)

    # Deduplicate preserving order
    seen = set()
    deduped = []
    for n in names:
        if n not in seen:
            seen.add(n)
            deduped.append(n)

    for name in deduped:
        if not hasattr(co, name):
            continue
        if name not in _ORIGINAL_WIN32_SYMBOLS:
            _ORIGINAL_WIN32_SYMBOLS[name] = getattr(wo, name, _MISSING)
        try:
            setattr(wo, name, getattr(co, name))
        except Exception:
            pass

    # Critical: make existing `if win32_overlay.IS_WINDOWS:` guards in ui code
    # activate the injected cocoa implementations on macOS.
    if "IS_WINDOWS" not in _ORIGINAL_WIN32_SYMBOLS:
        _ORIGINAL_WIN32_SYMBOLS["IS_WINDOWS"] = getattr(wo, "IS_WINDOWS", False)
    try:
        wo.IS_WINDOWS = True  # on darwin/force, use cocoa path
    except Exception:
        pass


_MISSING = object()


# --- Dictation paste injection ---

def _install_dictation_paste_bridge() -> None:
    try:
        import ui.island.dictation_paste as dp
    except Exception:
        return
    try:
        from ui_mac.cocoa_paste import CocoaPasteAdapter, is_accessibility_trusted
    except Exception:
        return

    for name, value in (
        ("CocoaPasteAdapter", CocoaPasteAdapter),
        ("is_accessibility_trusted", is_accessibility_trusted),
        ("ProductionPasteAdapter", CocoaPasteAdapter),
        ("_PRODUCTION_ADAPTER_CLS", CocoaPasteAdapter),
    ):
        if name not in _ORIGINAL_DICTATION_SYMBOLS:
            _ORIGINAL_DICTATION_SYMBOLS[name] = getattr(dp, name, _MISSING)
        try:
            setattr(dp, name, value)
        except Exception:
            pass

    def get_production_paste_adapter(*args, **kwargs):
        return CocoaPasteAdapter(*args, **kwargs)

    if "get_production_paste_adapter" not in _ORIGINAL_DICTATION_SYMBOLS:
        _ORIGINAL_DICTATION_SYMBOLS["get_production_paste_adapter"] = getattr(
            dp, "get_production_paste_adapter", _MISSING
        )
    try:
        dp.get_production_paste_adapter = get_production_paste_adapter
    except Exception:
        pass


# --- Qt application bridge ---

def _install_qt_app_bridge() -> None:
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return

    _apply_font_substitutions()
    _set_accessory_policy()

    app = QApplication.instance()
    if app is not None:
        _configure_app(app)
        _install_event_filter(app)
        return

    # Hook QApplication.__init__ so the filter is installed when the app is created.
    global _APP_INIT_PATCHED, _ORIG_QAPP_INIT
    if _APP_INIT_PATCHED:
        return
    try:
        _ORIG_QAPP_INIT = QApplication.__init__

        def _patched_init(self, *args, **kwargs):
            _ORIG_QAPP_INIT(self, *args, **kwargs)
            try:
                _configure_app(self)
                _install_event_filter(self)
            except Exception:
                pass

        QApplication.__init__ = _patched_init  # type: ignore[method-assign]
        _APP_INIT_PATCHED = True
    except Exception:
        pass


def _apply_font_substitutions() -> None:
    try:
        from PySide6.QtGui import QFont

        QFont.insertSubstitutions(
            "Segoe UI",
            ["SF Pro Text", ".AppleSystemUIFont", "Helvetica Neue"],
        )
        QFont.insertSubstitutions(
            "Consolas",
            ["SF Mono", "Menlo", "Monaco"],
        )
    except Exception:
        pass


def _set_accessory_policy() -> None:
    try:
        from ui_mac.cocoa_overlay import set_accessory_activation_policy

        set_accessory_activation_policy()
    except Exception:
        pass


def _configure_app(app) -> None:
    try:
        app.setQuitOnLastWindowClosed(False)
    except Exception:
        pass
    _apply_font_substitutions()
    _set_accessory_policy()
    # Ensure crisp tray icons in macOS menu bar - Qt handles this, but we can
    # set the attribute if available.
    try:
        from PySide6.QtCore import Qt

        # AA_UseHighDpiPixmaps is default in Qt6, but harmless to set.
        attr = getattr(Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps", None)
        if attr is not None:
            app.setAttribute(attr, True)
    except Exception:
        pass


def _install_event_filter(app) -> None:
    global _EVENT_FILTER
    if _EVENT_FILTER is not None:
        return
    try:
        from PySide6.QtCore import QObject, QEvent, Qt
        from PySide6.QtWidgets import QWidget
        from ui_mac.cocoa_overlay import configure_cocoa_overlay
    except Exception:
        return

    class _MacApplicationEventFilter(QObject):
        def eventFilter(self, obj, event):
            try:
                etype = event.type()
                if etype in (QEvent.Type.Show, QEvent.Type.WinIdChange):
                    if isinstance(obj, QWidget):
                        try:
                            top = obj.isWindow() or obj.windowFlags() & Qt.Window
                        except Exception:
                            top = False
                        if not top:
                            # isWindow() is reliable for top-level widgets.
                            try:
                                top = bool(obj.isWindow())
                            except Exception:
                                top = False
                        if top:
                            flags = int(obj.windowFlags())
                            stay_on_top = bool(flags & int(Qt.WindowStaysOnTopHint))
                            frameless = bool(flags & int(Qt.FramelessWindowHint))
                            if stay_on_top or frameless:
                                try:
                                    wid = int(obj.winId())
                                    if wid:
                                        configure_cocoa_overlay(wid)
                                except Exception:
                                    pass
            except Exception:
                pass
            return False

    try:
        _EVENT_FILTER = _MacApplicationEventFilter(app)
        app.installEventFilter(_EVENT_FILTER)
    except Exception:
        _EVENT_FILTER = None
