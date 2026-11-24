"""Fully offline combinatorial tests for actions.system_tray.

No real keyboard hook, tray icon or subprocess is ever created.
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from actions import system_tray as st


class FakeKeyboard:
    def __init__(self):
        self.handlers = {}
        self.added = []
        self.removed = []
        self.unhooked = 0
        self.counter = 0

    def add_hotkey(self, hotkey, callback, suppress=False):
        self.counter += 1
        handle = f"handle-{self.counter}"
        self.added.append((hotkey, handle))
        self.handlers[hotkey] = callback
        return handle

    def remove_hotkey(self, handle):
        self.removed.append(handle)

    def unhook_all_hotkeys(self):
        self.unhooked += 1


class FakeIcon:
    def __init__(self, name, icon, title, menu):
        self.name, self.icon, self.title, self.menu = name, icon, title, menu
        self.stopped = False
        self.run_calls = 0

    def run(self):
        self.run_calls += 1

    def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    kb = FakeKeyboard()
    monkeypatch.setattr(st, "keyboard", kb, raising=False)
    monkeypatch.setattr(st, "_KEYBOARD", True)
    monkeypatch.setattr(st, "pystray", SimpleNamespace(Icon=FakeIcon), raising=False)
    monkeypatch.setattr(st, "_PYSTRAY", True)
    with st._LOCK:
        st._tray_icon = None
        st._tray_thread = None
        st._registered_handles.clear()
        st._hotkey_callbacks.clear()
        st._last_fired.clear()
        st._hotkeys_paused = False
        st._hotkeys_registered = False
        st._last_error = ""
        st._cached_icon = None
    yield kb
    st.stop()


def _callbacks():
    return {name: lambda name=name: None for name in st.DEFAULT_HOTKEYS.values()}


# ---------------------------------------------------------------------------
# Registry + idempotency
# ---------------------------------------------------------------------------

def test_register_hotkeys_is_idempotent(_fresh_state):
    callbacks = _callbacks()
    first = st.register_hotkeys(callbacks)
    assert first == len(st.DEFAULT_HOTKEYS)
    original_handles = list(st._registered_handles.values())
    second = st.register_hotkeys(callbacks)
    assert second == first
    assert _fresh_state.removed == original_handles
    assert len(_fresh_state.added) == first * 2
    assert len(st.list_hotkeys()) == first


def test_register_hotkeys_normalizes_config_overrides(monkeypatch, tmp_path, _fresh_state):
    path = tmp_path / "ui_settings.json"
    path.write_text(json.dumps({"hotkeys": {"CTRL + ALT + Y": "briefing"}}))
    monkeypatch.setattr(st, "UI_SETTINGS_PATH", path)
    callbacks = _callbacks()
    st.register_hotkeys(callbacks)
    assert "ctrl+alt+y" in st.list_hotkeys()
    assert "ctrl+alt+m" in st.list_hotkeys()


def test_add_remove_and_list_hotkeys(_fresh_state):
    name = []
    assert st.add_hotkey("CTRL+ALT+N", lambda: name.append(1), name="custom")
    assert st.list_hotkeys()["ctrl+alt+n"] == "custom"
    handle = st._registered_handles["ctrl+alt+n"]
    assert st.remove_hotkey("ctrl + alt + n") is True
    assert handle in _fresh_state.removed
    assert "ctrl+alt+n" not in st.list_hotkeys()
    assert st.remove_hotkey("never-registered") is False


def test_diagnostics_reports_full_state(_fresh_state):
    st.register_hotkeys(_callbacks())
    info = st.diagnostics()
    expected = {
        "pystray_available", "keyboard_available", "tray_running",
        "hotkeys_registered", "hotkeys_paused", "hotkey_count",
        "active_hotkeys", "last_error",
    }
    assert expected <= set(info)
    assert info["hotkey_count"] == len(st.DEFAULT_HOTKEYS)
    assert info["hotkeys_registered"] is True
    st.pause_hotkeys()
    assert st.diagnostics()["hotkeys_paused"] is True
    st.resume_hotkeys()
    assert st.diagnostics()["hotkeys_paused"] is False


# ---------------------------------------------------------------------------
# Dispatch: off-thread, debounced, crash-proof
# ---------------------------------------------------------------------------

def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_callback_runs_off_calling_thread(_fresh_state):
    seen = []
    st.register_hotkeys({"toggle_mute": lambda: seen.append(
        (threading.current_thread().ident, threading.current_thread().name)
    )})
    handler = _fresh_state.handlers["ctrl+alt+m"]
    handler()
    assert _wait_for(lambda: bool(seen))
    ident, name = seen[0]
    assert ident != threading.current_thread().ident
    assert name.startswith("FridayHotkey-")


def test_debounce_suppresses_rapid_repeats(monkeypatch, _fresh_state):
    now = [1000.0]
    monkeypatch.setattr(st.time, "monotonic", lambda: now[0])
    calls = []
    st.register_hotkeys({"toggle_mute": lambda: calls.append(1)},
                        debounce_s=0.25)
    handler = _fresh_state.handlers["ctrl+alt+m"]
    handler()
    handler()
    assert len(calls) == 1
    now[0] += 0.3
    handler()
    assert len(calls) == 2


def test_pause_ignores_presses(monkeypatch, _fresh_state):
    calls = []
    st.register_hotkeys({"toggle_mute": lambda: calls.append(1)})
    handler = _fresh_state.handlers["ctrl+alt+m"]
    st.pause_hotkeys()
    handler()
    assert calls == []
    st.resume_hotkeys()
    st._last_fired.clear()
    handler()
    assert _wait_for(lambda: bool(calls))


def test_callback_exception_is_isolated(_fresh_state, capsys):
    def bad():
        raise RuntimeError("callback exploded")

    st.register_hotkeys({"toggle_mute": bad})
    _fresh_state.handlers["ctrl+alt+m"]()
    assert _wait_for(lambda: "callback exploded" in st.diagnostics()["last_error"])
    assert "callback exploded" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Tray icon lifecycle
# ---------------------------------------------------------------------------

def test_icon_is_64x64_rgba_and_cached():
    first = st._build_icon()
    second = st._build_icon()
    assert isinstance(first, Image.Image)
    assert first.size == (64, 64) and first.mode == "RGBA"
    assert first is second
    assert st._build_icon(force_rebuild=True) is not first


def test_configure_icon_is_idempotent_and_title_updates():
    created = []
    original = st.pystray.Icon

    def icon_factory(*args, **kwargs):
        icon = original(*args, **kwargs)
        created.append(icon)
        return icon

    st.pystray.Icon = icon_factory
    assert st.configure_icon(lambda: [], title="Friday") is True
    assert st.configure_icon(lambda: [], title="Friday") is True
    assert len(created) == 1
    assert created[0].title == "Friday"
    assert st.update_tray_title("Friday Online") is True
    assert created[0].title == "Friday Online"


def test_stop_removes_only_tracked_handles(_fresh_state):
    st.register_hotkeys(_callbacks())
    tracked = list(st._registered_handles.values())
    st.stop()
    assert sorted(_fresh_state.removed) == sorted(tracked)
    assert _fresh_state.unhooked == 0
    assert st._registered_handles == {} and st._hotkey_callbacks == {}
    assert st._hotkeys_registered is False
    assert st._tray_icon is None and st._tray_thread is None


def test_stop_joins_tray_thread_and_icon():
    icon = FakeIcon("Friday", st._build_icon(), "Friday", [])
    thread = threading.Thread(target=icon.run, daemon=True)
    thread.start()
    st._tray_icon, st._tray_thread = icon, thread
    st.stop()
    assert icon.stopped is True and not thread.is_alive()


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

def test_missing_optional_dependencies(monkeypatch, _fresh_state):
    monkeypatch.setattr(st, "_KEYBOARD", False)
    monkeypatch.setattr(st, "_PYSTRAY", False)
    assert st.register_hotkeys(_callbacks()) == 0
    assert st.configure_icon(lambda: []) is False
    assert st.update_tray_title("x") is False
    text = st.available()
    assert "tray: off" in text and "hotkeys: off" in text
