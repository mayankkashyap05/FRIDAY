# actions/reminder.py
# Friday — full reminder lifecycle via Windows Task Scheduler.
#
# Every schtasks call is guarded, every generated payload is escaped with
# json.dumps/xml_escape, and non-Windows platforms get an honest message.

from __future__ import annotations

import csv
import io
import json
import os
import platform
import re
import secrets
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape


TASK_PREFIX = "FridayReminder_"


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _platform() -> str:
    return platform.system()


def _now() -> datetime:
    return datetime.now()


def _temp_dir() -> Path:
    return Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")


def _python_executable() -> str:
    exe = sys.executable or "python"
    if exe.lower().endswith("python.exe"):
        pyw = exe.replace("python.exe", "pythonw.exe")
        if os.path.exists(pyw):
            return pyw
    return exe


def _parse_clock(text: str, reference: datetime) -> datetime:
    raw = str(text or "").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})\s*(am|pm)?", raw, re.IGNORECASE)
    if not match:
        raise ValueError("unrecognised time")
    hour, minute = int(match.group(1)), int(match.group(2))
    suffix = (match.group(3) or "").lower()
    if suffix:
        if not 1 <= hour <= 12:
            raise ValueError("invalid 12-hour time")
        hour = hour % 12 + (12 if suffix == "pm" else 0)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("invalid time")
    return reference.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _relative_delta(text: str) -> timedelta | None:
    match = re.fullmatch(
        r"in\s+(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)",
        str(text or "").strip().lower())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit.startswith(("s", "sec")):
        return timedelta(seconds=amount)
    if unit.startswith(("m", "min")):
        return timedelta(minutes=amount)
    return timedelta(hours=amount)


def _parse_reminder_time(params: dict) -> datetime:
    """Resolve date/time, relative delays and natural language into a datetime."""
    params = params if isinstance(params, dict) else {}
    now = _now().replace(microsecond=0)

    for key, unit in (("in_seconds", "seconds"), ("in_minutes", "minutes"),
                      ("in_hours", "hours")):
        if params.get(key) is not None:
            try:
                amount = float(params[key])
            except (TypeError, ValueError):
                raise ValueError(f"invalid {key}")
            return now + timedelta(**{unit: amount})

    when = str(params.get("when") or params.get("time") or "").strip()
    if not when and params.get("date"):
        raise ValueError("a time is required")

    delta = _relative_delta(when)
    if delta is not None:
        return now + delta

    lowered = when.lower()
    if lowered in ("now", "right now"):
        return now + timedelta(minutes=1)

    iso = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[T ](\d{1,2}:\d{2}(?::\d{2})?)", when)
    if iso:
        return datetime.strptime(f"{iso.group(1)} {iso.group(2)[:5]}", "%Y-%m-%d %H:%M")

    if lowered.startswith(("tomorrow", "today")):
        word, _, rest = when.partition(" ")
        target = _parse_clock(rest, now)
        if word.lower() == "tomorrow":
            target += timedelta(days=1)
        elif target <= now:
            target += timedelta(days=1)
        return target

    if params.get("date"):
        date_str = str(params["date"]).strip()
        if lowered:
            try:
                target = _parse_clock(when, now)
            except ValueError:
                target = datetime.strptime(f"{date_str} {when}", "%Y-%m-%d %H:%M")
                return target
            day = datetime.strptime(date_str, "%Y-%m-%d")
            return day.replace(hour=target.hour, minute=target.minute)
        return datetime.strptime(date_str, "%Y-%m-%d")

    if lowered:
        return _parse_clock(when, now)
    raise ValueError("I need both a date and a time to set a reminder.")


def _recurrence(params: dict) -> str:
    value = str(params.get("repeat") or params.get("recurrence") or "once").strip().lower()
    if value in ("none", "false", "no", "0"):
        return "once"
    if value.startswith("week"):
        return "weekly"
    if value.startswith("day"):
        return "daily"
    return "once" if value in ("once", "one_time", "") else value


