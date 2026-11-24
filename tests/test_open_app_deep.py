"""100%-offline combinatorial tests for actions.open_app."""

from __future__ import annotations

import json
import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from actions import open_app as oa


@pytest.fixture(autouse=True)
def _clean_index(monkeypatch):
    with oa._INDEX_LOCK:
        oa._APP_INDEX_CACHE = None
    monkeypatch.setattr(oa.platform, "system", lambda: "Windows")
    monkeypatch.setattr(oa.shutil, "which", lambda name, *a, **k: None)
    yield
    with oa._INDEX_LOCK:
        oa._APP_INDEX_CACHE = None


def _patch_sources(monkeypatch, start=None, app_paths=None, uninstall=None, uwp=None):
    calls = {"start": 0, "app_paths": 0, "uninstall": 0, "uwp": 0}

    def start_fn():
        calls["start"] += 1
        return start or []

    def app_fn():
        calls["app_paths"] += 1
        return app_paths or []

    def un_fn():
        calls["uninstall"] += 1
        return uninstall or []

    def uwp_fn():
        calls["uwp"] += 1
        return uwp or []

    monkeypatch.setattr(oa, "_start_menu_shortcuts", start_fn)
    monkeypatch.setattr(oa, "_app_paths_registry", app_fn)
    monkeypatch.setattr(oa, "_registry_app_paths", un_fn)
    monkeypatch.setattr(oa, "_uwp_apps", uwp_fn)
    return calls


# ---------------------------------------------------------------------------
# Multi-source index, TTL and refresh
# ---------------------------------------------------------------------------

def test_index_merges_all_four_sources_and_deduplicates(monkeypatch):
    _patch_sources(
        monkeypatch,
        start=[oa.Path("/start/Chrome.lnk")],
        app_paths=[("chrome", "C:/Program Files/Chrome/chrome.exe")],
        uninstall=[("Google Chrome", "C:/Program Files/Chrome")],
        uwp=[("WhatsApp", "9NNE93V5CM4T")],
    )
    entries = oa._build_windows_app_index(refresh=True)
    assert {e["source"] for e in entries} == {"start_menu", "app_paths", "uninstall", "uwp"}
    assert any(e.get("aumid") == "9NNE93V5CM4T" for e in entries)


def test_index_uses_ttl_cache_and_refresh_bypasses_it(monkeypatch):
    calls = _patch_sources(monkeypatch, start=[oa.Path("/start/Chrome.lnk")])
    first = oa._build_windows_app_index(refresh=True)
    second = oa._build_windows_app_index()
    assert first == second
    assert calls == {"start": 1, "app_paths": 1, "uninstall": 1, "uwp": 1}
    oa._build_windows_app_index(refresh=True)
    assert calls["start"] == 2
    with oa._INDEX_LOCK:
        now, _ = oa._APP_INDEX_CACHE
    oa._APP_INDEX_CACHE = (now - oa._INDEX_TTL_S - 1, [])
    oa._build_windows_app_index()
    assert calls["start"] == 3


def test_index_is_empty_off_windows(monkeypatch):
    monkeypatch.setattr(oa.platform, "system", lambda: "Linux")
    assert oa._build_windows_app_index(refresh=True) == []


def test_start_menu_shortcuts_uses_both_program_folders(tmp_path, monkeypatch):
    user = tmp_path / "roaming"
    system = tmp_path / "programdata"
    for base in (user, system):
        folder = base / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        folder.mkdir(parents=True)
        (folder / "App.lnk").write_text("x", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(user))
    monkeypatch.setenv("PROGRAMDATA", str(system))
    assert len(oa._start_menu_shortcuts()) == 2


def test_app_paths_and_uninstall_registry(monkeypatch):
    import sys
    import types

    fake_winreg = types.ModuleType("winreg")
    fake_winreg.HKEY_LOCAL_MACHINE = "HKLM"
    fake_winreg.HKEY_CURRENT_USER = "HKCU"
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    rows = {
        ("HKLM", "app"): [{"(Default)": '"C:\\Program Files\\chrome.exe"'}],
        ("HKLM", "un"): [{"DisplayName": "Google Chrome",
                          "InstallLocation": "C:\\Program Files\\Chrome"}],
    }
    monkeypatch.setattr(oa, "_registry_key_values", lambda hive, path: rows.get(
        (hive, "app" if "App Paths" in path else "un"), []))
    assert oa._app_paths_registry() == [("chrome", "C:\\Program Files\\chrome.exe")]
    assert ("Google Chrome", "C:\\Program Files\\Chrome") in oa._registry_app_paths()


def test_uwp_apps_reads_get_start_apps_json(monkeypatch):
    monkeypatch.setattr(oa.platform, "system", lambda: "Windows")
    monkeypatch.setattr(oa.shutil, "which", lambda _name: "powershell")
    result = SimpleNamespace(returncode=0, stdout=json.dumps([
        {"Name": "WhatsApp", "AppID": "9NNE93V5CM4T"}]), stderr="")
    monkeypatch.setattr(oa.subprocess, "run", lambda *a, **k: result)
    assert oa._uwp_apps() == [("WhatsApp", "9NNE93V5CM4T")]


