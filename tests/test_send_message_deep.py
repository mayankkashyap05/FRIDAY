"""100%-offline combinatorial tests for actions.send_message."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from actions import send_message as sm


class FakePyAutoGUI:
    def __init__(self):
        self.calls = []

    def hotkey(self, *keys):
        self.calls.append(("hotkey", keys))

    def press(self, key):
        self.calls.append(("press", key))

    def write(self, *a, **k):
        raise AssertionError("pyautogui.write must never be used for messages/contacts")


class FakeWindow:
    def __init__(self, title, visible=True, minimized=False):
        self.title = title
        self.visible = visible
        self.isMinimized = minimized
        self.activated = 0
        self.restored = 0

    def activate(self):
        self.activated += 1

    def restore(self):
        self.restored += 1


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch, tmp_path):
    pg = FakePyAutoGUI()
    pg.real_open_app = sm._open_app
    pg.real_wait_for_window = sm._wait_for_window
    monkeypatch.setattr(sm, "_PG", pg)
    monkeypatch.setattr(sm.time, "sleep", lambda _s: None)

    clipboard = {"text": "original clipboard"}

    fake_pyperclip = types.ModuleType("pyperclip")
    fake_pyperclip.paste = lambda: clipboard["text"]
    fake_pyperclip.copy = lambda text: clipboard.__setitem__("text", text)
    monkeypatch.setitem(sys.modules, "pyperclip", fake_pyperclip)

    monkeypatch.setattr(sm, "_open_app", lambda name: True)
    monkeypatch.setattr(sm, "_wait_for_window", lambda *a, **k: None)
    monkeypatch.setattr(sm.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(sm, "_foreground_title", lambda: "WhatsApp")
    monkeypatch.setattr(sm, "MEMORY_PATH", tmp_path / "long_term.json")
    yield pg


# ---------------------------------------------------------------------------
# Unicode-safe paste
# ---------------------------------------------------------------------------

def test_paste_text_handles_unicode_and_restores_clipboard(_offline_env):
    assert sm._paste_text("नमस्ते 😊\nsecond line") is True
    assert ("hotkey", ("ctrl", "v")) in _offline_env.calls
    import pyperclip
    assert pyperclip.paste() == "original clipboard"
    assert not any(c[0] == "write" for c in _offline_env.calls)


def test_paste_text_restores_clipboard_even_on_exception(_offline_env, monkeypatch):
    def boom(*keys):
        raise RuntimeError("hotkey failed")

    monkeypatch.setattr(_offline_env, "hotkey", boom)
    assert sm._paste_text("message") is False
    import pyperclip
    assert pyperclip.paste() == "original clipboard"


# ---------------------------------------------------------------------------
# Window focus gating
# ---------------------------------------------------------------------------

def test_wait_for_window_focuses_and_verifies(_offline_env, monkeypatch):
    window = FakeWindow("WhatsApp", minimized=True)
    monkeypatch.setattr(sm, "_get_windows", lambda: [window])
    ticks = iter([0, 0.1, 1.0])
    monkeypatch.setattr(sm.time, "monotonic", lambda: next(ticks))
    found = _offline_env.real_wait_for_window(["whatsapp"], timeout=1.0)
    assert found is window
    assert window.restored == 1 and window.activated == 1


def test_wait_for_window_failure_never_types(_offline_env, monkeypatch):
    monkeypatch.setattr(sm, "_get_windows", lambda: [])
    ticks = iter([0, 1.0])
    monkeypatch.setattr(sm.time, "monotonic", lambda: next(ticks))
    assert _offline_env.real_wait_for_window(["whatsapp"], timeout=0.5) is None
    assert _offline_env.calls == []


def test_nonmatching_window_is_ignored(_offline_env, monkeypatch):
    window = FakeWindow("Visual Studio Code")
    monkeypatch.setattr(sm, "_get_windows", lambda: [window])
    ticks = iter([0, 1.0])
    monkeypatch.setattr(sm.time, "monotonic", lambda: next(ticks))
    assert _offline_env.real_wait_for_window(["whatsapp"], timeout=0.5) is None
    assert window.activated == 0


# ---------------------------------------------------------------------------
# Deep links, memory lookup and draft mode
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("web,expected", [
    (False, "whatsapp://send?phone=919876543210&text="),
    (True, "https://wa.me/919876543210?text="),
])
def test_whatsapp_phone_deep_link(monkeypatch, web, expected):
    urls = []
    monkeypatch.setattr(sm.webbrowser, "open", lambda url: urls.append(url) or True)
    monkeypatch.setattr(sm, "_wait_for_window", lambda *a, **k: FakeWindow("WhatsApp"))
    result = sm.send_message({"receiver": "+91 98765 43210",
                              "message_text": "Hello", "web": web})
    assert "sent" in result.lower()
    assert urls[0].startswith(expected)
    assert "Hello" in urls[0]


def test_whatsapp_draft_mode_does_not_press_enter(_offline_env, monkeypatch):
    monkeypatch.setattr(sm, "_wait_for_window", lambda *a, **k: FakeWindow("WhatsApp"))
    result = sm.send_message({"receiver": "9876543210", "message_text": "Draft",
                              "send": False})
    assert "prepared" in result.lower()
    assert ("press", "enter") not in _offline_env.calls


def test_telegram_username_deep_link(monkeypatch):
    urls = []
    monkeypatch.setattr(sm.webbrowser, "open", lambda url: urls.append(url) or True)
    monkeypatch.setattr(sm, "_wait_for_window", lambda *a, **k: FakeWindow("Telegram"))
    result = sm.send_message({"receiver": "@Ada_Lovelace", "message_text": "Hi",
                              "platform": "telegram"})
    assert "sent" in result.lower()
    assert urls[0].startswith("tg://resolve?domain=Ada_Lovelace&text=")


def test_memory_contact_resolves_to_phone(tmp_path):
    (tmp_path / "long_term.json").write_text(json.dumps({
        "relationships": {"friend_ravi": {"value": "close friend, +91 90000 11111"}}
    }), encoding="utf-8")
    assert sm._resolve_phone("Ravi") == "919000011111"


def test_failed_focus_aborts_without_typing(_offline_env, monkeypatch):
    monkeypatch.setattr(sm, "_wait_for_window", lambda *a, **k: None)
    result = sm.send_message({"receiver": "9876543210", "message_text": "Secret"})
    assert "nothing was sent" in result.lower()
    assert _offline_env.calls == []


def test_missing_receiver_message_and_none_params():
    assert "who" in sm.send_message(None).lower()
    assert "what" in sm.send_message({"receiver": "Ravi"}).lower()
    assert isinstance(sm.send_message({"receiver": "Ravi", "message_text": "Hi",
                                       "platform": "unknown_app"}), str)


def test_open_app_delegates_without_win_key_typing(_offline_env, monkeypatch):
    from actions import open_app
    calls = []
    monkeypatch.setattr(open_app, "open_app", lambda params: calls.append(params) or "Opened successfully")
    assert _offline_env.real_open_app("WhatsApp") is True
    assert calls == [{"app_name": "WhatsApp"}]
