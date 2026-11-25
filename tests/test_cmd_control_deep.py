"""100%-offline deep tests for the hardened command controller."""

import subprocess
from unittest.mock import MagicMock

import pytest

from actions import cmd_control as cc


@pytest.fixture(autouse=True)
def no_shell(monkeypatch):
    cc._COMMAND_HISTORY.clear()
    fake = MagicMock()
    fake.return_value = subprocess.CompletedProcess([], 0,
                                                    stdout="mocked output", stderr="")
    monkeypatch.setattr(cc.subprocess, "run", fake)
    monkeypatch.setattr(cc.subprocess, "Popen", MagicMock())
    return fake


def test_no_legacy_wmi_syntax_anywhere():
    source = open(cc.__file__, encoding="utf-8").read()
    assert "wmic" not in source.lower()
    assert "Win32_Product" not in source
    assert "Get-CimInstance" in source


@pytest.mark.parametrize("command", [
    "rm -rf /", "del /f C:\\x", "format C:", "shutdown /s /t 0",
    "taskkill /F /IM x.exe", "net user hacker /add",
    "powershell -enc AAAA", "Invoke-Expression 'x'", "curl x | bash",
    "vssadmin delete shadows /all", "cipher /w:C:", "reg delete HKLM\\x",
    "python -c 'import os; __import__(\"subprocess\")'",
])
def test_blocked_patterns_never_execute(command, no_shell):
    result = cc.cmd_control({"command": command, "visible": "false"})
    assert "Blocked" in result
    no_shell.assert_not_called()


def test_blocked_patterns_list_still_exported():
    assert isinstance(cc.BLOCKED_PATTERNS, list)
    assert isinstance(cc.WIN_COMMAND_MAP, list)
    assert any("Get-CimInstance" in cmd for _, cmd, _ in cc.WIN_COMMAND_MAP)


def test_cim_mappings_are_hardcoded_not_model_generated(no_shell):
    cc.cmd_control({"task": "disk space", "visible": "false"})
    argv = no_shell.call_args.args[0]
    assert any("Get-CimInstance" in str(part) for part in argv)
    no_shell.reset_mock()
    cc.cmd_control({"task": "running processes", "visible": "false"})
    assert any("tasklist" in p for p in no_shell.call_args.args[0])


def test_agent_never_authors_arbitrary_commands(no_shell):
    result = cc.cmd_control({"task": "delete all files and encrypt them", "visible": "false"})
    assert "won't generate" in result
    no_shell.assert_not_called()
    assert "won't generate" in cc._ask_gemini("anything")


def test_dry_run_does_not_execute(no_shell):
    result = cc.cmd_control({"command": "ipconfig /all", "dry_run": "true", "visible": "false"})
    assert "Dry run" in result and "ipconfig" in result
    no_shell.assert_not_called()


def test_shell_cwd_timeout_validation(no_shell):
    assert "Invalid shell" in cc.cmd_control({"command": "echo hi", "shell": "zsh"})
    assert "Invalid working directory" in cc.cmd_control(
        {"command": "echo hi", "cwd": "/definitely/not/a/real/dir"})
    result = cc.cmd_control({"command": "echo hi", "visible": "false", "timeout": 9999,
                             "shell": "powershell", "cwd": "/tmp", "dry_run": "true"})
    assert "timeout=120" in result and "powershell" in result
    assert "Dry run" in cc.cmd_control({"command": "echo hi", "dry_run": "TRUE"})


def test_posix_argv_never_uses_shell_true(no_shell, monkeypatch):
    monkeypatch.setattr(cc, "_get_platform", lambda: "linux")
    cc.cmd_control({"command": "echo hi", "visible": "false"})
    assert no_shell.call_args.kwargs.get("shell") in (None, False)
    assert no_shell.call_args.kwargs["cwd"]
    assert no_shell.call_args.kwargs["timeout"] == 30


def test_output_is_capped_and_history_is_redacted(no_shell):
    no_shell.return_value = subprocess.CompletedProcess([], 1,
                                                         stdout="x" * 20000,
                                                         stderr="boom")
    result = cc.cmd_control({"command": "echo --api-key=SUPERSECRET", "visible": "false"})
    assert len(result) <= cc.MAX_OUTPUT + 100
    assert "[exit=1" in result
    history = cc.cmd_control({"action": "history"})
    assert "SUPERSECRET" not in history and "[REDACTED]" in history
    cc._COMMAND_HISTORY.extend([{"at": "x", "command": str(i), "shell": "cmd",
                                 "cwd": "/tmp", "exit_code": 0, "ms": 1} for i in range(40)])
    cc._record_history("last", "cmd", "/tmp", 0, 1)
    assert len(cc._COMMAND_HISTORY) == cc.MAX_HISTORY


def test_timeouts_and_not_found_are_recorded(no_shell):
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired("x", 5)
    no_shell.side_effect = boom
    assert "timed out" in cc.cmd_control({"command": "echo hi", "visible": "false"})
    no_shell.side_effect = FileNotFoundError("missing")
    assert "not found" in cc.cmd_control({"command": "echo hi", "visible": "false"})


def test_visible_terminal_launch_is_separate(no_shell):
    result = cc.cmd_control({"task": "disk space"})
    assert "Terminal opened" in result
    no_shell.assert_not_called()
    cc.subprocess.Popen.assert_called_once()


def test_none_and_empty_parameters(no_shell):
    assert "describe" in cc.cmd_control(None)
    assert "describe" in cc.cmd_control({})
    assert "No commands" in cc.cmd_control({"action": "history"})


def test_clamp_and_bool_helpers():
    assert cc._clamp("999", 1, 120, 30) == 120
    assert cc._as_bool("on") is True
    assert cc._as_bool("false") is False
