# actions/guardian.py
# Friday — PC Health Guardian: proactive smart alerts + battery health report.
#
# Design goals:
#   * zero setup, sensible defaults;
#   * no expensive PowerShell thermal probe on machines that do not expose it;
#   * all alerts are debounced so a single spike never wakes the user;
#   * every config write is atomic (temp file + os.replace).

import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    import psutil
    _PSUTIL = True
except ImportError:  # pragma: no cover - exercised on minimal installs
    psutil = None
    _PSUTIL = False


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_FILE = get_base_dir() / "config" / "guardian.json"

_DEFAULTS = {
    "enabled": True,
    "battery_low": 20,
    "battery_full_plugged": 100,
    "disk_min_free_gb": 10,
    "ram_high": 90,
    "cpu_high": 95,
    "temp_high": 85,
    "backup_days": 7,
    "last_backup": None,
    "cooldown": 3600,
    "sustained_seconds": 60,
    "snoozed_until": 0,
    "last_fired": {},
}
_NUM_KEYS = {
    "battery_low", "battery_full_plugged", "disk_min_free_gb",
    "ram_high", "cpu_high", "temp_high", "backup_days", "cooldown",
    "sustained_seconds",
}
_KEYS_HELP = (
    "battery_low | battery_full_plugged | disk_min_free_gb | ram_high | "
    "cpu_high | temp_high | backup_days | cooldown | sustained_seconds"
)

_lock = threading.RLock()

# The thermal probe is a very expensive operation on Windows (PowerShell +
# WMI). If it fails once, remember that for a while instead of paying the
# penalty on every monitor-loop tick.
_TEMP_BACKOFF_SECONDS = 900.0
_temp_unavailable_until = 0.0
_temp_probe_lock = threading.Lock()

# In-memory "first time this condition became true" markers. They are
# deliberately not persisted: a restart should require a fresh sustained
# breach before firing an alert.
_breach_since: dict[str, float] = {}


def _load() -> dict:
    cfg = dict(_DEFAULTS)
    cfg["last_fired"] = {}
    with _lock:
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for key, value in data.items():
                        if key in cfg:
                            cfg[key] = value
            except Exception:
                pass
    if not isinstance(cfg.get("last_fired"), dict):
        cfg["last_fired"] = {}
    return cfg


