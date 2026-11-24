"""Deep combinatorial tests for OS, file, shell, monitoring, and office actions."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# actions/autostart.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", ["status", "enable", "disable", "unknown"])
def test_autostart_actions_safe_on_linux(action):
    from actions.autostart import autostart_action

    res = autostart_action({"action": action})
    assert isinstance(res, str) and len(res) > 0


# ---------------------------------------------------------------------------
# actions/clipboard.py
# ---------------------------------------------------------------------------

def test_clipboard_actions_matrix(monkeypatch):
    from actions import clipboard

    store = {"text": ""}
    monkeypatch.setattr(clipboard, "_clipboard_set", lambda t: store.__setitem__("text", t) or True)
    monkeypatch.setattr(clipboard, "_clipboard_get", lambda: store["text"])

    assert "Copied" in clipboard.clipboard_action({"action": "set", "text": "hello"})
    assert "hello" in clipboard.clipboard_action({"action": "get"})
    assert "Copied" in clipboard.clipboard_action({"action": "set", "text": "world"})
    hist = clipboard.clipboard_action({"action": "history"})
    assert "world" in hist
    assert "cleared" in clipboard.clipboard_action({"action": "clear"}).lower()


# ---------------------------------------------------------------------------
# actions/cmd_control.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_cmd", [
    "rm -rf /",
    "del /f /s /q C:\\",
    "format c:",
    "echo hi & calc",
    "echo hi | whoami",
    "echo hi\nwhoami",
])
def test_cmd_control_blocks_dangerous_commands(bad_cmd):
    from actions.cmd_control import cmd_control

    res = cmd_control({"command": bad_cmd})
    assert any(w in res.lower() for w in ("block", "refus", "not allowed", "unsafe", "cannot", "invalid"))


# ---------------------------------------------------------------------------
# actions/computer_settings.py
# ---------------------------------------------------------------------------

def test_computer_settings_volume_and_brightness_zero(monkeypatch):
    from actions import computer_settings

    vol_calls = []
    bri_calls = []
    monkeypatch.setattr(computer_settings, "_pg", lambda: MagicMock())
    monkeypatch.setattr(computer_settings, "volume_set", lambda v: vol_calls.append(v))
    monkeypatch.setattr(computer_settings, "brightness_set", lambda v: bri_calls.append(v))

    res_v = computer_settings.computer_settings({"action": "volume_set", "value": 0})
    assert "0%" in res_v and vol_calls == [0]

    res_b = computer_settings.computer_settings({"action": "brightness_set", "value": 0})
    assert "0%" in res_b and bri_calls == [0]


# ---------------------------------------------------------------------------
# actions/desktop.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_url", [
    "http://127.0.0.1/img.jpg",
    "http://169.254.169.254/latest/meta-data/",
    "file:///etc/passwd",
])
def test_desktop_wallpaper_from_web_blocks_ssrf(bad_url):
    from actions.desktop import set_wallpaper_from_web

    res = set_wallpaper_from_web(bad_url)
    assert "not allowed" in res.lower() or "could not" in res.lower()


# ---------------------------------------------------------------------------
# actions/file_controller.py
# ---------------------------------------------------------------------------

def test_file_controller_crud_and_none_params(tmp_path):
    from actions.file_controller import file_controller

    # None parameters must not crash
    res_none = file_controller(None)
    assert isinstance(res_none, str)

    f = tmp_path / "demo.txt"
    assert "created" in file_controller({"action": "create_file", "path": str(f), "content": "abc"}).lower()
    assert "abc" in file_controller({"action": "read", "path": str(f)})
    file_controller({"action": "write", "path": str(f), "content": "xyz"})
    assert "xyz" in f.read_text()
    assert "demo.txt" in file_controller({"action": "list", "path": str(tmp_path)})


# ---------------------------------------------------------------------------
# actions/guardian.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_val", [-5, "nan", "inf", "not_a_num"])
def test_guardian_set_rejects_invalid_bounds(tmp_path, monkeypatch, bad_val):
    from actions import guardian

    monkeypatch.setattr(guardian, "CONFIG_FILE", tmp_path / "guardian.json")
    res = guardian.guardian_action({"action": "set", "key": "cpu_high", "value": bad_val})
    assert "must be" in res.lower()


def test_guardian_set_clamps_pct_and_cooldown(tmp_path, monkeypatch):
    from actions import guardian

    monkeypatch.setattr(guardian, "CONFIG_FILE", tmp_path / "guardian.json")
    res = guardian.guardian_action({"action": "set", "key": "cpu_high", "value": 150})
    assert "150" in res or "100" in res


# ---------------------------------------------------------------------------
# actions/monitor.py
# ---------------------------------------------------------------------------

def test_monitor_add_remove_and_check(tmp_path, monkeypatch):
    from actions import monitor

    monkeypatch.setattr(monitor, "RULES_FILE", tmp_path / "monitor.json", raising=False)
    monkeypatch.setattr(monitor, "_rules", [], raising=False)
    res_add = monitor.monitor_action({"action": "add", "kind": "cpu", "threshold": 50, "above": True})
    assert "ID" in res_add or "alert" in res_add.lower()
    res_list = monitor.monitor_action({"action": "list"})
    assert "cpu" in res_list.lower()


# ---------------------------------------------------------------------------
# actions/open_app.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_app", ["calc & whoami", "notepad\nwhoami", "cmd\rcalc", "app;ls"])
def test_open_app_rejects_metacharacters_and_newlines(bad_app):
    from actions.open_app import _launch_windows

    if sys.platform == "win32":
        res = _launch_windows(bad_app)
        assert "invalid" in res.lower() or "not" in res.lower()


# ---------------------------------------------------------------------------
# actions/office_builder.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected_safe", [
    ("CON", "_CON"),
    ("NUL", "_NUL"),
    ("COM1", "_COM1"),
    ("   ", "document"),
    ("Q4: Report/2026?", "Q4 Report2026"),
])
def test_office_builder_sanitize_reserved_names(raw, expected_safe):
    from actions.office_builder import _sanitize

    assert _sanitize(raw) == expected_safe


# ---------------------------------------------------------------------------
# actions/system_tray.py
# ---------------------------------------------------------------------------

def test_system_tray_stop_resets_hotkeys(monkeypatch):
    from actions import system_tray

    system_tray._hotkeys_registered = True
    monkeypatch.setattr(system_tray, "_KEYBOARD", True)
    kb_mock = MagicMock()
    monkeypatch.setattr(system_tray, "keyboard", kb_mock, raising=False)
    system_tray.stop()
    assert system_tray._hotkeys_registered is False
