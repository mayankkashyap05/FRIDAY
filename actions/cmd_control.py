# actions/cmd_control.py
# Friday — hardened, Windows 11-ready command control.
#
# Modern CIM only; legacy WMI CLI tooling and model-authored commands are never used.

from __future__ import annotations

import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

try:
    from config.models import MODEL_FLASH, MODEL_FLASH_LITE  # noqa: F401  (shared contract)
except Exception:
    MODEL_FLASH = MODEL_FLASH_LITE = ""


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
MAX_OUTPUT = 8000
MAX_HISTORY = 25
_COMMAND_HISTORY: list[dict] = []


def _get_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")
    return bool(value) if value is not None else default


def _clamp(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


_CIM = 'powershell -NoProfile -Command "Get-CimInstance'

WIN_COMMAND_MAP = [
    (["disk space", "disk usage", "storage", "free space", "c drive space"],
     _CIM + " Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID, VolumeName, "
     "@{N='FreeGB';E={[math]::Round($_.FreeSpace/1GB,2)}}, "
     "@{N='SizeGB';E={[math]::Round($_.Size/1GB,2)}}\"", False),
    (["running processes", "list processes", "show processes", "active processes", "tasklist"],
     "tasklist /fo table", False),
    (["ip address", "my ip", "network info", "ipconfig"], "ipconfig /all", False),
    (["ping", "internet connection", "connected to internet"], "ping -n 4 google.com", False),
    (["open ports", "listening ports", "netstat"], "netstat -an | findstr LISTENING", False),
    (["wifi networks", "available wifi", "wireless networks"], "netsh wlan show networks", False),
    (["system info", "computer info", "hardware info", "pc info", "specs"], "systeminfo", False),
    (["cpu usage", "processor usage", "processor", "cpu info"],
     _CIM + " Win32_Processor | Select-Object Name, NumberOfCores, MaxClockSpeed, LoadPercentage\"", False),
    (["memory usage", "ram usage", "memory info"],
     _CIM + " Win32_OperatingSystem | Select-Object "
     "@{N='TotalGB';E={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}}, "
     "@{N='FreeGB';E={[math]::Round($_.FreePhysicalMemory/1MB,2)}}\"", False),
    (["windows version", "os version", "os info"],
     _CIM + " Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber\"", False),
    (["bios", "serial number", "motherboard"],
     _CIM + " Win32_BIOS | Select-Object Manufacturer, SMBIOSBIOSVersion, SerialNumber\"", False),
    (["battery", "battery level", "power status"],
     _CIM + " Win32_Battery | Select-Object EstimatedChargeRemaining, BatteryStatus\"", False),
    (["current time", "what time", "system time"], "time /t", False),
    (["current date", "what date", "system date"], "date /t", False),
    (["desktop files", "files on desktop"], f'dir "{Path.home() / "Desktop"}"', False),
    (["downloads", "files in downloads"], f'dir "{Path.home() / "Downloads"}"', False),
    (["large files", "biggest files", "largest files"],
     'powershell "Get-ChildItem C:\\ -Recurse -ErrorAction SilentlyContinue | '
     'Sort-Object Length -Descending | Select-Object -First 10 FullName,Length | Format-Table"', False),
]

_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\]=<>!~]{0,80}$")


def _find_hardcoded(task: str) -> str | None:
    task_lower = (task or "").lower()
    if "notepad" in task_lower or any(ext in task_lower for ext in (".txt", ".log", ".md", ".csv")):
        match = re.search(r'["\']?([\S]+\.(?:txt|log|md|csv|json|xml))["\']?', task, re.IGNORECASE)
        if match:
            filename = match.group(1)
            desktop = Path.home() / "Desktop"
            filepath = Path(filename) if Path(filename).is_absolute() else desktop / filename
            return f'notepad "{filepath}"'
        if "notepad" in task_lower:
            return "notepad"
    pip_match = re.search(r"(?:pip\s+install|install\s+package)\s+([\w\-]+)", task_lower)
    if pip_match:
        package = pip_match.group(1)
        if not _PACKAGE_RE.match(package):
            return None
        return f"pip install {package}"
    for keywords, command, _ in WIN_COMMAND_MAP:
        if command and any(keyword in task_lower for keyword in keywords):
            return command
    return None