def _save(cfg: dict) -> None:
    """Atomically persist guardian settings."""
    with _lock:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix="guardian-", suffix=".json", dir=str(CONFIG_FILE.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(cfg, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, CONFIG_FILE)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


# ── Metric readers ─────────────────────────────────────────────────────────
def _battery() -> tuple[float | None, bool]:
    """Return (percent, plugged). percent None = no battery."""
    if not _PSUTIL:
        return None, False
    try:
        batt = psutil.sensors_battery()
    except Exception:
        return None, False
    if batt is None:
        return None, False
    return float(batt.percent), bool(batt.power_plugged)


def _fixed_drives() -> list[str]:
    """Fixed, local drives only — never network mounts or CD-ROM drives."""
    drives: list[str] = []
    if _PSUTIL:
        try:
            for part in psutil.disk_partitions(all=False):
                # Exclude CD-ROM and network mounts; local fixed disks are the
                # ones worth warning about (fstype may be empty on Windows).
                opts = (part.opts or "").lower()
                fstype = (part.fstype or "").lower()
                if "cdrom" in opts or fstype in ("nfs", "smb", "cifs", "9p"):
                    continue
                if part.device and part.device not in drives:
                    drives.append(part.device)
        except Exception:
            pass
    if not drives:
        candidate = str(get_base_dir())[:3]
        if re.match(r"^[A-Za-z]:[\\/]$", candidate):
            drives = [candidate]
    return drives


def _disk_free_gb(drive: str | None = None) -> float | None:
    if not _PSUTIL:
        return None
    try:
        target = drive or str(get_base_dir())[:3]
        return psutil.disk_usage(target).free / 1024 ** 3
    except Exception:
        return None


def _ram_pct() -> float | None:
    if not _PSUTIL:
        return None
    try:
        return float(psutil.virtual_memory().percent)
    except Exception:
        return None


def _cpu_pct() -> float | None:
    if not _PSUTIL:
        return None
    try:
        return float(psutil.cpu_percent(interval=0.4))
    except Exception:
        return None


def _temp_c() -> float | None:
    """CPU/board temperature, with an availability cache/backoff.

    psutil sensors are cheap and tried first. On Windows we additionally try
    the WMI ACPI thermal zone *once*, then back off when it is unavailable —
    a PowerShell spawn costs seconds and used to happen on every check.
    """
    global _temp_unavailable_until
    if _PSUTIL:
        try:
            temps = psutil.sensors_temperatures() or {}
            for entries in temps.values():
                for entry in entries:
                    if entry.current is not None and 0 <= entry.current <= 110:
                        return float(entry.current)
        except Exception:
            pass

    if platform.system() != "Windows":
        return None

    with _temp_probe_lock:
        if time.time() < _temp_unavailable_until:
            return None
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance -Namespace root/wmi "
                 "-ClassName MSAcpi_ThermalZoneTemperature).CurrentTemperature"],
                capture_output=True, text=True, timeout=5,
            ).stdout or ""
            for value in re.findall(r"[-+]?\d+(?:\.\d+)?", out):
                celsius = float(value) / 10.0 - 273.15
                if 0 <= celsius <= 110:
                    _temp_unavailable_until = 0.0
                    return round(celsius, 1)
        except Exception:
            pass
        # Remember the failure so the next dozen checks stay cheap.
        _temp_unavailable_until = time.time() + _TEMP_BACKOFF_SECONDS
    return None


def battery_health() -> str:
    """Battery wear report via `powercfg /batteryreport` (spoken friendly).

    Output is written into a TemporaryDirectory and removed automatically —
    no report files are left in config/ or the user's home folder.
    """
    if platform.system() != "Windows":
        return "Battery health reporting is only available on Windows, sir."
    try:
        with tempfile.TemporaryDirectory(prefix="friday-batt-") as tmp:
            report = Path(tmp) / "battery-report.html"
            subprocess.run(
                ["powercfg", "/batteryreport", "/output", str(report)],
                capture_output=True, timeout=20, check=True,
            )
            html = report.read_text(encoding="utf-8", errors="ignore")
            design = re.findall(r"DESIGN\s+CAPACITY[^0-9]*([0-9,]+)", html, re.I)
            full = re.findall(r"FULL\s+CHARGE\s+CAPACITY[^0-9]*([0-9,]+)", html, re.I)
            cycles = re.findall(r"CYCLE\s+COUNT[^0-9]*([0-9,]+)", html, re.I)
            if not (design and full):
                return "Battery health report is unavailable on this laptop right now."
            designed = float(design[0].replace(",", ""))
            charged = float(full[0].replace(",", ""))
            if designed <= 0:
                return "Battery health report is unavailable on this laptop right now."
            pct = charged / designed * 100
            verdict = ("excellent" if pct >= 90 else "good" if pct >= 80 else
                       "fair" if pct >= 70 else "worn out")
            result = (f"Battery health is {pct:.0f} percent of design "
                      f"capacity ({verdict}).")
            if cycles:
                result += f" Cycle count: {int(float(cycles[0].replace(',', '')))}."
            return result
    except Exception:
        return "Battery health report is unavailable on this laptop right now."


# ── Alert checks ───────────────────────────────────────────────────────────
def _fired(cfg: dict, key: str) -> bool:
    try:
        last = float(cfg.get("last_fired", {}).get(key, 0) or 0)
    except (TypeError, ValueError):
        return False
    return (time.time() - last) < float(cfg.get("cooldown", 3600))


def _mark_fired(cfg: dict, key: str) -> None:
    cfg.setdefault("last_fired", {})[key] = time.time()


