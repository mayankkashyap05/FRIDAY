"""Offline deep tests for guardian.py and monitor.py."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from actions import guardian, monitor


# ── guardian ────────────────────────────────────────────────────────────────
def _set_guardian_cfg(monkeypatch, tmp_path, **updates):
    cfg = dict(guardian._DEFAULTS)
    cfg.update(updates)
    cfg["last_fired"] = {}
    monkeypatch.setattr(guardian, "CONFIG_FILE", tmp_path / "guardian.json")
    guardian.CONFIG_FILE.write_text(json.dumps(cfg), encoding="utf-8")
    guardian._breach_since.clear()
    return cfg


def test_guardian_atomic_write_leaves_no_temp_files(tmp_path, monkeypatch):
    _set_guardian_cfg(monkeypatch, tmp_path)
    guardian.guardian_action({"action": "set", "key": "cpu_high", "value": 88})
    assert guardian.guardian_action({"action": "status"}).find("88") >= 0
    assert not list(tmp_path.glob("guardian-*.json"))


def test_guardian_thermal_probe_backoff_after_failure(tmp_path, monkeypatch):
    _set_guardian_cfg(monkeypatch, tmp_path)
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise OSError("no powershell")

    monkeypatch.setattr(guardian.platform, "system", lambda: "Windows")
    monkeypatch.setattr(guardian.subprocess, "run", fail)
    monkeypatch.setattr(guardian, "_temp_unavailable_until", 0.0)
    monkeypatch.setattr(guardian, "_PSUTIL", False)
    assert guardian._temp_c() is None
    assert guardian._temp_c() is None
    assert len(calls) == 1
    assert guardian._temp_unavailable_until > 0


def test_guardian_sustained_breach_debounce(tmp_path, monkeypatch):
    _set_guardian_cfg(monkeypatch, tmp_path, sustained_seconds=60,
                      cpu_high=90, ram_high=200, temp_high=200)
    monkeypatch.setattr(guardian, "_cpu_pct", lambda: 99.0)
    monkeypatch.setattr(guardian, "_ram_pct", lambda: 1.0)
    monkeypatch.setattr(guardian, "_temp_c", lambda: 20.0)
    monkeypatch.setattr(guardian, "_battery", lambda: (None, False))
    monkeypatch.setattr(guardian, "_fixed_drives", lambda: [])
    clock = [1_700_000_000.0]
    monkeypatch.setattr(guardian.time, "time", lambda: clock[0])
    assert guardian.check_guardian() == []
    clock[0] += 30
    assert guardian.check_guardian() == []
    clock[0] += 31
    alerts = guardian.check_guardian()
    assert len(alerts) == 1 and "High CPU" in alerts[0]


def test_guardian_checks_every_fixed_drive(tmp_path, monkeypatch):
    _set_guardian_cfg(monkeypatch, tmp_path, sustained_seconds=0,
                      cpu_high=200, ram_high=200, temp_high=200,
                      disk_min_free_gb=10)
    monkeypatch.setattr(guardian, "_cpu_pct", lambda: 1.0)
    monkeypatch.setattr(guardian, "_ram_pct", lambda: 1.0)
    monkeypatch.setattr(guardian, "_temp_c", lambda: None)
    monkeypatch.setattr(guardian, "_battery", lambda: (None, False))
    monkeypatch.setattr(guardian, "_fixed_drives", lambda: ["C:\\", "D:\\"])
    monkeypatch.setattr(guardian, "_disk_free_gb",
                        lambda drive=None: 2.0 if drive == "D:\\" else 500.0)
    alerts = guardian.check_guardian()
    assert len(alerts) == 1 and "D:" in alerts[0]
    cfg = json.loads(guardian.CONFIG_FILE.read_text())
    assert "disk_low:D" in cfg["last_fired"]
    assert "disk_low:C" not in cfg["last_fired"]


def test_battery_health_parses_capacity_and_cycles(tmp_path, monkeypatch):
    monkeypatch.setattr(guardian.platform, "system", lambda: "Windows")

    def fake_run(cmd, **kwargs):
        output = Path(cmd[cmd.index("/output") + 1])
        output.write_text(
            "<table><tr><td>DESIGN CAPACITY</td><td>50,000 mWh</td></tr>"
            "<tr><td>FULL CHARGE CAPACITY</td><td>42,500 mWh</td></tr>"
            "<tr><td>CYCLE COUNT</td><td>137</td></tr></table>",
            encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(guardian.subprocess, "run", fake_run)
    result = guardian.battery_health()
    assert "85 percent" in result and "137" in result


def test_guardian_snooze_and_unsnooze(tmp_path, monkeypatch):
    _set_guardian_cfg(monkeypatch, tmp_path, cpu_high=1, sustained_seconds=0)
    monkeypatch.setattr(guardian, "_cpu_pct", lambda: 99.0)
    monkeypatch.setattr(guardian, "_ram_pct", lambda: 1.0)
    monkeypatch.setattr(guardian, "_temp_c", lambda: None)
    monkeypatch.setattr(guardian, "_battery", lambda: (None, False))
    monkeypatch.setattr(guardian, "_fixed_drives", lambda: [])
    assert "snoozed" in guardian.guardian_action({"action": "snooze", "minutes": 30}).lower()
    assert guardian.check_guardian() == []
    assert "resumed" in guardian.guardian_action({"action": "unsnooze"}).lower()
    assert any("High CPU" in alert for alert in guardian.check_guardian())


# ── monitor ─────────────────────────────────────────────────────────────────
def _reset_monitor(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "RULES_FILE", tmp_path / "monitor.json", raising=False)
    monkeypatch.setattr(monitor, "STATE_FILE", tmp_path / "state.json", raising=False)
    monkeypatch.setattr(monitor, "_rules", [], raising=False)
    monkeypatch.setattr(monitor, "_loaded", False, raising=False)
    monkeypatch.setattr(monitor, "_paused", False, raising=False)
    monitor._sustained_since.clear()


def test_monitor_reads_disk_letter_and_process_exit(monkeypatch):
    fake_psutil = SimpleNamespace(
        disk_usage=lambda drive: SimpleNamespace(percent=42 if drive == "D:" else 10),
        process_iter=lambda attrs: [SimpleNamespace(info={"name": "chrome.exe"})],
    )
    monkeypatch.setattr(monitor, "_PSUTIL", True)
    monkeypatch.setattr(monitor, "psutil", fake_psutil, raising=False)
    value, label = monitor._read_value("disk:D")
    assert value == 42 and "D:" in label
    value, label = monitor._read_value("process_exit", {"process": "chrome"})
    assert value == 1.0 and "chrome" in label
    value, _ = monitor._read_value("process_exit", {"process": "missing"})
    assert value == 0.0


def test_monitor_sustained_and_per_rule_cooldown(tmp_path, monkeypatch):
    _reset_monitor(monkeypatch, tmp_path)
    monitor.add_rule("cpu", 90, above=True, sustained=10, cooldown=100)
    monkeypatch.setattr(monitor, "_read_value", lambda *a, **k: (99.0, "CPU usage"))
    clock = [500.0]
    monkeypatch.setattr(monitor.time, "time", lambda: clock[0])
    assert monitor.check_rules() == []
    clock[0] += 11
    first = monitor.check_rules()
    assert len(first) == 1
    clock[0] += 5
    assert monitor.check_rules() == []
    assert not list(tmp_path.glob("friday-monitor-*"))


def test_monitor_pause_resume_and_clear(tmp_path, monkeypatch):
    _reset_monitor(monkeypatch, tmp_path)
    monitor.add_rule("ram", 80)
    monkeypatch.setattr(monitor, "_read_value", lambda *a, **k: (99.0, "RAM usage"))
    assert "paused" in monitor.monitor_action({"action": "pause"}).lower()
    assert monitor.check_rules() == []
    assert "resumed" in monitor.monitor_action({"action": "resume"}).lower()
    assert len(monitor.check_rules()) == 1
    assert "Cleared" in monitor.monitor_action({"action": "clear"})


def test_email_watch_seeds_watermark_on_first_enable(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "WATCH_FILE", tmp_path / "watch.json")
    monkeypatch.setattr(monitor, "_mail_credentials", lambda: ("user@gmail.com", "pass"))
    monkeypatch.setattr(monitor, "_oauth_ready", lambda: False)
    monkeypatch.setattr(monitor, "_poll_imap", lambda state, seed_only=False: (42, ["old"]))
    result = monitor.email_watch_action({"action": "enable"})
    assert "enabled" in result.lower()
    state = json.loads(monitor.WATCH_FILE.read_text())
    assert state["last_uid"] == 42 and state["seeded"] is True


def test_email_watch_poll_oauth_and_filters(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "WATCH_FILE", tmp_path / "watch.json")
    state = dict(monitor._WATCH_DEFAULT, enabled=True, last_uid=10, seeded=True,
                 sender="boss@", subject="invoice")
    monitor.WATCH_FILE.write_text(json.dumps(state))
    monkeypatch.setattr(monitor, "_oauth_ready", lambda: True)
    monkeypatch.setattr(monitor, "_poll_api",
                        lambda state: (20, ["[Email] New mail from boss: invoice"]))
    alerts = monitor.check_email_watch()
    assert alerts == ["[Email] New mail from boss: invoice"]
    assert json.loads(monitor.WATCH_FILE.read_text())["last_uid"] == 20
    assert monitor._passes_filters(state, "boss@corp.com", "invoice pending", True)
    assert not monitor._passes_filters(state, "spam@corp.com", "invoice", True)
    assert not monitor._passes_filters(state, "boss@corp.com", "lunch", True)


def test_email_watch_imap_fallback_when_oauth_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "WATCH_FILE", tmp_path / "watch.json")
    monitor.WATCH_FILE.write_text(json.dumps(
        dict(monitor._WATCH_DEFAULT, enabled=True, last_uid=5, seeded=True)))
    monkeypatch.setattr(monitor, "_oauth_ready", lambda: False)
    monkeypatch.setattr(monitor, "_poll_imap", lambda state: (11, ["[Email] new"]))
    assert monitor.check_email_watch() == ["[Email] new"]
    assert json.loads(monitor.WATCH_FILE.read_text())["last_uid"] == 11
