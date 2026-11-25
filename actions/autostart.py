"""Cross-platform FRIDAY login startup management.

Windows uses either the per-user Run key or a delayed Task Scheduler logon
trigger. macOS and Linux use their native per-user startup file formats.
"""
from __future__ import annotations

import getpass
import os
import platform
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

try:  # Windows-only; keeping the import guarded makes this module portable.
    import winreg  # type: ignore
except ImportError:  # pragma: no cover - exercised on non-Windows hosts
    winreg = None

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "FridayAssistant"
TASK_NAME = "FridayAutostart"


def get_base_dir() -> Path:
    """Return the repository/install directory for source and frozen builds."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _python_executable() -> Path:
    """Prefer pythonw.exe from the active interpreter's exact environment."""
    active = Path(sys.executable).absolute()
    if platform.system() == "Windows" and not getattr(sys, "frozen", False):
        candidate = active.with_name("pythonw.exe")
        if candidate.is_file():
            return candidate
    return active


def _launch_parts(minimized: bool = False) -> tuple[str, str, str]:
    base = get_base_dir()
    if getattr(sys, "frozen", False):
        executable, arguments = str(Path(sys.executable).resolve()), ""
    else:
        executable = str(_python_executable())
        main_path = str((base / "main.py").resolve())
        arguments = f'-u "{main_path}"'
    if minimized:
        arguments = (arguments + " --minimized").strip()
    return executable, arguments, str(base.resolve())


def _launch_command(minimized: bool = False) -> str:
    executable, arguments, _ = _launch_parts(minimized)
    return f'"{executable}"' + (f" {arguments}" if arguments else "")


def _windows_available() -> bool:
    return platform.system() == "Windows" and winreg is not None


def _set_run(cmd: str) -> None:
    if not _windows_available():
        raise OSError("Windows Registry startup is only available on Windows.")
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, cmd)


def _del_run() -> bool:
    if not _windows_available():
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


def _get_run() -> str | None:
    if not _windows_available():
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            return winreg.QueryValueEx(key, RUN_VALUE_NAME)[0]
    except FileNotFoundError:
        return None


def _run_schtasks(*args: str, check: bool = True, **kwargs):
    return subprocess.run(["schtasks", *args], capture_output=True, text=True,
                          check=check, **kwargs)


def _delete_task() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        result = _run_schtasks("/Delete", "/TN", TASK_NAME, "/F", check=False)
        return result.returncode == 0
    except OSError:
        return False