BLOCKED_PATTERNS = [
    # Destructive CMD / POSIX
    r"\brm\s+-rf\b", r"\brmdir\s+/s\b", r"\bdel\s+/[fqs]",
    r"\bformat\b", r"\bdiskpart\b", r"\bfdisk\b", r"\bmkfs\b",
    r"\bdd\s+if=", r"\bshutdown\b", r"\breboot\b", r"\brestart-computer\b",
    r"\bstop-computer\b", r"\bstop-process\b", r"\bkill\s+-9\b", r"\btaskkill\b",
    r"\breg\s+(delete|add)\b", r"\bbcdedit\b", r"\bnet\s+(localgroup|user)\b",
    # PowerShell evasion / destruction
    r"(?:^|[\s\"\'])-enc(odedcommand)?\b", r"\binvoke-expression\b", r"\biex\b",
    r"\bdownloadstring\b", r"\bdownloadfile\b", r"\bclear-disk\b",
    r"\bformat-volume\b", r"\bremove-item\s+.*-recurse\s+.*-force\b",
    r"\bset-mppreference\s+.*-disablerealtimemonitoring\b",
    r"\beval\b", r"\b__import__\b",
    # Windows persistence / wipe
    r"\bvssadmin\s+delete\s+shadows\b", r"\bcipher\s+/w\b",
    r"\breg\s+delete\s+hk(lm|cr|u)\b", r"\bnet\s+user\s+.*\/add\b",
    r"\bnet\s+localgroup\s+administrators\b", r"\btakeown\s+.*windows\b",
    r"\bicacls\s+.*system32\b",
    # Download cradles & exec helpers
    r"\bcertutil(\.exe)?\s+-(urlcache|decode)", r"\bbitsadmin(\.exe)?\b",
    r"\bvssadmin(\.exe)?\b", r"\bstart-process\b",
    # Pipe-to-shell
    r"\|\s*(sh|bash|cmd|powershell|pwsh)\b",
]
_BLOCKED_RE = re.compile("|".join(BLOCKED_PATTERNS), re.IGNORECASE)
_METACHARS = re.compile(r"[&|<>^();`$\r\n\x00]")


def _is_blocked_command(command: str, trusted: bool = False) -> tuple[bool, str]:
    """Screen command text before any process is created.

    Curated intents are trusted for metacharacters only; explicit commands never are.
    """
    text = str(command or "")
    if "\x00" in text or "\r" in text or "\n" in text:
        return True, "control characters are not allowed"
    match = _BLOCKED_RE.search(text)
    if match:
        return True, f"Blocked pattern: '{match.group()}'"
    if not trusted and _METACHARS.search(text):
        return True, "command contains shell metacharacters"
    return False, "OK"


def _is_safe(command: str) -> tuple[bool, str]:
    blocked, reason = _is_blocked_command(command)
    return (not blocked), reason


def _ask_gemini(task: str) -> str:
    """Deprecated: Friday never asks a model to author shell commands."""
    return ("For safety, I won't generate terminal commands automatically. "
            "Provide an explicit `command` or use a supported system-info task.")


def _redact(command: str) -> str:
    return re.sub(r"(?i)(--?(?:token|password|passwd|api[_-]?key|secret)\s*[=:]\s*)(\S+)",
                  r"\1[REDACTED]", str(command or ""))


def _record_history(command: str, shell: str, cwd: str, exit_code, elapsed_ms: int):
    _COMMAND_HISTORY.append({
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "command": _redact(command), "shell": shell, "cwd": cwd,
        "exit_code": exit_code, "ms": elapsed_ms,
    })
    del _COMMAND_HISTORY[:-MAX_HISTORY]


def _history_text() -> str:
    if not _COMMAND_HISTORY:
        return "No commands executed yet."
    lines = [f"{i}. [{item['at']}] {item['command']} → exit {item['exit_code']} ({item['ms']} ms)"
             for i, item in enumerate(_COMMAND_HISTORY, 1)]
    return "Command history:\n" + "\n".join(lines)


