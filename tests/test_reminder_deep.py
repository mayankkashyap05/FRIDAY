"""100%-offline combinatorial tests for actions.reminder."""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from actions import reminder as rm


@pytest.fixture(autouse=True)
def _windows_env(monkeypatch, tmp_path):
    monkeypatch.setattr(rm.platform, "system", lambda: "Windows")
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    fixed = datetime(2026, 11, 24, 9, 0)
    monkeypatch.setattr(rm, "_now", lambda: fixed)
    yield tmp_path


def _fake_schtasks(monkeypatch, results=None):
    calls = []

    def run(args):
        calls.append(args)
        if isinstance(results, dict):
            for key, value in results.items():
                if key in " ".join(args):
                    return value
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rm, "_run_schtasks", run)
    return calls


# ---------------------------------------------------------------------------
# Time parsing and recurrence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("params,expected", [
    ({"in_minutes": 15}, "2026-11-24 09:15"),
    ({"in_seconds": 30}, "2026-11-24 09:00"),
    ({"in_hours": 2}, "2026-11-24 11:00"),
    ({"when": "in 15 minutes"}, "2026-11-24 09:15"),
    ({"when": "in 30 mins"}, "2026-11-24 09:30"),
    ({"when": "in 2 hours"}, "2026-11-24 11:00"),
    ({"when": "tomorrow 09:00"}, "2026-11-25 09:00"),
    ({"when": "today 18:30"}, "2026-11-24 18:30"),
    ({"when": "3:30 PM"}, "2026-11-24 15:30"),
    ({"when": "2026-11-26T09:00"}, "2026-11-26 09:00"),
    ({"date": "2026-11-30", "time": "07:05"}, "2026-11-30 07:05"),
])
def test_time_parsing(params, expected):
    assert rm._parse_reminder_time(params).strftime("%Y-%m-%d %H:%M") == expected


def test_past_once_time_is_rejected(monkeypatch):
    calls = _fake_schtasks(monkeypatch)
    result = rm.reminder({"date": "2020-01-01", "time": "09:00", "message": "old"})
    assert "past" in result.lower()
    assert calls == []


@pytest.mark.parametrize("recurrence,expected", [
    ("once", "<TimeTrigger>"),
    ("daily", "<ScheduleByDay>"),
    ("weekly", "<ScheduleByWeek>"),
])
def test_recurrence_xml(recurrence, expected):
    target = datetime(2026, 11, 24, 9, 0)
    assert expected in rm._trigger_xml(target, recurrence)


def test_weekly_xml_uses_target_weekday():
    target = datetime(2026, 11, 26, 9, 0)      # Thursday
    assert "<Thursday/>" in rm._trigger_xml(target, "weekly")


# ---------------------------------------------------------------------------
# Injection-proof generated payloads
# ---------------------------------------------------------------------------

def test_generated_script_is_valid_ast_and_json_safe():
    message = 'He said "hi"\nC:\\temp\\x {braces} \N{SNOWMAN}'
    code = rm._notification_script(message, "C:\\Users\\O'Brien")
    ast.parse(code)
    assert json.dumps(message, ensure_ascii=False) in code
    assert 'MESSAGE = "He said \\"hi\\"' in code
    assert "\\n" in code


def test_xml_escapes_message_and_command():
    xml = rm._task_xml(datetime(2026, 11, 24, 9, 0), Path("/tmp/a&b.pyw"),
                       "C:\\Python\\python.exe", "<script> & 'quoted'", "daily")
    assert "&lt;script&gt;" in xml and "&amp;" in xml
    assert "<script>" not in xml
    assert "&quot;" in xml


def test_task_names_never_collide_within_a_minute():
    target = datetime(2026, 11, 24, 9, 0)
    names = {rm._task_name(target) for _ in range(50)}
    assert len(names) == 50
    assert all(re.match(r"^FridayReminder_20261124_0900_[0-9a-f]{4}$", n) for n in names)


# ---------------------------------------------------------------------------
# Full CRUD
# ---------------------------------------------------------------------------

