"""Offline deep tests for media_control.py, notifications.py and office_builder.py."""

import base64
import json
import sys
import types
from pathlib import Path

import pytest

from actions import media_control, notifications, office_builder


# ── media control ───────────────────────────────────────────────────────────
def test_windows_sendinput_uses_full_input_union(monkeypatch):
    captured = []

    class FakeUser32:
        @staticmethod
        def SendInput(count, pointer, size):
            entry = pointer._obj
            captured.append((count, size, entry.type, entry.ki.wVk, entry.ki.dwFlags))
            return count

    monkeypatch.setattr(media_control.platform, "system", lambda: "Windows")
    monkeypatch.setattr(media_control.ctypes, "windll",
                        types.SimpleNamespace(user32=FakeUser32()), raising=False)
    assert media_control._windows_send_vk(0xB3, 2) is True
    assert len(captured) == 4
    assert [c[3] for c in captured] == [0xB3, 0xB3, 0xB3, 0xB3]
    assert captured[1][4] == 0x0002  # KEYEVENTF_KEYUP


def test_media_fallbacks_and_string_steps(monkeypatch):
    monkeypatch.setattr(media_control.platform, "system", lambda: "Linux")
    monkeypatch.setattr(media_control, "_linux_send", lambda vk, steps: False)
    pressed = []
    monkeypatch.setattr(media_control, "_keyboard_fallback",
                        lambda vk, steps: pressed.append((vk, steps)) or True)
    result = media_control.media_control_action({"action": "next", "steps": "3"})
    assert "3 time(s)" in result and pressed == [(0xB0, 3)]


def test_media_volume_set_and_status(monkeypatch):
    monkeypatch.setattr(media_control, "_set_volume", lambda pct: pct == 75)
    assert "75 percent" in media_control.media_control_action(
        {"action": "volume_set", "volume": "75"})
    monkeypatch.setattr(media_control, "_query_media_status",
                        lambda: {"status": "playing", "title": "Friday Song"})
    assert "Friday Song" in media_control.media_control_action({"action": "now_playing"})
    assert "playing" in media_control.media_control_action({"action": "status"})


def test_media_unknown_action_is_safe():
    assert "Unknown media action" in media_control.media_control_action({"action": "rm -rf"})


# ── notifications ───────────────────────────────────────────────────────────
def test_notify_dedup_force_history_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(notifications, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(notifications, "_recent", {})
    monkeypatch.setattr(notifications.platform, "system", lambda: "Linux")
    monkeypatch.setattr(notifications, "_linux_toast", lambda *a, **k: True)
    params = {"message": "Disk almost full"}
    assert "sent" in notifications.notify(params).lower()
    assert "Duplicate" in notifications.notify(params)
    assert "sent" in notifications.notify(dict(params, force=True)).lower()
    assert "Disk almost full" in notifications.notify({"action": "history"})
    assert "cleared" in notifications.notify({"action": "clear_history"}).lower()
    assert notifications.notify({"action": "history"}).startswith("No notifications")


def test_notify_url_scheme_validation():
    assert notifications._safe_url("https://example.com/a") == "https://example.com/a"
    assert notifications._safe_url("javascript:alert(1)") is None
    assert notifications._safe_url("file:///etc/passwd") is None


def test_notify_winotify_rich_params(monkeypatch, tmp_path):
    captured = {}

    class FakeNote:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def set_audio(self, sound, loop=False):
            captured["sound"] = sound

        def add_actions(self, actions):
            captured["actions"] = actions

        def set_icon(self, icon):
            captured["icon"] = icon

        def show(self):
            captured["shown"] = True

    audio = types.SimpleNamespace(Default="default", Silent="none",
                                  Alarm="alarm", Reminder="reminder")
    monkeypatch.setitem(sys.modules, "winotify",
                        types.SimpleNamespace(Notification=FakeNote, audio=audio))
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"png")
    ok = notifications._windows_toast("Title", "Message", {
        "duration": "long", "sound": "alarm", "url": "https://example.com",
        "icon": str(icon),
    })
    assert ok and captured["shown"] is True
    assert captured["duration"] == "long" and captured["sound"] == "alarm"
    assert captured["actions"][0][1] == "https://example.com"
    assert captured["icon"] == str(icon)