def _sustained(cfg: dict, key: str, condition: bool) -> bool:
    """Require a condition to hold continuously for `sustained_seconds`."""
    now = time.time()
    required = max(0.0, float(cfg.get("sustained_seconds", 60) or 0))
    if not condition:
        _breach_since.pop(key, None)
        return False
    first = _breach_since.setdefault(key, now)
    if now - first < required:
        return False
    _breach_since.pop(key, None)
    return True


def _maybe_alert(cfg: dict, key: str, condition: bool, text: str,
                 alerts: list[str]) -> bool:
    if not _sustained(cfg, key, condition):
        return False
    if _fired(cfg, key):
        return False
    _mark_fired(cfg, key)
    alerts.append(f"[Guardian] {text}")
    return True


def check_guardian() -> list[str]:
    """Return alert texts for newly-crossed health conditions."""
    cfg = _load()
    if not cfg.get("enabled"):
        return []
    try:
        snoozed_until = float(cfg.get("snoozed_until", 0) or 0)
    except (TypeError, ValueError):
        snoozed_until = 0
    if time.time() < snoozed_until:
        return []

    alerts: list[str] = []
    changed = False

    pct, plugged = _battery()
    if pct is not None:
        if _maybe_alert(cfg, "battery_low",
                        not plugged and pct <= float(cfg.get("battery_low", 20)),
                        f"Battery is at {pct:.0f} percent. Plug in the charger soon.",
                        alerts):
            changed = True
        if _maybe_alert(cfg, "battery_full",
                        plugged and pct >= float(cfg.get("battery_full_plugged", 100)),
                        f"Battery is full ({pct:.0f} percent) and still plugged in. "
                        "Unplug to protect the battery.", alerts):
            changed = True

    min_free = float(cfg.get("disk_min_free_gb", 10))
    for drive in _fixed_drives():
        free = _disk_free_gb(drive)
        label = drive.rstrip("\\/") or "system drive"
        if free is not None and _maybe_alert(
            cfg, f"disk_low:{label.rstrip(':')}", free < min_free,
            f"Low disk space: only {free:.1f} GB free on {label}.", alerts,
        ):
            changed = True

    ram = _ram_pct()
    if ram is not None and _maybe_alert(
        cfg, "ram_high", ram >= float(cfg.get("ram_high", 90)),
        f"High memory usage: {ram:.0f} percent of RAM in use.", alerts,
    ):
        changed = True

    cpu = _cpu_pct()
    if cpu is not None and _maybe_alert(
        cfg, "cpu_high", cpu >= float(cfg.get("cpu_high", 95)),
        f"High CPU usage: {cpu:.0f} percent.", alerts,
    ):
        changed = True

    temp = _temp_c()
    if temp is not None and _maybe_alert(
        cfg, "temp_high", temp >= float(cfg.get("temp_high", 85)),
        f"Laptop temperature is high: {temp:.0f} degrees.", alerts,
    ):
        changed = True

    last = cfg.get("last_backup")
    if last:
        try:
            days = (time.time() - float(last)) / 86400
            if _maybe_alert(
                cfg, "backup_due", days >= float(cfg.get("backup_days", 7)),
                f"Last backup was {days:.0f} days ago. Run a backup when you can.",
                alerts,
            ):
                changed = True
        except (TypeError, ValueError):
            pass

    if changed:
        _save(cfg)
    return alerts