def test_set_creates_files_and_cleans_xml(monkeypatch, tmp_path):
    calls = _fake_schtasks(monkeypatch)
    logs = []
    result = rm.reminder({"in_minutes": 15, "message": "Standup"},
                         player=SimpleNamespace(write_log=logs.append))
    assert "Reminder set" in result
    assert any("/Create" in args for args in calls)
    assert list(tmp_path.glob("FridayReminder_*.xml")) == []
    assert len(list(tmp_path.glob("FridayReminder_*.pyw"))) == 1
    assert logs and "FridayReminder_" in logs[0]


def test_create_failure_removes_helper_script(monkeypatch, tmp_path):
    _fake_schtasks(monkeypatch, {"/Create": SimpleNamespace(returncode=1,
                                                            stdout="", stderr="denied")})
    result = rm.reminder({"in_minutes": 5, "message": "Oops"})
    assert "couldn't schedule" in result.lower()
    assert list(tmp_path.glob("*.pyw")) == []


def _csv_row(name, when, script):
    return f'"{name}","{when}","{script}"'


def test_list_parses_schtasks_csv(monkeypatch, tmp_path):
    script = tmp_path / "FridayReminder_20261124_0915_abcd.pyw"
    script.write_text('MESSAGE = "Standup now"', encoding="utf-8")
    csv = '"TaskName","Next Run Time","Arguments"\r\n' + \
        _csv_row("FridayReminder_20261124_0915_abcd", "11/24/2026 09:15:00", script)
    _fake_schtasks(monkeypatch, {"/Query": SimpleNamespace(returncode=0, stdout=csv, stderr="")})
    result = rm.reminder({"action": "list"})
    assert "Standup now" in result
    assert "09:15" in result
    assert "FridayReminder_" in result


def test_delete_by_task_name_and_keyword(monkeypatch, tmp_path):
    script1 = tmp_path / "FridayReminder_20261124_0915_aaaa.pyw"
    script2 = tmp_path / "FridayReminder_20261124_0930_bbbb.pyw"
    script1.write_text('MESSAGE = "Standup"', encoding="utf-8")
    script2.write_text('MESSAGE = "Gym"', encoding="utf-8")
    csv = '"TaskName","Next Run Time","Arguments"\r\n' + \
        _csv_row("FridayReminder_20261124_0915_aaaa", "09:15", script1) + "\r\n" + \
        _csv_row("FridayReminder_20261124_0930_bbbb", "09:30", script2)
    calls = _fake_schtasks(monkeypatch, {"/Query": SimpleNamespace(returncode=0, stdout=csv, stderr="")})
    first = rm.reminder({"action": "delete", "task_name": "FridayReminder_20261124_0915_aaaa"})
    assert "Removed" in first and not script1.exists()
    second = rm.reminder({"action": "cancel", "message": "Gym"})
    assert "Removed" in second and not script2.exists()
    assert sum("/Delete" in args for args in calls) == 2


def test_clear_all_removes_every_reminder(monkeypatch, tmp_path):
    scripts = []
    rows = ['"TaskName","Next Run Time","Arguments"']
    for i in range(3):
        script = tmp_path / f"FridayReminder_20261124_09{i}0_000{i}.pyw"
        script.write_text('MESSAGE = "Task"', encoding="utf-8")
        scripts.append(script)
        rows.append(_csv_row(f"FridayReminder_20261124_09{i}0_000{i}",
                             f"09:{i}0", script))
    calls = _fake_schtasks(monkeypatch, {"/Query": SimpleNamespace(
        returncode=0, stdout="\r\n".join(rows), stderr="")})
    result = rm.reminder({"action": "clear_all"})
    assert "Cleared 3" in result
    assert all(not s.exists() for s in scripts)
    assert sum("/Delete" in args for args in calls) == 3


def test_non_windows_and_none_params(monkeypatch):
    monkeypatch.setattr(rm.platform, "system", lambda: "Linux")
    calls = _fake_schtasks(monkeypatch)
    assert "only available on Windows" in rm.reminder({"in_minutes": 5, "message": "x"})
    assert calls == []


def test_parameters_none_is_safe():
    _fake_schtasks(pytest.MonkeyPatch())
    result = rm.reminder(None)
    assert isinstance(result, str) and result