# ── office builder ──────────────────────────────────────────────────────────
def test_executive_presentation_with_notes_and_table(tmp_path, monkeypatch):
    monkeypatch.setattr(office_builder, "OUT_DIR", tmp_path)
    result = office_builder.create_presentation(
        title="Q4 Strategy", subtitle="Board review",
        slides=[
            {"title": "Highlights", "bullets": ["Revenue up", "Costs down"],
             "notes": "Mention the new region."},
            {"title": "Numbers", "table": {
                "headers": ["Region", "Revenue"], "rows": [["IN", "₹1,200"]]}},
        ], filename="strategy")
    assert "created" in result.lower()
    from pptx import Presentation
    prs = Presentation(tmp_path / "strategy.pptx")
    assert len(prs.slides) == 3
    assert prs.slides[0].shapes.title.text == "Q4 Strategy"
    assert "Mention the new region" in prs.slides[1].notes_slide.notes_text_frame.text
    assert any(shape.has_table for shape in prs.slides[2].shapes)


def test_spreadsheet_multisheet_formula_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(office_builder, "OUT_DIR", tmp_path)
    result = office_builder.create_spreadsheet(
        filename="forecast", sheets=[
            {"name": "Sales", "headers": ["Month", "Amount"],
             "rows": [["Jan", "1200"], ["Feb", 1500]],
             "summary": ["Total", "=SUM(B2:B3)"]},
            {"name": "Costs", "headers": ["Item", "Amount"], "rows": [["Rent", "400"]]},
        ])
    assert "created" in result.lower()
    from openpyxl import load_workbook
    wb = load_workbook(tmp_path / "forecast.xlsx")
    assert wb.sheetnames == ["Sales", "Costs"]
    ws = wb["Sales"]
    assert ws["B2"].value == 1200
    assert ws["B4"].value == "=SUM(B2:B3)"
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref == "A1:B3"


def test_sheet_names_avoid_reserved_and_invalid_characters():
    assert office_builder._sheet_name("Q4:Report/2026") == "Q4Report2026"
    assert office_builder._sheet_name("CON") == "_CON"


def test_notify_missing_optional_audio_and_silent_mode(monkeypatch):
    calls = []

    class Note:
        def __init__(self, **kwargs): pass
        def set_audio(self, *args, **kwargs): calls.append((args, kwargs))
        def show(self): pass

    monkeypatch.setitem(sys.modules, "winotify", types.SimpleNamespace(Notification=Note))
    assert notifications._windows_toast("t", "m", {"sound": "none"})
    assert calls == []
    incomplete = types.SimpleNamespace(Default="default")
    monkeypatch.setitem(sys.modules, "winotify",
                        types.SimpleNamespace(Notification=Note, audio=incomplete))
    assert notifications._windows_toast("t", "m", {"sound": "alarm"})
    assert calls == [(('default',), {"loop": False})]


def test_create_spreadsheet_accepts_summary_row(tmp_path, monkeypatch):
    monkeypatch.setattr(office_builder, "OUT_DIR", tmp_path)
    result = office_builder.create_spreadsheet(
        filename="summary-row", headers=["Name", "Value"], rows=[["Total", 5]],
        summary_row=["Grand total", 5],
    )
    assert "created" in result.lower()
    from openpyxl import load_workbook
    sheet = load_workbook(tmp_path / "summary-row.xlsx").active
    assert sheet.cell(row=3, column=1).value == "Grand total"


def test_media_status_ignores_generic_windows_titles(monkeypatch):
    monkeypatch.setattr(media_control.platform, "system", lambda: "Windows")
    monkeypatch.setattr(media_control.subprocess, "run", lambda *a, **k:
                        types.SimpleNamespace(stdout="SystemSettings|Settings"))
    assert media_control._query_media_status() == {}
    monkeypatch.setattr(media_control.subprocess, "run", lambda *a, **k:
                        types.SimpleNamespace(stdout="Spotify|Track Name"))
    assert media_control._query_media_status()["title"] == "Track Name"