def _task_name(target: datetime) -> str:
    return f"{TASK_PREFIX}{target.strftime('%Y%m%d_%H%M')}_{secrets.token_hex(2)}"


def _notification_script(message: str, project_root: str) -> str:
    """json.dumps makes quotes, newlines, braces and backslashes harmless."""
    return (
        "import sys, os, time, json\n"
        f"sys.path.insert(0, {json.dumps(project_root)})\n"
        f"MESSAGE = {json.dumps(message, ensure_ascii=False)}\n"
        "try:\n"
        "    import winsound\n"
        "    for freq in [800, 1000, 1200]:\n"
        "        winsound.Beep(freq, 200)\n"
        "        time.sleep(0.1)\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        "    from winotify import Notification\n"
        "    Notification(app_id='Friday', title='Friday Reminder', msg=MESSAGE,\n"
        "                 duration='long').show()\n"
        "except Exception:\n"
        "    try:\n"
        "        import subprocess\n"
        "        subprocess.run(['msg', '*', '/TIME:30', MESSAGE])\n"
        "    except Exception:\n"
        "        pass\n"
        "time.sleep(3)\n"
        "try:\n"
        "    os.remove(__file__)\n"
        "except Exception:\n"
        "    pass\n"
    )


def _trigger_xml(target: datetime, recurrence: str) -> str:
    boundary = xml_escape(target.strftime("%Y-%m-%dT%H:%M:%S"))
    if recurrence == "daily":
        schedule = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    elif recurrence == "weekly":
        day = target.strftime("%A")
        schedule = ("<ScheduleByWeek><WeeksInterval>1</WeeksInterval><DaysOfWeek>"
                    f"<{day}/></DaysOfWeek></ScheduleByWeek>")
    else:
        return (f"<TimeTrigger><StartBoundary>{boundary}</StartBoundary>"
                "<Enabled>true</Enabled></TimeTrigger>")
    return (f"<CalendarTrigger><StartBoundary>{boundary}</StartBoundary>"
            f"<Enabled>true</Enabled>{schedule}</CalendarTrigger>")


def _task_xml(target: datetime, script_path: Path, python_exe: str,
              message: str, recurrence: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
        '<RegistrationInfo><Description>Friday Reminder: '
        f'{xml_escape(message)}</Description></RegistrationInfo>'
        f'<Triggers>{_trigger_xml(target, recurrence)}</Triggers>'
        '<Actions><Exec>'
        f'<Command>{xml_escape(python_exe)}</Command>'
        f'<Arguments>&quot;{xml_escape(str(script_path))}&quot;</Arguments>'
        '</Exec></Actions>'
        '<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>'
        '<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>'
        '<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>'
        '<StartWhenAvailable>true</StartWhenAvailable><WakeToRun>true</WakeToRun>'
        '<ExecutionTimeLimit>PT5M</ExecutionTimeLimit><Enabled>true</Enabled></Settings>'
        '<Principals><Principal><LogonType>InteractiveToken</LogonType>'
        '<RunLevel>LeastPrivilege</RunLevel></Principal></Principals></Task>'
    )


