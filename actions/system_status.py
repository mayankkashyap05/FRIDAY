# actions/system_status.py
# Friday — fast, voice-friendly system telemetry with detailed/JSON modes.
#
# psutil is optional: every probe is guarded so the module imports and returns
# an honest message on Windows and Linux without it.

from __future__ import annotations

import json
import platform
import shutil
import socket
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path

try:
    import psutil
    _PSUTIL = True
except ImportError:                                    # pragma: no cover - environment
    psutil = None
    _PSUTIL = False

BASE_DIR = Path(__file__).resolve().parent.parent
UI_SETTINGS_PATH = BASE_DIR / "config" / "ui_settings.json"

_CACHE_TTL_S = 3.0
_CACHE_MAX = 32
_CACHE_LOCK = threading.RLock()
_SNAPSHOT_CACHE: OrderedDict[tuple, tuple[float, str]] = OrderedDict()

_VIRTUAL_ADAPTERS = (
    "vethernet", "wsl", "loopback", "docker", "hyper-v", "virtual",
    "virtualbox", "vmware", "tap-windows", "tunnel",
)


def _cache_get(key: tuple) -> str | None:
    with _CACHE_LOCK:
        entry = _SNAPSHOT_CACHE.get(key)
        if not entry:
            return None
        created, value = entry
        if time.monotonic() - created > _CACHE_TTL_S:
            _SNAPSHOT_CACHE.pop(key, None)
            return None
        _SNAPSHOT_CACHE.move_to_end(key)
        return value


def _cache_put(key: tuple, value: str) -> None:
    with _CACHE_LOCK:
        _SNAPSHOT_CACHE[key] = (time.monotonic(), value)
        _SNAPSHOT_CACHE.move_to_end(key)
        while len(_SNAPSHOT_CACHE) > _CACHE_MAX:
            _SNAPSHOT_CACHE.popitem(last=False)


