"""Offline deep tests for daily_briefing / desktop upgrades."""

import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from actions import daily_briefing as db
from actions import desktop


# ──────────────────────────── daily_briefing ─────────────────────────────

@pytest.mark.parametrize("hour,expected", [
    (8, "Good morning"), (14, "Good afternoon"), (21, "Good evening"),
])
def test_greeting_is_time_aware(hour, expected):
    assert db._greeting(datetime(2026, 11, 24, hour)) == expected


def test_section_filter_accepts_lists_and_strings():
    assert db._parse_sections("weather, calendar") == ["weather", "calendar"]
    assert db._parse_sections(["SYSTEM", "memory"]) == ["system", "memory"]
    assert db._parse_sections("none") == []
    assert "news" not in db._parse_sections(None)
    assert "news" in db._parse_sections("all")


def test_collect_sections_is_bounded_by_timeout(monkeypatch):
    monkeypatch.setitem(db._SECTION_FUNCS, "fast", lambda ctx: "quick")
    monkeypatch.setitem(db._SECTION_FUNCS, "slow", lambda ctx: time.sleep(0.3) or "late")
    result = db._collect_sections(["fast", "slow"], "Guwahati", timeout=0.05)
    assert result == {"fast": "quick", "slow": ""}


def test_briefing_uses_saved_name_and_detailed_digest(monkeypatch):
    monkeypatch.setattr(db, "_memory_identity", lambda: {"name": "Aman", "city": "Guwahati"})
    monkeypatch.setitem(db._SECTION_FUNCS, "weather", lambda ctx: f"Weather in {ctx['city']} is warm.")
    monkeypatch.setitem(db._SECTION_FUNCS, "system", lambda ctx: "CPU 10%.")
    monkeypatch.setattr(db, "_get_time", lambda: "Tuesday, November 24 2026 — 08:00 AM")
    monkeypatch.setattr(db, "_greeting", lambda now: "Good morning")

    spoken = db.daily_briefing({"sections": ["weather", "system"]})
    assert spoken.startswith("Good morning, Aman.")
    assert "Weather in Guwahati" in spoken and "CPU 10%" in spoken
    assert "\n" not in spoken

    detailed = db.daily_briefing({"sections": ["weather"], "format": "detailed"})
    assert detailed.startswith("Good morning, Aman.")
    assert "\n\n" in detailed


def test_briefing_trims_spoken_at_sentence_boundary():
    text = "One sentence. " * 200
    trimmed = db._trim_spoken(text, 100)
    assert len(trimmed) <= 100
    assert trimmed.endswith(".")


def test_briefing_memory_excludes_raw_conversation_notes(monkeypatch):
    from memory import memory_manager

    fake = {
        "identity": {"name": {"value": "Aman"}},
        "preferences": {
            "conversation_2026_11_24": {"value": "SECRET TRANSCRIPT"},
            "favorite_language": {"value": "Hindi"},
        },
        "notes": {"conversation_1": {"value": "SECRET NOTES"}},
    }
    monkeypatch.setattr(memory_manager, "load_memory", lambda: fake)
    result = db._get_memory()
    assert "Hindi" in result
    assert "SECRET" not in result
    assert "conversation" not in result.lower()


def test_news_requires_opt_in(monkeypatch):
    called = []
    monkeypatch.setitem(db._SECTION_FUNCS, "news", lambda ctx: called.append(1) or "Headlines: A, B.")
    monkeypatch.setitem(db._SECTION_FUNCS, "system", lambda ctx: "CPU fine.")
    monkeypatch.setattr(db, "_get_time", lambda: "now")
    db.daily_briefing({"sections": ["system"]})
    assert called == []
    assert "Headlines" in db.daily_briefing({"sections": ["system"], "news": "true"})


# ─────────────────────────────── desktop ─────────────────────────────────

