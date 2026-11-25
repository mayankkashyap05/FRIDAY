"""Fully offline combinatorial tests for actions.system_status."""

from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import pytest

from actions import daily_briefing
from actions import system_status as ss


@pytest.fixture(autouse=True)
def _clear_cache():
    with ss._CACHE_LOCK:
        ss._SNAPSHOT_CACHE.clear()
    yield
    with ss._CACHE_LOCK:
        ss._SNAPSHOT_CACHE.clear()


def _disk_usage(total, free):
    return SimpleNamespace(total=total, free=free, used=total - free,
                           percent=round((total - free) / total * 100))


def _fake_psutil(monkeypatch, *, battery=None, temps=None):
    calls = {"cpu": [], "sleep": []}

    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    fake = SimpleNamespace(
        cpu_count=lambda logical=True: 8 if logical else 4,
        cpu_percent=lambda interval=None, percpu=False: (
            [10.0] * 8 if percpu else 25.0
        ),
        cpu_freq=lambda: SimpleNamespace(current=2400.0, max=4000.0),
        virtual_memory=lambda: SimpleNamespace(
            percent=60.0, used=12 * 1024**3, total=20 * 1024**3,
            available=8 * 1024**3,
        ),
        swap_memory=lambda: SimpleNamespace(
            total=4 * 1024**3, used=1 * 1024**3, percent=25.0,
        ),
        sensors_battery=lambda: battery,
        sensors_temperatures=lambda: temps if temps is not None else {
            "coretemp": [SimpleNamespace(label="Package id 0", current=55.0)]
        },
        disk_partitions=lambda all=False: [
            SimpleNamespace(fstype="ntfs", mountpoint="C:\\"),
            SimpleNamespace(fstype="cdrom", mountpoint="D:\\"),
            SimpleNamespace(fstype="", mountpoint="E:\\"),
            SimpleNamespace(fstype="ext4", mountpoint="/bad"),
            SimpleNamespace(fstype="ext4", mountpoint="/dev/sda1"),
        ],
        disk_usage=lambda path: _disk_usage(100 * 1024**3, 8 * 1024**3)
        if path in ("C:\\", "/bad") else _disk_usage(500 * 1024**3, 400 * 1024**3),
        net_if_addrs=lambda: {
            "Ethernet": [SimpleNamespace(family=socket.AF_INET, address="192.168.1.10")],
            "Loopback Pseudo": [SimpleNamespace(family=socket.AF_INET, address="127.0.0.1")],
            "vEthernet (WSL)": [SimpleNamespace(family=socket.AF_INET, address="172.20.1.1")],
            "Wi-Fi": [SimpleNamespace(family=socket.AF_INET, address="169.254.4.5")],
        },
        net_if_stats=lambda: {
            "Ethernet": SimpleNamespace(isup=True),
            "Wi-Fi": SimpleNamespace(isup=True),
            "vEthernet (WSL)": SimpleNamespace(isup=False),
        },
        net_io_counters=lambda: SimpleNamespace(bytes_sent=1.5 * 1024**3,
                                                bytes_recv=0.5 * 1024**3),
        boot_time=lambda: 0,
        process_iter=lambda attrs=None: [],
        NoSuchProcess=NoSuchProcess,
        AccessDenied=AccessDenied,
        POWER_TIME_UNKNOWN=-1,
        POWER_TIME_UNLIMITED=-2,
    )
    monkeypatch.setattr(ss, "psutil", fake)
    monkeypatch.setattr(ss, "_PSUTIL", True)
    return fake, calls


# ---------------------------------------------------------------------------
# Focus + format matrix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("focus", [
    "overview", "battery", "cpu", "memory", "ram", "disk", "storage",
    "network", "wifi", "processes", "top", "gpu", "temperature", "temp",
    "uptime", "hardware", "specs", "os", "nonsense",
])
@pytest.mark.parametrize("fmt", ["brief", "detailed", "json"])
def test_all_focus_and_format_combinations(monkeypatch, focus, fmt):
    _fake_psutil(monkeypatch)
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    result = ss.system_status({"focus": focus, "format": fmt})
    assert isinstance(result, str) and result
    if fmt == "json":
        data = json.loads(result)
        assert isinstance(data, dict) and data["focus"]
    elif fmt == "detailed":
        assert "\n" in result or len(result) > 40
    else:
        assert "\n" not in result