def _clamp(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _gb(byte_count) -> float:
    try:
        return float(byte_count) / (1024 ** 3)
    except (TypeError, ValueError):
        return 0.0


def _error(name: str, exc: Exception) -> str:
    print(f"[SystemStatus] {name} unavailable: {exc}")
    return "unavailable"


def _uptime_seconds() -> int:
    try:
        return max(0, int(time.time() - psutil.boot_time()))
    except Exception:
        return 0


def _boot_time() -> str:
    try:
        up = _uptime_seconds()
        days, rem = divmod(up, 86400)
        hrs, rem = divmod(rem, 3600)
        mins = rem // 60
        return f"{days}d {hrs}h {mins}m"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------

def _remaining_time(seconds) -> str:
    """Format a sane seconds-left value, rejecting driver sentinels."""
    unknown = (getattr(psutil, "POWER_TIME_UNKNOWN", -1),
               getattr(psutil, "POWER_TIME_UNLIMITED", -2))
    try:
        number = float(seconds)
    except (TypeError, ValueError):
        return ""
    if number in unknown or number < 0 or number > 72 * 3600:
        return ""
    minutes = int(number // 60)
    if minutes < 1:
        return ""
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60}m"


def _battery() -> str:
    if not _PSUTIL or not hasattr(psutil, "sensors_battery"):
        return "Battery info not available."
    try:
        batt = psutil.sensors_battery()
    except Exception as exc:
        return _error("Battery", exc)
    if batt is None:
        return "No battery detected (desktop, likely on AC power)."
    try:
        pct = int(batt.percent)
    except (TypeError, ValueError):
        pct = 0
    plugged = bool(getattr(batt, "power_plugged", False))
    state = "charging" if plugged else "on battery"
    detail = ""
    if not plugged:
        remaining = _remaining_time(getattr(batt, "secsleft", None))
        if remaining:
            detail = f" (~{remaining} left)"
        if pct <= 20:
            detail += " — low battery, plug in soon"
    return f"{pct}% ({state}){detail}"


# ---------------------------------------------------------------------------
# CPU / memory / temperature
# ---------------------------------------------------------------------------

def _cpu(detailed: bool = False) -> str:
    if not _PSUTIL:
        return "unavailable"
    try:
        usage = float(psutil.cpu_percent(interval=0.2))
    except Exception as exc:
        return _error("CPU", exc)
    physical = logical = None
    try:
        physical = psutil.cpu_count(logical=False)
        logical = psutil.cpu_count(logical=True)
    except Exception:
        pass
    cores = f"{physical or logical or '?'} physical / {logical or physical or '?'} logical cores"
    frequency = ""
    try:
        freq = psutil.cpu_freq()
        if freq:
            current = getattr(freq, "current", None)
            maximum = getattr(freq, "max", None)
            if current:
                frequency = f", {int(current)} MHz"
            if maximum:
                frequency += f" / {int(maximum)} MHz max"
    except Exception:
        pass
    result = f"{usage:.0f}% usage, {cores}{frequency}"
    if detailed:
        try:
            per_core = psutil.cpu_percent(interval=None, percpu=True) or []
            if per_core:
                result += ", cores: " + ", ".join(f"{float(value):.0f}%" for value in per_core[:16])
        except Exception:
            pass
    return result


def _memory(detailed: bool = False) -> str:
    if not _PSUTIL:
        return "unavailable"
    try:
        mem = psutil.virtual_memory()
    except Exception as exc:
        return _error("Memory", exc)
    result = (
        f"{mem.percent:.0f}% used "
        f"({_gb(mem.used):.1f}/{_gb(mem.total):.1f} GB, "
        f"{_gb(getattr(mem, 'available', 0)):.1f} GB free)"
    )
    try:
        swap = psutil.swap_memory()
        if swap and getattr(swap, "total", 0) > 0:
            result += (
                f"; swap {swap.percent:.0f}% "
                f"({_gb(swap.used):.1f}/{_gb(swap.total):.1f} GB)"
            )
    except Exception:
        pass
    return result


def _windows_temperature() -> str:
    """Last-resort thermal probe on Windows; never blocks more than 2s."""
    if platform.system() != "Windows" or not shutil.which("powershell"):
        return ""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_TemperatureProbe | "
             "Select-Object -First 1).CurrentReading"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        value = float((result.stdout or "").strip().splitlines()[0]) / 10.0
        if 0 < value < 150:
            return f"CPU {value:.0f}°C"
    except Exception:
        pass
    return ""


def _temperature() -> str:
    if not _PSUTIL:
        return "unavailable on this hardware"
    try:
        sensors = psutil.sensors_temperatures() or {}
    except Exception:
        sensors = {}
    readings = []
    for name, entries in sensors.items():
        for entry in entries or []:
            value = getattr(entry, "current", None)
            if value is None:
                continue
            label = getattr(entry, "label", None) or name
            readings.append(f"{label} {float(value):.0f}°C")
    if readings:
        return ", ".join(readings[:5])
    fallback = _windows_temperature()
    return fallback or "unavailable on this hardware"


# ---------------------------------------------------------------------------
# GPU (opt-in, subprocess only when nvidia-smi exists)
# ---------------------------------------------------------------------------

def _gpu() -> str:
    if not shutil.which("nvidia-smi"):
        return "No dedicated NVIDIA GPU telemetry available"
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except Exception as exc:
        return _error("GPU", exc)
    line = (result.stdout or "").strip().splitlines()
    if not line or result.returncode not in (0, None):
        return "No dedicated NVIDIA GPU telemetry available"
    fields = [part.strip() for part in line[0].split(",")]
    if len(fields) < 4:
        return "No dedicated NVIDIA GPU telemetry available"
    name = fields[0] or "NVIDIA GPU"
    load, used, total = fields[1], fields[2], fields[3]
    text = f"{name} {load}% load, VRAM {used}/{total} MB"
    if len(fields) >= 5 and fields[4]:
        text += f", {fields[4]}°C"
    return text


# ---------------------------------------------------------------------------
# Disk / network
# ---------------------------------------------------------------------------

def _disk(detailed: bool = False) -> str:
    if not _PSUTIL:
        return "unavailable"
    parts = []
    try:
        partitions = psutil.disk_partitions(all=False) or []
    except Exception as exc:
        return _error("Disk", exc)
    for part in partitions:
        fstype = str(getattr(part, "fstype", "") or "").lower()
        mountpoint = str(getattr(part, "mountpoint", "") or "")
        if not mountpoint or not fstype or "cdrom" in fstype:
            continue
        if mountpoint.startswith("/dev/") or mountpoint.startswith("/snap/"):
            continue
        try:
            usage = psutil.disk_usage(mountpoint)
        except Exception:
            continue
        free_gb = _gb(usage.free)
        total_gb = _gb(usage.total)
        entry = (
            f"{mountpoint}: {usage.percent:.0f}% used "
            f"({free_gb:.0f} GB free of {total_gb:.0f} GB)"
        )
        if free_gb < 10 or usage.percent >= 90:
            entry += " [LOW SPACE]"
        parts.append(entry)
    return "; ".join(parts) if parts else "unavailable"


def _wifi() -> str:
    if platform.system() != "Windows" or not shutil.which("netsh"):
        return ""
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except Exception:
        return ""
    ssid = signal = ""
    for line in (result.stdout or "").splitlines():
        text = line.strip()
        if text.lower().startswith("ssid") and ":" in text:
            ssid = text.split(":", 1)[1].strip()
        if text.lower().startswith("signal") and ":" in text:
            signal = text.split(":", 1)[1].strip().replace("%", "")
    if not ssid:
        return ""
    detail = f" Wi-Fi {ssid}"
    if signal:
        detail += f" ({signal}% signal)"
    return detail


def _io_counters() -> str:
    try:
        counters = psutil.net_io_counters()
    except Exception:
        return ""
    if not counters:
        return ""
    sent, received = _gb(counters.bytes_sent), _gb(counters.bytes_recv)
    unit_sent = "GB" if sent >= 1 else "MB"
    unit_recv = "GB" if received >= 1 else "MB"
    value_sent = sent if sent >= 1 else sent * 1024
    value_recv = received if received >= 1 else received * 1024
    return f"; sent {value_sent:.1f} {unit_sent}, received {value_recv:.1f} {unit_recv}"


def _network(detailed: bool = False) -> str:
    if not _PSUTIL:
        return "unavailable"
    physical, fallback = [], []
    try:
        addrs = psutil.net_if_addrs() or {}
        stats = psutil.net_if_stats() or {}
        for iface, entries in addrs.items():
            for entry in entries or []:
                if entry.family != socket.AF_INET:
                    continue
                address = str(entry.address or "")
                if address.startswith("127.") or address.startswith("169.254."):
                    continue
                label = f"{iface}: {address}"
                is_virtual = any(tag in iface.lower() for tag in _VIRTUAL_ADAPTERS)
                if is_virtual:
                    fallback.append(label)
                else:
                    physical.append(label)
    except Exception as exc:
        _error("Network addresses", exc)

    lines = list(dict.fromkeys(physical or fallback)) or ["no network address"]
    try:
        stats = psutil.net_if_stats() or {}
        active = [name for name, state in stats.items() if getattr(state, "isup", False)]
        if active:
            lines.append("Active: " + ", ".join(active[:5]))
    except Exception:
        pass
    result = "; ".join(lines)
    wifi = _wifi()
    if wifi:
        result += wifi
    if detailed:
        result += _io_counters()
    return result


# ---------------------------------------------------------------------------
# Processes / hardware
# ---------------------------------------------------------------------------

def _top_processes(n: int = 5, sort_by: str = "memory") -> str:
    if not _PSUTIL:
        return "unavailable"
    n = _clamp(n, 1, 20, 5)
    by_cpu = str(sort_by or "memory").strip().lower() in ("cpu", "cpu_percent")
    try:
        processes = list(psutil.process_iter(["pid", "name"]))
    except TypeError:
        processes = list(psutil.process_iter())
    except Exception as exc:
        return _error("Processes", exc)

    if by_cpu:
        # Prime psutil's internal CPU counters, then take one bounded sample.
        for proc in processes:
            try:
                proc.cpu_percent(None)
            except Exception:
                pass
        time.sleep(0.15)
    rows = []
    for proc in processes:
        try:
            info = getattr(proc, "info", {}) or {}
            name = str(info.get("name") or "")
            if not name and hasattr(proc, "name"):
                name = str(proc.name())
            if name.lower() == "system idle process":
                continue
            pid = int(info.get("pid") if info.get("pid") is not None else proc.pid)
            if pid <= 0:
                continue
            cpu = float(proc.cpu_percent(None) or 0) if by_cpu else 0.0
            if not by_cpu:
                try:
                    cpu = float(info.get("cpu_percent") or proc.cpu_percent(None) or 0)
                except Exception:
                    cpu = 0.0
            try:
                rss = float(proc.memory_info().rss)
            except Exception:
                rss = 0.0
            if name:
                rows.append((cpu if by_cpu else rss, name, cpu, rss))
        except Exception:
            continue

    rows.sort(key=lambda row: row[0], reverse=True)
    return ", ".join(
        f"{name} ({rss / (1024 ** 2):.0f} MB, {cpu:.0f}% CPU)"
        for _, name, cpu, rss in rows[:n]
    ) or "unavailable"


def _hardware() -> str:
    bits = [f"{platform.node()} — {platform.system()} {platform.release()}"]
    bits.append(platform.machine() or "unknown architecture")
    try:
        physical = psutil.cpu_count(logical=False)
        logical = psutil.cpu_count(logical=True)
        bits.append(f"{physical or logical or '?'} cores ({logical or physical or '?'} threads)")
        bits.append(f"{_gb(psutil.virtual_memory().total):.1f} GB RAM")
    except Exception:
        pass
    try:
        total_disk = sum(
            _gb(psutil.disk_usage(part.mountpoint).total)
            for part in (psutil.disk_partitions(all=False) or [])
            if getattr(part, "mountpoint", "")
        )
        if total_disk:
            bits.append(f"{total_disk:.0f} GB disk")
    except Exception:
        pass
    bits.append(f"Python {platform.python_version()}")
    bits.append(f"Uptime {_boot_time()}")
    return "; ".join(bits)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_FOCUS_ALIASES = [
    (("battery", "power"), "battery"),
    (("temperature", "temp", "thermal"), "temperature"),
    (("processor", "cpu"), "cpu"),
    (("memory", "ram"), "memory"),
    (("storage", "drive", "disk"), "disk"),
    (("wifi", "internet", "network", "ip"), "network"),
    (("process", "running", "task"), "processes"),
    (("hardware", "specs", "os"), "hardware"),
    (("gpu", "graphics", "nvidia"), "gpu"),
    (("uptime", "on since", "how long"), "uptime"),
    (("overview", "health", "status"), "overview"),
]


def _canonical_focus(focus: str) -> str:
    text = str(focus or "overview").strip().lower()
    for keywords, canonical in _FOCUS_ALIASES:
        if any(word in text for word in keywords):
            return canonical
    return "overview"


def _focus_report(focus: str, fmt: str, sort_by: str, limit: int) -> tuple[str, dict]:
    detailed = fmt == "detailed"
    data: dict = {}
    if focus == "battery":
        value = _battery()
        data["battery"] = value
        return f"Battery: {value}.", data
    if focus == "cpu":
        value = _cpu(detailed=detailed)
        data["cpu"] = value
        return f"CPU: {value}.", data
    if focus == "memory":
        value = _memory(detailed=detailed)
        data["memory"] = value
        return f"Memory: {value}.", data
    if focus == "disk":
        value = _disk(detailed=detailed)
        data["disk"] = value
        return f"Disk: {value}.", data
    if focus == "network":
        value = _network(detailed=detailed)
        data["network"] = value
        return f"Network: {value}.", data
    if focus == "processes":
        value = _top_processes(limit, sort_by=sort_by)
        data["processes"] = value
        return f"Top processes: {value}.", data
    if focus == "gpu":
        value = _gpu()
        data["gpu"] = value
        return f"GPU: {value}.", data
    if focus == "temperature":
        value = _temperature()
        data["temperature"] = value
        return f"Temperature: {value}.", data
    if focus == "uptime":
        value = _boot_time()
        data["uptime"] = value
        return f"System uptime: {value}.", data
    if focus == "hardware":
        value = _hardware()
        data["hardware"] = value
        return f"Hardware: {value}.", data

    battery = _battery()
    cpu = _cpu(detailed=detailed)
    memory = _memory(detailed=detailed)
    disk = _disk(detailed=detailed)
    network = _network(detailed=detailed)
    uptime = _boot_time()
    processes = _top_processes(5, sort_by=sort_by)
    data.update({
        "battery": battery, "cpu": cpu, "memory": memory, "disk": disk,
        "network": network, "uptime": uptime, "processes": processes,
    })
    parts = [
        "System status, sir.",
        f"Battery: {battery}.", f"CPU: {cpu}.", f"Memory: {memory}.",
        f"Disk: {disk}.", f"Network: {network}.", f"Uptime: {uptime}.",
        f"Top processes: {processes}.",
    ]
    if detailed:
        temp = _temperature()
        data["temperature"] = temp
        parts.append(f"Temperature: {temp}.")
        warnings = []
        if "low battery" in battery.lower():
            warnings.append("Battery below 20%")
        if "[low space]" in disk.lower():
            warnings.append("Low disk space")
        if "unavailable" in network.lower():
            warnings.append("Network telemetry unavailable")
        parts.append("Warnings: " + ("; ".join(warnings) if warnings else "none") + ".")
    return " ".join(parts), data


def system_status(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Report system health in brief, detailed or JSON format."""
    if not _PSUTIL:
        message = "psutil is not installed. Run: pip install psutil"
        if player:
            try:
                player.write_log("SYS: " + message)
            except Exception:
                pass
        return message

    params = parameters if isinstance(parameters, dict) else {}
    focus = _canonical_focus(
        params.get("focus") or params.get("query") or "overview"
    )
    fmt = str(params.get("format") or "brief").strip().lower()
    if fmt not in ("brief", "detailed", "json"):
        fmt = "brief"
    sort_by = str(params.get("sort_by") or "memory").strip().lower()
    if sort_by not in ("cpu", "memory"):
        sort_by = "memory"
    limit = _clamp(params.get("limit", 5), 1, 20, 5)
    fresh = bool(params.get("fresh", False))

    cache_key = (focus, fmt, sort_by, limit)
    if not fresh:
        cached = _cache_get(cache_key)
        if cached:
            if player:
                try:
                    player.write_log("SYS: system status (cached)")
                except Exception:
                    pass
            return cached

    try:
        text, data = _focus_report(focus, fmt, sort_by, limit)
        if fmt == "json":
            data["focus"] = focus
            data["timestamp"] = time.time()
            result = json.dumps(data, ensure_ascii=False)
        else:
            result = text
            if fmt == "detailed":
                if "\n" not in result:
                    result = f"System status — {focus}\n{result}"
                if "Warnings:" not in result:
                    warnings = []
                    if "low battery" in str(data.get("battery", "")).lower():
                        warnings.append("Battery below 20%")
                    if "[low space]" in str(data.get("disk", "")).lower():
                        warnings.append("Low disk space")
                    result += ("\nWarnings: " +
                               ("; ".join(warnings) if warnings else "none") + ".")
    except Exception as exc:
        result = f"System status unavailable: {exc}"

    _cache_put(cache_key, result)
    if player:
        try:
            player.write_log("SYS: " + result[:160])
        except Exception:
            pass
    if session_memory:
        try:
            session_memory.set_last_search(
                query=f"system status: {focus}", response=result
            )
        except Exception:
            pass
    return result