def _run_schtasks(args: list[str]):
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def _message_from_script(path: Path) -> str:
    try:
        match = re.search(r"^MESSAGE = (.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            return str(json.loads(match.group(1)))
    except Exception:
        pass
    return ""


def _query_tasks() -> list[dict]:
    """Parse `schtasks /Query /FO CSV /V` into Friday reminder rows."""
    result = _run_schtasks(["/Query", "/FO", "CSV", "/V"])
    if result.returncode != 0:
        return []
    rows = []
    reader = csv.reader(io.StringIO(result.stdout or ""))
    header = None
    for row in reader:
        if not row:
            continue
        if header is None:
            header = [cell.strip().lower() for cell in row]
            continue
        record = dict(zip(header, row))
        name = str(record.get("taskname") or record.get("task name") or "").strip().strip('"')
        if not name.startswith(TASK_PREFIX):
            continue
        args = str(record.get("arguments") or "").strip().strip('"').strip('"')
        script = Path(args.replace('"', ""))
        rows.append({
            "task_name": name,
            "next_run": str(record.get("next run time") or record.get("next_run_time") or ""),
            "script": script,
            "message": _message_from_script(script),
        })
    return rows


def _delete_task(task_name: str, script: Path | None = None) -> bool:
    result = _run_schtasks(["/Delete", "/TN", task_name, "/F"])
    success = result.returncode == 0
    if success and script:
        try:
            Path(script).unlink(missing_ok=True)
        except Exception:
            pass
    return success


def _format_task(row: dict) -> str:
    when = row.get("next_run") or "time unknown"
    message = row.get("message") or row.get("task_name")
    return f"- {row['task_name']} — {when}: {message}"


def reminder(
    parameters: dict,
    response: str | None = None,
    player=None,
    session_memory=None,
) -> str:
    """Set, list, delete or clear Friday's Task Scheduler reminders."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "set").strip().lower()

    if _platform() != "Windows":
        return "Windows Task Scheduler reminders are only available on Windows, sir."

    if action in ("list", "show", "all"):
        rows = _query_tasks()
        if not rows:
            return "You have no pending reminders, sir."
        return "Pending reminders:\n" + "\n".join(_format_task(row) for row in rows[:30])

    if action in ("delete", "cancel", "remove", "clear", "clear_all"):
        rows = _query_tasks()
        if not rows:
            return "You have no pending reminders to remove, sir."
        target = str(params.get("task_name") or params.get("id") or "").strip().lower()
        keyword = str(params.get("message") or params.get("query") or "").strip().lower()
        clear_all = action in ("clear", "clear_all") or params.get("all") is True
        removed = []
        for row in rows:
            name = row["task_name"]
            haystack = f"{name} {row.get('message','')} {row.get('next_run','')}".lower()
            match = (clear_all or name.lower() == target or
                     (target and target in name.lower()) or
                     (keyword and keyword in haystack))
            if match and _delete_task(name, row.get("script")):
                removed.append(name)
        if not removed:
            return "I couldn't find a matching reminder, sir."
        if clear_all:
            return f"Cleared {len(removed)} reminder(s), sir."
        return f"Removed {', '.join(removed)}, sir."

    if action not in ("set", "add", "create"):
        return f"Unknown reminder action '{action}', sir."

    message = str(params.get("message") or "Reminder").strip()[:500] or "Reminder"
    recurrence = _recurrence(params)
    try:
        target = _parse_reminder_time(params)
    except ValueError as e:
        return f"I couldn't understand that reminder time, sir: {e}"

    if recurrence == "once" and target <= _now():
        return "That time is already in the past, sir."

    task_name = _task_name(target)
    temp_dir = _temp_dir()
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    script_path = temp_dir / f"{task_name}.pyw"
    xml_path = temp_dir / f"{task_name}.xml"
    project_root = str(Path(__file__).resolve().parent.parent)

    try:
        script_path.write_text(_notification_script(message, project_root), encoding="utf-8")
        xml_path.write_text(
            _task_xml(target, script_path, _python_executable(), message, recurrence),
            encoding="utf-16",
        )
        result = _run_schtasks(["/Create", "/TN", task_name, "/XML", str(xml_path), "/F"])
    except Exception as e:
        for path in (xml_path, script_path):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        return f"Something went wrong while scheduling the reminder: {str(e)[:80]}"

    try:
        xml_path.unlink(missing_ok=True)
    except Exception:
        pass

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        print(f"[Reminder] schtasks failed: {err}")
        try:
            script_path.unlink(missing_ok=True)
        except Exception:
            pass
        return "I couldn't schedule the reminder due to a system error, sir."

    if player:
        try:
            player.write_log(f"[reminder] {task_name} at {target:%Y-%m-%d %H:%M}")
        except Exception:
            pass
    when = target.strftime("%B %d at %I:%M %p")
    suffix = f" ({recurrence})" if recurrence != "once" else ""
    return f"Reminder set for {when}{suffix}, sir."