# ── Tool actions ───────────────────────────────────────────────────────────
def guardian_action(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """status | report | enable | disable | check | backup | set | reset |
    snooze | unsnooze"""
    params = parameters or {}
    action = str(params.get("action", "status")).lower().strip()
    cfg = _load()

    if action in ("report", "health", "summary"):
        parts = []
        pct, plugged = _battery()
        if pct is None:
            parts.append("No battery detected (desktop, on AC power).")
        else:
            parts.append(f"Battery {pct:.0f} percent "
                         f"({'charging' if plugged else 'on battery'}).")
        parts.append(battery_health())
        for drive in _fixed_drives() or [None]:
            free = _disk_free_gb(drive)
            if free is not None:
                label = (drive or "system drive").rstrip("\\/") or "system drive"
                parts.append(f"{free:.1f} GB free on {label}.")
        ram, cpu, temp = _ram_pct(), _cpu_pct(), _temp_c()
        if ram is not None:
            parts.append(f"RAM {ram:.0f} percent used.")
        if cpu is not None:
            parts.append(f"CPU {cpu:.0f} percent.")
        if temp is not None:
            parts.append(f"Temperature {temp:.0f} degrees.")
        return "Health report, sir. " + " ".join(parts)

    if action in ("enable", "on", "start"):
        cfg["enabled"] = True
        cfg["snoozed_until"] = 0
        _save(cfg)
        return ("PC health guardian is ON, sir. I'll watch your battery, disks, "
                "memory, CPU, temperature and backups automatically.")

    if action in ("disable", "off", "stop"):
        cfg["enabled"] = False
        _save(cfg)
        return "PC health guardian is OFF, sir."

    if action in ("check", "test", "now"):
        alerts = check_guardian()
        return " | ".join(alerts) if alerts else "All health checks look fine right now, sir."

    if action in ("backup", "backup_done", "done"):
        cfg["last_backup"] = time.time()
        _save(cfg)
        return "Backup recorded. I'll remind you again if none happens for a while, sir."

    if action in ("snooze", "later", "pause"):
        try:
            minutes = max(1, min(1440, int(float(params.get("minutes", 30) or 30))))
        except (TypeError, ValueError):
            return "Snooze minutes must be a number, sir."
        cfg["snoozed_until"] = time.time() + minutes * 60
        _save(cfg)
        return f"Guardian alerts snoozed for {minutes} minute(s), sir."

    if action in ("unsnooze", "resume"):
        cfg["snoozed_until"] = 0
        _save(cfg)
        return "Guardian alerts resumed, sir."

    if action in ("set", "config"):
        key = str(params.get("key", "")).lower().strip()
        value = params.get("value")
        if key not in _NUM_KEYS:
            return f"Unknown setting '{key}', sir. Use one of: {_KEYS_HELP}."
        try:
            num = float(value)
        except (TypeError, ValueError):
            return "Value must be a number, sir."
        if math.isnan(num) or math.isinf(num) or num < 0:
            return "Value must be a finite non-negative number, sir."
        if key in ("ram_high", "cpu_high", "temp_high", "battery_low",
                   "battery_full_plugged"):
            num = max(0.0, min(100.0, num))
        elif "cooldown" in key or "seconds" in key:
            num = max(10.0, num)
        cfg[key] = num
        _save(cfg)
        return f"Guardian setting {key} is now {cfg[key]}."

    if action in ("reset", "defaults"):
        cfg = dict(_DEFAULTS)
        cfg["last_fired"] = {}
        _breach_since.clear()
        _save(cfg)
        return "Guardian reset to default settings, sir."

    if action in ("status", "info"):
        state = "ON" if cfg.get("enabled") else "OFF"
        parts = [f"PC health guardian is {state}."]
        try:
            snoozed = float(cfg.get("snoozed_until", 0) or 0)
        except (TypeError, ValueError):
            snoozed = 0
        if snoozed > time.time():
            parts.append(f"Alerts snoozed for another {int((snoozed - time.time()) / 60)} minute(s).")
        if cfg.get("enabled"):
            parts.append(
                f"Battery low threshold {cfg.get('battery_low')}%, disk below "
                f"{cfg.get('disk_min_free_gb')} GB free, RAM above "
                f"{cfg.get('ram_high')}%, CPU above {cfg.get('cpu_high')}%.")
        if cfg.get("last_backup"):
            days = (time.time() - float(cfg["last_backup"])) / 86400
            parts.append(f"Last backup {days:.0f} days ago.")
        return " ".join(parts)

    return ("Unknown action '" + action + "', sir. Use: status | report | enable | "
            "disable | check | backup | set | reset | snooze | unsnooze.")