def test_parameters_none_and_player_logging(monkeypatch):
    _fake_psutil(monkeypatch)
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    logged = []
    player = SimpleNamespace(write_log=lambda line: logged.append(line))
    result = ss.system_status(None, player=player)
    assert "System status" in result
    assert logged and logged[0].startswith("SYS:")


def test_daily_briefing_compatibility(monkeypatch):
    _fake_psutil(monkeypatch)
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    text = daily_briefing._get_system()
    assert isinstance(text, str) and "Battery" in text


# ---------------------------------------------------------------------------
# Top processes
# ---------------------------------------------------------------------------

def _proc(pid, name, cpu, rss):
    return SimpleNamespace(
        pid=pid,
        info={"pid": pid, "name": name, "cpu_percent": cpu},
        cpu_percent=lambda interval=None: cpu,
        memory_info=lambda: SimpleNamespace(rss=rss),
    )


@pytest.mark.parametrize("sort_by,first", [("cpu", "python.exe"),
                                          ("memory", "chrome.exe")])
def test_top_processes_primes_cpu_and_sorts(monkeypatch, sort_by, first):
    procs = [
        _proc(1, "System Idle Process", 99.0, 0),
        _proc(0, "idle", 99.0, 0),
        _proc(10, "chrome.exe", 5.0, 900 * 1024**2),
        _proc(11, "python.exe", 40.0, 100 * 1024**2),
    ]
    primed = []
    fake, calls = _fake_psutil(monkeypatch)
    monkeypatch.setattr(fake, "process_iter",
                        lambda attrs=None: procs)
    monkeypatch.setattr(ss.time, "sleep", lambda value: calls["sleep"].append(value))
    result = ss._top_processes(3, sort_by=sort_by)
    assert result.startswith(first)
    assert "System Idle" not in result and "idle" not in result
    assert "MB" in result and "% CPU" in result
    if sort_by == "cpu":
        assert calls["sleep"] == [0.15]


def test_top_processes_clamps_limit(monkeypatch):
    procs = [_proc(index, f"app{index}.exe", 1.0, 1024**2) for index in range(1, 25)]
    fake, _ = _fake_psutil(monkeypatch)
    monkeypatch.setattr(fake, "process_iter", lambda attrs=None: procs)
    assert ss._top_processes(999).count("app") == 20
    assert ss._top_processes(0).count("app") == 1


# ---------------------------------------------------------------------------
# Battery edge cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (3660, "1h 1m"), (1200, "20m"), (-1, ""), (-2, ""),
    (4294967295, ""), (-50, ""),
])
def test_battery_remaining_time_guards(monkeypatch, seconds, expected):
    _fake_psutil(monkeypatch)
    assert ss._remaining_time(seconds) == expected


@pytest.mark.parametrize("battery,expected", [
    (None, "No battery detected"),
    (SimpleNamespace(percent=80, power_plugged=True, secsleft=-1), "charging"),
    (SimpleNamespace(percent=45, power_plugged=False, secsleft=3660), "1h 1m"),
    (SimpleNamespace(percent=45, power_plugged=False, secsleft=-1), "on battery"),
    (SimpleNamespace(percent=18, power_plugged=False, secsleft=-2), "low battery"),
])
def test_battery_states(monkeypatch, battery, expected):
    _fake_psutil(monkeypatch, battery=battery)
    assert expected in ss._battery()


# ---------------------------------------------------------------------------
# Network / Wi-Fi / GPU / disk
# ---------------------------------------------------------------------------

def test_network_filters_noisy_interfaces(monkeypatch):
    _fake_psutil(monkeypatch)
    monkeypatch.setattr(ss, "_wifi", lambda: " Wi-Fi Home (72% signal)")
    result = ss._network(detailed=True)
    assert "192.168.1.10" in result
    assert "127.0.0.1" not in result and "169.254" not in result
    assert "vEthernet" not in result and "Active:" in result
    assert "Wi-Fi Home (72% signal)" in result
    assert "sent" in result and "received" in result


def test_network_keeps_virtual_only_as_fallback(monkeypatch):
    fake, _ = _fake_psutil(monkeypatch)
    monkeypatch.setattr(fake, "net_if_addrs", lambda: {
        "vEthernet (WSL)": [SimpleNamespace(family=socket.AF_INET, address="172.20.1.1")],
    })
    monkeypatch.setattr(ss, "_wifi", lambda: "")
    assert "172.20.1.1" in ss._network()