def test_get_desktop_uses_onedrive_redirect(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")

    class FakeReg:
        HKEY_CURRENT_USER = object()

        @staticmethod
        def OpenKey(root, path):
            assert "User Shell Folders" in path
            return object()

        @staticmethod
        def QueryValueEx(key, name):
            assert name == "Desktop"
            return (str(tmp_path / "OneDrive" / "Desktop"), 1)

    fake_winreg = ModuleType("winreg")
    fake_winreg.HKEY_CURRENT_USER = FakeReg.HKEY_CURRENT_USER
    fake_winreg.OpenKey = FakeReg.OpenKey
    fake_winreg.QueryValueEx = FakeReg.QueryValueEx
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    (tmp_path / "OneDrive" / "Desktop").mkdir(parents=True)
    assert desktop._get_desktop() == tmp_path / "OneDrive" / "Desktop"


def test_wallpaper_conversion_history_and_undo(monkeypatch, tmp_path):
    from PIL import Image

    # Force the Linux gsettings branch: this test must never touch a real
    # Windows wallpaper or registry, even when run on Windows.
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop, "BASE_DIR", tmp_path)
    (tmp_path / "old.png").write_bytes(b"old")
    monkeypatch.setattr(desktop, "get_current_wallpaper", lambda: f"Current wallpaper: {tmp_path / 'old.png'}")
    image_path = tmp_path / "photo.webp"
    Image.new("RGB", (8, 8), "blue").save(image_path, format="WEBP")
    commands = []
    monkeypatch.setattr(desktop.subprocess, "run", lambda args, **kw: commands.append(args))

    result = desktop.set_wallpaper(str(image_path))
    assert "Wallpaper set" in result
    assert any("picture-uri" in " ".join(args) for args in commands)
    history = json.loads((tmp_path / "data" / "wallpapers" / "history.json").read_text())
    assert history["entries"][-1]["path"] == str(tmp_path / "old.png")

    restored = []
    monkeypatch.setattr(desktop, "set_wallpaper", lambda path: restored.append(path) or "restored")
    assert "restored" == desktop.undo_wallpaper()
    assert restored == [str(tmp_path / "old.png")]


def test_wallpaper_windows_registry_types_conversion_and_undo(monkeypatch, tmp_path):
    """Fully mocked Windows path: no real registry, wallpaper or shell writes."""
    from types import ModuleType, SimpleNamespace
    from PIL import Image

    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setattr(desktop, "BASE_DIR", tmp_path)
    (tmp_path / "old.png").write_bytes(b"old")
    monkeypatch.setattr(
        desktop, "get_current_wallpaper",
        lambda: f"Current wallpaper: {tmp_path / 'old.png'}")

    image_path = tmp_path / "photo.webp"
    Image.new("RGB", (8, 8), "blue").save(image_path, format="WEBP")

    def _no_subprocess(*args, **kwargs):
        raise AssertionError("Windows wallpaper test must not spawn subprocesses")

    monkeypatch.setattr(desktop.subprocess, "run", _no_subprocess)

    registry_writes = []
    fake_winreg = ModuleType("winreg")
    fake_winreg.HKEY_CURRENT_USER = object()
    fake_winreg.KEY_SET_VALUE = 2
    fake_winreg.REG_SZ = 1

    def _open_key(root, subkey, reserved=0, access=0):
        assert root is fake_winreg.HKEY_CURRENT_USER
        assert subkey == r"Control Panel\Desktop"
        return object()

    def _set_value(key, name, reserved, value_type, value):
        assert value_type == fake_winreg.REG_SZ
        assert isinstance(value, str), f"{name} must be str, got {type(value)}"
        registry_writes.append((name, value))

    fake_winreg.OpenKey = _open_key
    fake_winreg.SetValueEx = _set_value
    fake_winreg.CloseKey = lambda key: None
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    spi_calls = []
    fake_user32 = SimpleNamespace(
        SystemParametersInfoW=lambda *args: spi_calls.append(args) or 1)
    monkeypatch.setattr(
        desktop.ctypes, "windll",
        SimpleNamespace(user32=fake_user32), raising=False)

    result = desktop.set_wallpaper(str(image_path))
    assert "Wallpaper set" in result
    assert ("WallpaperStyle", "10") in registry_writes
    assert ("TileWallpaper", "0") in registry_writes
    assert len(spi_calls) == 1
    applied = Path(spi_calls[0][2])
    assert applied.suffix.lower() == ".png" and applied.exists()
    assert applied == tmp_path / "data" / "wallpapers" / "photo.png"

    history = json.loads((tmp_path / "data" / "wallpapers" / "history.json").read_text())
    assert history["entries"][-1]["path"] == str(tmp_path / "old.png")

    restored = []
    monkeypatch.setattr(
        desktop, "set_wallpaper",
        lambda path: restored.append(path) or "restored")
    assert desktop.undo_wallpaper() == "restored"
    assert restored == [str(tmp_path / "old.png")]


def _fake_image_response(data: bytes, url="https://example.com/photo.jpg"):
    response = MagicMock()
    response.status_code = 200
    response.url = url
    response.headers = {"Content-Length": str(len(data))}
    response.content = data
    response.iter_content.return_value = [data]
    return response


