"""Deterministic coverage of cross-platform startup registration behavior."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from actions import autostart


class _Registry:
    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 2
    REG_SZ = 1
    values = {}

    class Key:
        def __enter__(self): return self
        def __exit__(self, *args): return False

    @classmethod
    def CreateKeyEx(cls, *args): return cls.Key()
    @classmethod
    def OpenKey(cls, *args):
        if not cls.values:
            raise FileNotFoundError
        return cls.Key()
    @classmethod
    def SetValueEx(cls, key, name, _reserved, _kind, value): cls.values[name] = value
    @classmethod
    def QueryValueEx(cls, key, name): return cls.values[name], cls.REG_SZ
    @classmethod
    def DeleteValue(cls, key, name):
        if name not in cls.values: raise FileNotFoundError
        del cls.values[name]


@pytest.fixture
def windows(monkeypatch, tmp_path):
    _Registry.values = {}
    monkeypatch.setattr(autostart, "platform", SimpleNamespace(system=lambda: "Windows"))
    monkeypatch.setattr(autostart, "winreg", _Registry)
    monkeypatch.setattr(autostart, "get_base_dir", lambda: tmp_path)
    interpreter = tmp_path / ".venv" / "Scripts" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    pythonw = interpreter.with_name("pythonw.exe")
    pythonw.write_text("", encoding="utf-8")
    main_file = tmp_path / "main.py"
    main_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(interpreter))
    return tmp_path


def test_registry_enable_status_disable_and_repair(windows):
    assert "Registry" in autostart.autostart_action({"action": "enable"})
    assert autostart.RUN_VALUE_NAME in _Registry.values
    command = _Registry.values[autostart.RUN_VALUE_NAME]
    assert 'pythonw.exe' in command and 'main.py' in command
    assert "valid" in autostart.autostart_action({"action": "status"})
    assert "enabled" in autostart.autostart_action({"action": "repair"}).lower()
    assert "disabled" in autostart.autostart_action({"action": "disable"}).lower()
    assert autostart.RUN_VALUE_NAME not in _Registry.values
    assert "already disabled" in autostart.autostart_action({"action": "disable"}).lower()


def test_stale_registered_path_is_reported(windows, monkeypatch):
    _Registry.values[autostart.RUN_VALUE_NAME] = '"Z:\\old\\pythonw.exe" -u "Z:\\old\\main.py"'
    status = autostart.autostart_action()
    assert "STALE/BROKEN" in status
    assert "missing" in status.lower() or "stale" in status.lower()


def test_pythonw_resolution_falls_back_to_active_interpreter(windows, monkeypatch):
    assert Path(autostart._python_executable()).name == "pythonw.exe"
    pyw = Path(sys.executable).with_name("pythonw.exe")
    pyw.unlink()
    assert Path(autostart._python_executable()) == Path(sys.executable).resolve()


def test_task_scheduler_xml_delay_and_bounds(windows):
    xml = autostart._task_xml(30)
    assert "<Delay>PT30S</Delay>" in xml
    assert f"<WorkingDirectory>{windows}</WorkingDirectory>" in xml
    assert "<Delay>PT5S</Delay>" in autostart._task_xml(0)
    assert "<Delay>PT300S</Delay>" in autostart._task_xml(999)


def test_delayed_mode_mutually_excludes_registry(windows, monkeypatch):
    calls = []
    monkeypatch.setattr(autostart, "_delete_task", lambda: calls.append("delete-task") or True)
    monkeypatch.setattr(autostart, "_set_task", lambda delay, minimized=False: calls.append(("task", delay, minimized)))
    result = autostart.autostart_action({"action": "enable", "delay_seconds": 30, "minimized": "true"})
    assert "30s" in result
    assert calls == [("task", 30, True)]
    assert _Registry.values == {}


def test_registry_mode_removes_task_and_disable_cleans_both(windows, monkeypatch):
    calls = []
    monkeypatch.setattr(autostart, "_delete_task", lambda: calls.append("delete-task") or True)
    monkeypatch.setattr(autostart, "_run_schtasks", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=""))
    autostart.autostart_action({"action": "enable", "method": "registry"})
    assert calls == ["delete-task"]
    assert autostart.RUN_VALUE_NAME in _Registry.values
    assert "disabled" in autostart.autostart_action({"action": "disable"}).lower()
    assert _Registry.values == {}


def test_task_scheduler_clamps_delay_and_builds_xml_file(windows, monkeypatch):
    observed = {}
    def run(*args, **kwargs):
        observed["args"] = args
        xml_file = Path(args[args.index("/XML") + 1])
        observed["xml"] = xml_file.read_text(encoding="utf-16")
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(autostart, "_run_schtasks", run)
    autostart._set_task(999)
    assert "/Create" in observed["args"] and "/XML" in observed["args"]
    assert "<Delay>PT300S</Delay>" in observed["xml"]


def test_non_windows_native_files_and_parameter_none(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(autostart, "get_base_dir", lambda: tmp_path)
    monkeypatch.setattr(autostart, "platform", SimpleNamespace(system=lambda: "Linux"))
    assert "disabled" in autostart.autostart_action(parameters=None).lower()
    assert "xdg_autostart" in autostart.autostart_action({"action": "enable"})
    desktop = tmp_path / ".config" / "autostart" / "friday.desktop"
    assert desktop.is_file() and "[Desktop Entry]" in desktop.read_text()
    assert "disabled" in autostart.autostart_action({"action": "disable"}).lower()
    monkeypatch.setattr(autostart, "platform", SimpleNamespace(system=lambda: "Darwin"))
    assert "launch_agent" in autostart.autostart_action({"action": "enable"})
    assert (tmp_path / "Library/LaunchAgents/com.friday.assistant.plist").is_file()


def test_string_boolean_and_invalid_parameters(windows):
    assert "Registry" in autostart.autostart_action({"action": "enable", "minimized": "false"})
    assert "integer" in autostart.autostart_action({"action": "enable", "delay_seconds": "bad"})
    assert "0 to 300" in autostart.autostart_action({"action": "enable", "delay_seconds": 301})
