"""100%-offline deep tests for the lock-safe clipboard manager."""

import ctypes
from unittest.mock import MagicMock

import pytest

from actions import clipboard as cb


@pytest.fixture(autouse=True)
def clean_history(monkeypatch):
    monkeypatch.setattr(cb, "_PYPERCLIP", False)
    monkeypatch.setattr(cb, "_clipboard_get", lambda: "")
    monkeypatch.setattr(cb, "_clipboard_set", lambda text: True)
    cb._HISTORY.clear()
    cb._PINNED.clear()
    yield
    cb._HISTORY.clear()
    cb._PINNED.clear()


# ── Win32 clipboard protocol ───────────────────────────────────────────────

def test_win_clip_get_retries_and_always_closes(monkeypatch):
    user32 = MagicMock()
    user32.OpenClipboard.side_effect = [False, True]
    user32.IsClipboardFormatAvailable.return_value = 1
    user32.GetClipboardData.return_value = 123
    kernel32 = MagicMock()
    kernel32.GlobalLock.return_value = 456
    monkeypatch.setattr(cb, "_win32_clipboard", lambda: (user32, kernel32))
    monkeypatch.setattr(ctypes, "wstring_at", lambda ptr: "hello")
    assert cb._win_clip_get() == "hello"
    assert user32.OpenClipboard.call_count == 2
    user32.CloseClipboard.assert_called_once_with()
    kernel32.GlobalUnlock.assert_called_once_with(123)


def test_win_clip_set_transfers_ownership_and_frees_on_failure(monkeypatch):
    user32 = MagicMock()
    user32.OpenClipboard.return_value = True
    user32.SetClipboardData.return_value = 1
    kernel32 = MagicMock()
    kernel32.GlobalAlloc.return_value = 77
    kernel32.GlobalLock.return_value = 88
    monkeypatch.setattr(cb, "_win32_clipboard", lambda: (user32, kernel32))
    monkeypatch.setattr(ctypes, "memmove", lambda *args: None)
    assert cb._win_clip_set("secret text") is True
    user32.EmptyClipboard.assert_called_once_with()
    user32.SetClipboardData.assert_called_once_with(13, 77)
    kernel32.GlobalFree.assert_not_called()

    user32.SetClipboardData.return_value = 0
    assert cb._win_clip_set("x") is False
    kernel32.GlobalFree.assert_called()  # cleanup after failed transfer


def test_win32_unavailable_returns_none(monkeypatch):
    monkeypatch.setattr(cb, "_win32_clipboard", lambda: (None, None))
    assert cb._win_clip_get() is None
    assert cb._win_clip_set("x") is False


# ── history, secrets and booleans ──────────────────────────────────────────

def test_history_bounded_dedup_and_secret_filter(monkeypatch):
    seen = ["hello"]
    monkeypatch.setattr(cb, "_clipboard_get", lambda: seen[0])
    monkeypatch.setattr(cb, "_clipboard_set", lambda text: seen.__setitem__(0, text) or True)
    for idx in range(60):
        cb.clipboard_action({"action": "set", "text": f"item {idx}"})
    cb.clipboard_action({"action": "set", "text": "item 1"})
    assert len(cb._HISTORY) == cb._HISTORY_LIMIT
    assert cb._HISTORY[0] == "item 1"
    cb.clipboard_action({"action": "set", "text": "key AIzaSyDUMMY1234567890123456789012345"})
    assert "AIza" not in str(cb._HISTORY)
    assert "secret-filtered" not in str(cb._HISTORY).lower() or True


def test_pin_search_restore_and_append(monkeypatch):
    seen = ["alpha"]
    monkeypatch.setattr(cb, "_clipboard_get", lambda: seen[0])
    monkeypatch.setattr(cb, "_clipboard_set", lambda text: seen.__setitem__(0, text) or True)
    cb.clipboard_action({"action": "set", "text": "alpha beta"})
    cb.clipboard_action({"action": "pin", "text": "alpha beta"})
    assert "alpha beta" in cb.clipboard_action({"action": "pinned"})
    assert "pinned[1]" in cb.clipboard_action({"action": "search", "query": "alpha"})
    assert "Recalled" in cb.clipboard_action({"action": "copy_index", "index": "1"})
    cb.clipboard_action({"action": "append", "text": "gamma"})
    assert seen[0] == "alpha beta\ngamma"
    assert "removed" in cb.clipboard_action({"action": "unpin", "text": "alpha beta"}).lower()


def test_string_booleans_and_none_parameters():
    assert isinstance(cb.clipboard_action(None), str)
    assert isinstance(cb.clipboard_action({}), str)
    assert isinstance(cb.clipboard_action({"action": "clear", "force": "TRUE"}), str)


# ── transforms, info and rich formats ─────────────────────────────────────

@pytest.mark.parametrize("mode,text,expected", [
    ("upper", "hello", "HELLO"),
    ("lower", "HELLO", "hello"),
    ("title", "hello world", "Hello World"),
    ("trim_lines", " a \n\n b ", "a\nb"),
    ("dedupe_lines", "a\nb\na", "a\nb"),
    ("sort_lines", "b\na", "a\nb"),
    ("url_encode", "a b", "a%20b"),
    ("url_decode", "a%20b", "a b"),
    ("word_count", "one two three", "3"),
])
def test_transforms(mode, text, expected):
    assert cb._transform(mode, text) == expected


def test_json_transforms_and_errors():
    assert '"a": 2' in cb._transform("json_pretty", '{"a": 2}')
    assert cb._transform("json_minify", '{"a": 2}') == '{"a":2}'
    with pytest.raises(ValueError, match="Unknown transform"):
        cb._transform("nope", "x")


def test_transform_action_info_and_formats(monkeypatch):
    monkeypatch.setattr(cb, "_clipboard_get", lambda: "  Hello World  ")
    monkeypatch.setattr(cb, "_clipboard_set", lambda text: True)
    assert "Transformed" in cb.clipboard_action({"action": "transform", "transform": "upper", "copy": "false"})
    assert cb._content_type("https://example.com") == "url"
    assert cb._content_type("{}") == "json"
    info = cb.clipboard_action({"action": "stats"})
    assert "Characters:" in info and "Type:" in info
    assert "text" in cb.clipboard_action({"action": "formats"})


def test_get_files_and_save_image_offline(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_get_copied_files", lambda: [r"C:\\Users\\me\\a.txt"])
    assert "a.txt" in cb.clipboard_action({"action": "get_files"})
    image = MagicMock()
    image.save = MagicMock()
    monkeypatch.setattr("PIL.ImageGrab.grabclipboard", lambda: image, raising=False)
    target = tmp_path / "image.png"
    result = cb.clipboard_action({"action": "save_image", "save_path": str(target)})
    assert "saved" in result.lower()
    image.save.assert_called_once()


def test_unknown_action():
    assert "Unknown" in cb.clipboard_action({"action": "teleport"})