def _task_xml(delay_seconds: int, minimized: bool = False) -> str:
    executable, arguments, working_dir = _launch_parts(minimized)
    delay = max(5, min(300, int(delay_seconds)))
    username = os.environ.get("USERNAME") or getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    user_id = f"{domain}\\{username}" if domain else username
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled><Delay>PT{delay}S</Delay></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{escape(user_id)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><StartWhenAvailable>true</StartWhenAvailable><Enabled>true</Enabled></Settings>
  <Actions Context="Author"><Exec><Command>{escape(executable)}</Command><Arguments>{escape(arguments)}</Arguments><WorkingDirectory>{escape(working_dir)}</WorkingDirectory></Exec></Actions>
</Task>'''


def _set_task(delay_seconds: int, minimized: bool = False) -> None:
    if platform.system() != "Windows":
        raise OSError("Task Scheduler startup is only available on Windows.")
    xml_text = _task_xml(delay_seconds, minimized)
    fd, xml_path = tempfile.mkstemp(prefix="friday-autostart-", suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as stream:
            stream.write(xml_text)
        _run_schtasks("/Create", "/TN", TASK_NAME, "/XML", xml_path, "/F")
    finally:
        try:
            os.unlink(xml_path)
        except OSError:
            pass


def _get_task_xml() -> str | None:
    if platform.system() != "Windows":
        return None
    try:
        result = _run_schtasks("/Query", "/TN", TASK_NAME, "/XML", check=False)
        return result.stdout if result.returncode == 0 else None
    except OSError:
        return None


def _write_non_windows(method: str, minimized: bool = False) -> Path:
    executable, arguments, working_dir = _launch_parts(minimized)
    if method == "launch_agent":
        import plistlib
        path = Path.home() / "Library" / "LaunchAgents" / "com.friday.assistant.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"Label": "com.friday.assistant", "ProgramArguments": [executable] +
                   (shlex.split(arguments) if arguments else []),
                   "WorkingDirectory": working_dir, "RunAtLoad": True}
        path.write_bytes(plistlib.dumps(payload))
    else:
        path = Path.home() / ".config" / "autostart" / "friday.desktop"
        path.parent.mkdir(parents=True, exist_ok=True)
        command = f'"{executable}"' + (f" {arguments}" if arguments else "")
        path.write_text("[Desktop Entry]\nType=Application\nName=FRIDAY\n"
                        f"Exec={command}\nPath={working_dir}\nX-GNOME-Autostart-enabled=true\n",
                        encoding="utf-8")
    return path


def _non_windows_path(method: str) -> Path:
    if method == "launch_agent":
        return Path.home() / "Library" / "LaunchAgents" / "com.friday.assistant.plist"
    return Path.home() / ".config" / "autostart" / "friday.desktop"


def _remove_non_windows() -> bool:
    removed = False
    for method in ("launch_agent", "xdg_autostart"):
        try:
            path = _non_windows_path(method)
            if path.exists():
                path.unlink()
                removed = True
        except OSError:
            pass
    return removed


def _registered_status() -> tuple[str | None, str | None, int | None]:
    """Return (method, command, delay), without claiming stale entries are valid."""
    if _windows_available():
        command = _get_run()
        if command is not None:
            return "registry", command, 0
        xml = _get_task_xml()
        if xml:
            import html
            import re
            command_match = re.search(r"<Command>(.*?)</Command>", xml, re.S)
            args_match = re.search(r"<Arguments>(.*?)</Arguments>", xml, re.S)
            delay_match = re.search(r"<Delay>PT(\d+)S</Delay>", xml)
            command = html.unescape(command_match.group(1)) if command_match else ""
            args = html.unescape(args_match.group(1)) if args_match else ""
            return "task_scheduler", f'"{command}" {args}'.strip(), int(delay_match.group(1)) if delay_match else None
        return None, None, None
    system = platform.system()
    methods = (["launch_agent"] if system == "Darwin" else ["xdg_autostart"])
    for method in methods:
        path = _non_windows_path(method)
        if path.is_file():
            try:
                if method == "launch_agent":
                    import plistlib
                    payload = plistlib.loads(path.read_bytes())
                    command = shlex.join(payload.get("ProgramArguments", []))
                else:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    command = next((line.partition("=")[2] for line in text.splitlines()
                                    if line.startswith("Exec=")), "")
                return method, command, None
            except (OSError, ValueError, TypeError):
                return method, "", None
    return None, None, None


def _path_health(method: str, command: str) -> tuple[bool, str]:
    """Check registered executable/script against the current install location."""
    base = get_base_dir().resolve()
    try:
        parts = shlex.split(command, posix=platform.system() != "Windows")
        parts = [part.strip('"') for part in parts]
        executable = Path(parts[0]) if parts else Path("")
        if not executable.exists():
            return False, f"registered executable is missing: {executable}"
        if not getattr(sys, "frozen", False):
            script = next((Path(p) for p in parts[1:] if p.lower().endswith("main.py")), None)
            expected = (base / "main.py").resolve()
            if script is None or not script.exists() or script.resolve() != expected:
                return False, f"registered main.py path is stale (expected {expected})"
        return True, "registered executable and application path are valid"
    except (OSError, ValueError, IndexError) as exc:
        return False, f"could not validate registered command: {exc}"


def _parse_bool(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "1", "on", "minimized", "minimise", "minimize"):
            return True
        if text in ("false", "no", "0", "off", "normal", ""):
            return False
    return bool(value)


def autostart_action(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Manage FRIDAY autostart: status, enable, disable, repair, or update."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action", "status") or "status").strip().lower()
    try:
        if action in ("status", "check"):
            method, command, delay = _registered_status()
            if not method:
                return "FRIDAY autostart is disabled."
            healthy, detail = _path_health(method, command or "")
            state = "valid" if healthy else f"STALE/BROKEN — {detail}"
            delay_text = f", delay {delay}s" if delay else ""
            return (f"FRIDAY autostart enabled via {method}{delay_text}; {state}. "
                    f"Registered command: {command}")

        if action in ("enable", "on", "add", "repair", "update"):
            is_repair = action in ("repair", "update")
            previous, previous_cmd, previous_delay = _registered_status() if is_repair else (None, None, None)
            try:
                requested_delay = int(params.get("delay_seconds", previous_delay or 0) or 0)
            except (TypeError, ValueError):
                return "delay_seconds must be an integer from 0 to 300."
            if not 0 <= requested_delay <= 300:
                return "delay_seconds must be an integer from 0 to 300."
            requested_method = str(params.get("method", "auto") or "auto").strip().lower()
            if is_repair and "method" not in params and previous:
                requested_method = previous
            if is_repair and "delay_seconds" not in params and previous_delay is not None:
                requested_delay = previous_delay
            minimized = _parse_bool(params.get("minimized"), False)
            system = platform.system()
            if system == "Windows":
                if requested_method == "auto":
                    requested_method = "task_scheduler" if requested_delay else "registry"
                if requested_method not in ("registry", "task_scheduler"):
                    return "Windows method must be auto, registry, or task_scheduler."
                if requested_method == "task_scheduler" or requested_delay > 0:
                    _del_run()
                    delay = max(5, min(300, requested_delay or 5))
                    _set_task(delay, minimized)
                    return f"FRIDAY autostart enabled via Task Scheduler with {delay}s delay."
                _delete_task()
                _set_run(_launch_command(minimized))
                return "FRIDAY autostart enabled via the per-user Registry Run key."
            method = "launch_agent" if system == "Darwin" else "xdg_autostart"
            _write_non_windows(method, minimized)
            return f"FRIDAY autostart enabled via {method}."

        if action in ("disable", "off", "remove", "delete"):
            removed = False
            if _windows_available():
                removed = _del_run() or removed
                removed = _delete_task() or removed
            else:
                removed = _remove_non_windows()
            return "FRIDAY autostart disabled; all startup hooks removed." if removed else "FRIDAY autostart was already disabled."
        return f"Unknown autostart action: {action}. Use status | enable | disable | repair."
    except Exception as exc:
        return f"Autostart operation failed: {exc}"