def _build_argv(command: str, shell: str, cwd: str):
    if shell == "powershell":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    if _get_platform() == "windows":
        return ["cmd", "/c", command]
    return shlex.split(command)          # never shell=True on POSIX


def _run_silent(command: str, timeout: int = 30, shell: str = "cmd", cwd: str = "") -> str:
    started = time.perf_counter()
    working_dir = cwd or str(Path.home())
    try:
        result = subprocess.run(
            _build_argv(command, shell, working_dir),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, cwd=working_dir,
        )
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        elapsed = int((time.perf_counter() - started) * 1000)
        _record_history(command, shell, working_dir, result.returncode, elapsed)
        return (output[:MAX_OUTPUT] or "Command executed with no output.") + \
            f"\n[exit={result.returncode}, {elapsed}ms]"
    except subprocess.TimeoutExpired:
        _record_history(command, shell, working_dir, "timeout",
                        int((time.perf_counter() - started) * 1000))
        return f"Command timed out after {timeout}s."
    except FileNotFoundError as exc:
        _record_history(command, shell, working_dir, "not_found", 0)
        return f"Command not found: {exc}"
    except Exception as exc:
        return f"Execution error: {exc}"


def _run_visible(command: str) -> None:
    try:
        if _get_platform() == "windows":
            subprocess.Popen(["cmd", "/k", command],
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        elif _get_platform() == "macos":
            escaped = command.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e",
                              f'tell application "Terminal" to do script "{escaped}"'])
        else:
            for terminal in ("gnome-terminal", "xterm", "konsole"):
                try:
                    subprocess.Popen([terminal, "--", "bash", "-c", f"{command}; exec bash"])
                    break
                except FileNotFoundError:
                    continue
    except Exception as exc:
        print(f"[CMD] ⚠️ Terminal open failed: {exc}")


def cmd_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None
) -> str:
    """Run approved commands with strict blocklist screening and dry-run support."""
    params = parameters if isinstance(parameters, dict) else {}
    task = str(params.get("task", "") or "").strip()
    command = str(params.get("command", "") or "").strip()
    action = str(params.get("action", "") or "").strip().lower()

    if action in ("history", "recent"):
        return _history_text()

    if not task and not command:
        return "Please describe what you want to do, sir."

    hardcoded = False
    if not command:
        command = _find_hardcoded(task) or ""
        if command:
            hardcoded = True
            print(f"[CMD] ⚡ Hardcoded: {command[:80]}")
        else:
            return (
                "For safety, I won't generate terminal commands automatically. "
                "Either rephrase the request into a supported system-info task "
                "(disk space, processes, ipconfig, etc.), or provide an explicit `command`."
            )

    blocked, reason = _is_blocked_command(command, trusted=hardcoded)
    if blocked:
        return f"Blocked for safety: {reason}"

    shell = str(params.get("shell", "cmd") or "cmd").strip().lower()
    if shell not in ("cmd", "powershell"):
        return "Invalid shell. Use cmd or powershell."
    timeout = _clamp(params.get("timeout", 30), 1, 120, 30)
    cwd = str(params.get("cwd", "") or "").strip()
    if cwd and not Path(cwd).expanduser().is_dir():
        return f"Invalid working directory: {cwd}"
    if cwd:
        cwd = str(Path(cwd).expanduser().resolve())

    dry_run = _as_bool(params.get("dry_run"), False)
    if dry_run:
        return f"Dry run: shell={shell} | cwd={cwd or '(home)'} | timeout={timeout}s | command={command}"

    if player:
        try:
            player.write_log(f"[CMD] {command[:60]}")
        except Exception:
            pass

    if any(token in command.lower() for token in ("notepad", "explorer", "start ")):
        if not hardcoded:
            return "Blocked for safety: opening applications requires an approved task."
        try:
            subprocess.Popen(["cmd", "/c", command],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Opened: {command}"
        except Exception as exc:
            return f"Could not open: {exc}"

    if _as_bool(params.get("visible"), True):
        _run_visible(command)
        return f"Terminal opened with command: {command[:80]}"
    return _run_silent(command, timeout, shell, cwd)