def test_wifi_netsh_failure_is_graceful(monkeypatch):
    _fake_psutil(monkeypatch)
    monkeypatch.setattr(ss.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ss.shutil, "which", lambda name: "netsh.exe")
    monkeypatch.setattr(ss.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow")))
    assert ss._wifi() == ""


def test_wifi_parses_ssid_and_signal(monkeypatch):
    _fake_psutil(monkeypatch)
    monkeypatch.setattr(ss.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ss.shutil, "which", lambda name: "netsh.exe")
    monkeypatch.setattr(ss.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="    SSID  : My Home Net\n    Signal : 87%\n", returncode=0,
    ))
    assert ss._wifi() == " Wi-Fi My Home Net (87% signal)"


def test_gpu_parses_nvidia_smi_csv(monkeypatch):
    _fake_psutil(monkeypatch)
    monkeypatch.setattr(ss.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(ss.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="NVIDIA RTX 4070, 42, 2048, 12288, 61\n", returncode=0,
    ))
    result = ss._gpu()
    assert "RTX 4070" in result and "42% load" in result
    assert "2048/12288 MB" in result and "61°C" in result


@pytest.mark.parametrize("which,error", [
    (lambda name: None, None),
    (lambda name: "/usr/bin/nvidia-smi", TimeoutError("timeout")),
])
def test_gpu_degrades_cleanly(monkeypatch, which, error):
    _fake_psutil(monkeypatch)
    monkeypatch.setattr(ss.shutil, "which", which)
    if error:
        monkeypatch.setattr(ss.subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(error))
    assert "No dedicated NVIDIA" in ss._gpu() or "unavailable" in ss._gpu()


def test_disk_skips_bad_drives_and_flags_low_space(monkeypatch):
    _fake_psutil(monkeypatch)
    result = ss._disk()
    assert "C:\\" in result and "[LOW SPACE]" in result
    assert "D:\\" not in result and "E:\\" not in result and "/dev/" not in result
    assert "GB free" in result


def test_memory_includes_swap(monkeypatch):
    _fake_psutil(monkeypatch)
    assert "swap 25%" in ss._memory()


def test_temperature_reads_sensors(monkeypatch):
    _fake_psutil(monkeypatch, temps={"acpitz": [SimpleNamespace(label="", current=48)]})
    assert "48°C" in ss._temperature()


# ---------------------------------------------------------------------------
# Cache / degradation
# ---------------------------------------------------------------------------

def test_ttl_cache_and_fresh_bypass(monkeypatch):
    _fake_psutil(monkeypatch)
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    calls = []
    original = ss._battery

    def counted():
        calls.append(1)
        return original()

    monkeypatch.setattr(ss, "_battery", counted)
    ss.system_status({"focus": "battery"})
    ss.system_status({"focus": "battery"})
    assert len(calls) == 1
    ss.system_status({"focus": "battery", "fresh": True})
    assert len(calls) == 2


def test_psutil_missing_message(monkeypatch):
    monkeypatch.setattr(ss, "_PSUTIL", False)
    assert "pip install psutil" in ss.system_status(None)


def test_hardware_focus_specs(monkeypatch):
    _fake_psutil(monkeypatch)
    result = ss.system_status({"focus": "hardware"})
    assert "Python 3." in result and "GB RAM" in result


def test_network_active_interfaces_prefers_physical(monkeypatch):
    fake, _ = _fake_psutil(monkeypatch)
    monkeypatch.setattr(fake, "net_if_addrs", lambda: {
        "Ethernet": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.2")],
        "vEthernet (WSL)": [SimpleNamespace(family=socket.AF_INET, address="172.20.0.1")],
    })
    monkeypatch.setattr(fake, "net_if_stats", lambda: {
        "Ethernet": SimpleNamespace(isup=True),
        "vEthernet (WSL)": SimpleNamespace(isup=True),
    })
    monkeypatch.setattr(ss, "_wifi", lambda: "")
    result = ss._network()
    assert "Active: Ethernet" in result
    assert "Active: Ethernet, vEthernet" not in result


def test_wifi_decodes_using_explicit_utf8(monkeypatch):
    _fake_psutil(monkeypatch)
    monkeypatch.setattr(ss.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ss.shutil, "which", lambda name: "netsh.exe")
    observed = {}
    def run(*args, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(stdout="SSID : Café\nSignal : 90%\n", returncode=0)
    monkeypatch.setattr(ss.subprocess, "run", run)
    assert "Café" in ss._wifi()
    assert observed["encoding"].lower() == "utf-8"
    assert observed["errors"] == "replace"