def test_wallpaper_url_streaming_verify_size_and_ssrf(monkeypatch, tmp_path):
    from PIL import Image

    monkeypatch.setattr(desktop, "BASE_DIR", tmp_path)
    monkeypatch.setattr("actions.document_qa._is_private_target", lambda url: False)
    saved = []
    monkeypatch.setattr(desktop, "set_wallpaper", lambda path: saved.append(path) or "set")
    buffer = io.BytesIO()
    Image.new("RGB", (6, 6), "green").save(buffer, format="PNG")
    data = buffer.getvalue()
    monkeypatch.setattr("requests.get", lambda *a, **kw: _fake_image_response(data))

    assert "set" == desktop.set_wallpaper_from_web("https://example.com/photo.png")
    assert Path(saved[0]).read_bytes() == data

    huge = MagicMock()
    huge.headers = {"Content-Length": str(30 * 1024 * 1024)}
    monkeypatch.setattr("requests.get", lambda *a, **kw: huge)
    assert "too large" in desktop.set_wallpaper_from_web("https://example.com/huge.png").lower()

    monkeypatch.setattr("actions.document_qa._is_private_target", lambda url: True)
    assert "not allowed" in desktop.set_wallpaper_from_web("http://127.0.0.1/x.png").lower()


def test_organize_clean_dry_run_and_undo(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "_get_desktop", lambda: tmp_path)
    monkeypatch.setattr(desktop, "BASE_DIR", tmp_path)
    (tmp_path / "report.pdf").write_text("pdf")
    (tmp_path / "song.mp3").write_text("mp3")

    assert "Dry run" in desktop.organize_desktop(dry_run=True)
    assert (tmp_path / "report.pdf").exists()
    result = desktop.organize_desktop()
    assert "2 files moved" in result
    assert (tmp_path / "Documents" / "report.pdf").exists()
    assert "Undid" in desktop.undo_desktop_action()
    assert (tmp_path / "report.pdf").exists()

    assert "Dry run" in desktop.clean_desktop(dry_run=True)
    desktop.clean_desktop()
    assert any(p.name.startswith("Desktop Archive") for p in tmp_path.iterdir())


def test_list_and_stats_with_sort_limit_and_file_breakdown(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "_get_desktop", lambda: tmp_path)
    for name in ("b.txt", "a.png", "c.pdf"):
        (tmp_path / name).write_text("x" * 10)
    listing = desktop.list_desktop(sort_by="name", limit=2)
    assert "2 items" in listing and "a.png" in listing
    stats = desktop.get_desktop_stats()
    assert "Files   : 3" in stats
    assert "Images: 1" in stats and "Documents: 2" in stats


def test_desktop_control_new_actions_are_patchable(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "_get_desktop", lambda: tmp_path)
    monkeypatch.setattr(desktop, "BASE_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(desktop, "undo_wallpaper", lambda: calls.append("undo_wp") or "wp undone")
    monkeypatch.setattr(desktop, "list_desktop", lambda sort_by, limit: f"listed {sort_by} {limit}")
    assert desktop.desktop_control({"action": "undo_wallpaper"}) == "wp undone"
    assert "listed size 5" == desktop.desktop_control(
        {"action": "list", "sort_by": "size", "limit": "5"})
    assert "Dry run" in desktop.desktop_control(
        {"action": "organize", "dry_run": "true"})


def test_desktop_cleanup_and_organize_preserve_windows_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "_get_desktop", lambda: tmp_path)
    for name in ("Desktop.INI", "THUMBS.DB", ".DS_STORE", "report.pdf"):
        (tmp_path / name).write_text("metadata" if name != "report.pdf" else "data")
    plan, _ = desktop._organize_plan(tmp_path, "by_type")
    assert [src.name for src, _ in plan] == ["report.pdf"]
    desktop.clean_desktop()
    assert (tmp_path / "Desktop.INI").exists()
    assert (tmp_path / "THUMBS.DB").exists()
    assert (tmp_path / ".DS_STORE").exists()


def test_briefing_omits_empty_reminders_and_tasks(monkeypatch):
    monkeypatch.setattr(db, '_memory_identity', lambda: {})
    monkeypatch.setitem(db._SECTION_FUNCS, 'reminders', lambda _: 'You have no pending reminders, sir.')
    monkeypatch.setitem(db._SECTION_FUNCS, 'tasks', lambda _: 'Summary — tasks: 0 total. 0 pending. top processes: x.')
    result = db.daily_briefing({'sections': ['reminders', 'tasks']})
    assert 'Reminders:' not in result and 'Tasks:' not in result
    monkeypatch.setitem(db._SECTION_FUNCS, 'reminders', lambda _: 'One reminder: call home.')
    monkeypatch.setitem(db._SECTION_FUNCS, 'tasks', lambda _: 'Summary — tasks: 1 total. 1 pending.')
    result = db.daily_briefing({'sections': ['reminders', 'tasks']})
    assert 'Reminders:' in result and 'Tasks:' in result