# ---------------------------------------------------------------------------
# Ranking, launching, actions
# ---------------------------------------------------------------------------

def test_ranking_exact_prefix_substring_fuzzy(monkeypatch):
    entries = [
        {"name": "Chrome"},
        {"name": "Chrome Canary"},
        {"name": "Google Chrome"},
        {"name": "Chromium Browser"},
        {"name": "Unrelated Tool"},
    ]
    ordered = oa._rank_candidates("chrome", entries)
    assert ordered[0]["name"] == "Chrome"
    assert ordered[1]["name"] == "Chrome Canary"
    assert ordered[-1]["name"] == "Unrelated Tool"


def test_uwp_launch_uses_shell_apps_folder(monkeypatch):
    opened = []
    monkeypatch.setattr(oa.os, "startfile", opened.append, raising=False)
    assert oa._launch_windows("WhatsApp", aumid="9NNE93V5CM4T") is True
    assert opened == ["shell:AppsFolder\\9NNE93V5CM4T"]


def test_start_menu_and_explicit_exe_launch(monkeypatch, tmp_path):
    opened, spawned = [], []
    link = tmp_path / "Notepad.lnk"
    monkeypatch.setattr(oa, "_start_menu_shortcuts", lambda: [link])
    monkeypatch.setattr(oa.os, "startfile", lambda p: opened.append(p), raising=False)
    monkeypatch.setattr(oa.subprocess, "Popen", lambda *a, **k: spawned.append(a[0]))
    assert oa._launch_windows("Notepad") is True
    assert opened == [str(link)]
    monkeypatch.setattr(oa, "_start_menu_shortcuts", lambda: [])
    monkeypatch.setattr(oa.shutil, "which", lambda name, *a, **k: "/usr/bin/code")
    assert oa._launch_windows("code", args=["file.py"]) is True
    assert spawned[-1] == ["/usr/bin/code", "file.py"]


@pytest.mark.parametrize("bad", [
    "calc & whoami", "notepad\nwhoami", "cmd\rcalc", "app;ls", "a|b", "a>b",
    "a<b", "a^b", "a(b)", "a%b", "a!b",
])
def test_metacharacters_rejected_by_launch_windows(bad, monkeypatch):
    calls = []
    monkeypatch.setattr(oa.subprocess, "Popen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(oa.os, "startfile", lambda _p: calls.append("startfile"), raising=False)
    assert oa._launch_windows(bad) is False
    assert calls == []


def test_unsafe_args_rejected(monkeypatch):
    assert oa._safe_args(["ok", "a;b"]) is None
    assert oa._safe_args("plain") == ["plain"]


def test_focus_and_status_actions(monkeypatch):
    monkeypatch.setattr(oa, "_focus_window", lambda name: name == "Chrome")
    assert "front" in oa.open_app({"app_name": "Chrome", "action": "focus"})
    monkeypatch.setattr(oa, "_is_running", lambda name: True)
    assert "running" in oa.open_app({"app_name": "Chrome", "action": "is_running"})
    monkeypatch.setattr(oa, "_build_windows_app_index", lambda **k: [
        {"name": "Chrome"}, {"name": "Spotify"}])
    result = oa.open_app({"action": "list", "query": "Chrome"})
    assert "Chrome" in result and "Spotify" in result


def test_url_scheme_allowlist(monkeypatch):
    opened = []
    monkeypatch.setattr(oa.webbrowser, "open", lambda url: opened.append(url) or True)
    assert "successfully" in oa.open_app({"app_name": "https://example.com/a"})
    assert "successfully" in oa.open_app({"app_name": "ms-settings:display"})
    assert "safe" in oa.open_app({"app_name": "javascript:alert(1)"}).lower()
    assert "scheme" in oa.open_app({"app_name": "file:///etc/passwd"}).lower()
    assert opened == ["https://example.com/a", "ms-settings:display"]


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_non_windows_launchers_are_shell_free(system, monkeypatch):
    monkeypatch.setattr(oa.platform, "system", lambda: system)
    calls = []
    monkeypatch.setattr(oa.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(oa.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    monkeypatch.setattr(oa.shutil, "which", lambda name, *a, **k: None)
    result = oa.open_app({"app_name": "Unknown App"})
    assert isinstance(result, str)
    assert not any(isinstance(c, list) and "win" in c for c in calls)


def test_parameters_none_and_player_log():
    assert "specify" in oa.open_app(None).lower()
    logs = []
    player = SimpleNamespace(write_log=logs.append)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(oa.platform, "system", lambda: "Linux")
    monkeypatch.setattr(oa.shutil, "which", lambda name, *a, **k: "/bin/echo")
    monkeypatch.setattr(oa.subprocess, "Popen", lambda *a, **k: None)
    oa.open_app({"app_name": "echo"}, player=player)
    monkeypatch.undo()
    assert logs and "echo" in logs[0]
