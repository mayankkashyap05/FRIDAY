# actions/task_manager.py
# Friday — unified background agent task control + OS process management.
#
# All destructive process actions stay behind safety.py's Risk.HIGH gate. This
# module adds its own critical-process blocklist, self-protection and
# multi-match guard so an approved command still cannot kill the OS.

from __future__ import annotations

import os
import time
from collections import Counter

from agent.task_queue import get_queue

try:
    import psutil
    _PSUTIL = True
except ImportError:                                    # pragma: no cover - environment
    psutil = None
    _PSUTIL = False

CRITICAL_NAMES = {
    "system", "system idle process", "registry", "memory compression",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe", "ntoskrnl.exe",
    "init", "systemd", "kernel_task", "launchd",
}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"pending", "running"}


def _need_psutil() -> bool:
    return _PSUTIL and psutil is not None


def _limit(params: dict, key: str, default: int, maximum: int) -> int:
    try:
        value = int(params.get(key, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(maximum, value))


def _ps_error(*names):
    for name in names:
        error = getattr(psutil, name, None)
        if isinstance(error, type) and issubclass(error, Exception):
            return error
    return Exception


def _proc_info(proc) -> dict | None:
    info = None
    if isinstance(proc, dict):
        info = dict(proc)
    else:
        info = getattr(proc, "info", None)
        if not isinstance(info, dict):
            try:
                info = proc.as_dict([
                    "pid", "name", "status", "cpu_percent",
                    "memory_info", "create_time",
                ])
            except Exception:
                info = {}
    if not isinstance(info, dict):
        return None
    try:
        pid = int(info.get("pid") if info.get("pid") is not None else proc.pid)
    except Exception:
        return None
    name = str(info.get("name") or getattr(proc, "name", lambda: "")() or "")
    memory = info.get("memory_info")
    rss = getattr(memory, "rss", None)
    if rss is None and isinstance(memory, dict):
        rss = memory.get("rss")
    try:
        rss = float(rss or 0)
    except (TypeError, ValueError):
        rss = 0.0
    try:
        cpu = float(info.get("cpu_percent") or 0)
    except (TypeError, ValueError):
        cpu = 0.0
    create_time = info.get("create_time") or 0
    try:
        uptime = max(0.0, time.time() - float(create_time)) if create_time else 0.0
    except (TypeError, ValueError):
        uptime = 0.0
    return {
        "pid": pid, "name": name,
        "status": str(info.get("status") or "running"),
        "cpu": cpu, "rss_mb": rss / (1024 * 1024), "uptime": uptime,
    }


def _uptime_text(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes >= 60:
        return f"{minutes // 60}h {minutes % 60}m"
    return f"{minutes}m"


def _iter_processes():
    attrs = ["pid", "name", "status", "cpu_percent", "memory_info", "create_time"]
    try:
        return list(psutil.process_iter(attrs))
    except TypeError:
        return list(psutil.process_iter())


def _process_rows(params: dict, limit: int) -> list[dict]:
    sort_by = str(params.get("sort_by", "memory")).strip().lower()
    by_cpu = sort_by in ("cpu", "cpu_percent", "cpu%")
    if by_cpu:
        # Prime psutil's internal CPU sampler with a short interval.
        try:
            psutil.cpu_percent(interval=0.1)
        except Exception:
            time.sleep(0.1)
    rows = []
    for proc in _iter_processes():
        info = _proc_info(proc)
        if info:
            if by_cpu:
                try:
                    info["cpu"] = float(proc.cpu_percent(interval=None) or 0)
                except Exception:
                    pass
            rows.append(info)
    if by_cpu:
        rows.sort(key=lambda row: row["cpu"], reverse=True)
    else:
        rows.sort(key=lambda row: row["rss_mb"], reverse=True)
    return rows[:limit]


def _format_process(row: dict) -> str:
    return (
        f"PID {row['pid']} {row['name']} — {row['cpu']:.1f}% CPU, "
        f"{row['rss_mb']:.0f} MB RAM"
    )


def _agent_list(params: dict) -> str:
    tasks = get_queue().get_all_statuses() or []
    status_filter = str(params.get("status_filter", "")).strip().lower()
    if status_filter:
        tasks = [task for task in tasks
                 if str(task.get("status", "")).lower() == status_filter]
    limit = _limit(params, "limit", 10, 50)
    if not tasks:
        return "No background tasks, sir."
    counts = Counter(str(task.get("status", "unknown")) for task in tasks)
    summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    lines = [
        f"{task.get('task_id', '?')}: {task.get('status', '?')} — "
        f"{str(task.get('goal', ''))[:70]}"
        for task in tasks[-limit:]
    ]
    return f"Tasks ({summary}):\n" + "\n".join(lines)


def _agent_status(params: dict) -> str:
    queue = get_queue()
    task_id = str(params.get("task_id", "")).strip()
    if not task_id or task_id.lower() in ("latest", "last", "current"):
        tasks = queue.get_all_statuses() or []
        if not tasks:
            return "There are no background tasks yet, sir."
        task_id = str(tasks[-1].get("task_id", ""))
    info = queue.get_status(task_id)
    if not info:
        return f"No task found with ID {task_id}, sir."
    status = str(info.get("status", "")).lower()
    goal = str(info.get("goal", ""))
    if status == "completed" and info.get("result"):
        return f"Task {task_id} completed. Goal: {goal}. Result: {info['result']}"
    if status == "failed":
        return f"Task {task_id} failed. {info.get('error', '')}".strip()
    if status == "cancelled":
        return f"Task {task_id} was cancelled. Goal: {goal}"
    return f"Task {task_id} is {status}. Goal: {goal}"


def _agent_cancel(params: dict) -> str:
    queue = get_queue()
    task_id = str(params.get("task_id", "")).strip()
    cancel_all = bool(params.get("cancel_all")) or task_id.lower() in ("all", "*")
    if cancel_all:
        candidates = [str(task.get("task_id", "")) for task in
                      (queue.get_all_statuses() or [])
                      if str(task.get("status", "")).lower() in ACTIVE_STATUSES]
        cancelled = sum(1 for tid in candidates if tid and queue.cancel(tid))
        return f"Cancelled {cancelled} background task(s), sir."
    if not task_id:
        return "Please provide a task_id to cancel, sir."
    if queue.cancel(task_id):
        return f"Task {task_id} cancelled."
    return f"Could not cancel task {task_id}. It may be already finished."


def _summary(params: dict) -> str:
    tasks = get_queue().get_all_statuses() or []
    counts = Counter(str(task.get("status", "unknown")) for task in tasks)
    parts = [f"tasks: {len(tasks)} total"]
    for status in ("pending", "running", "completed", "failed", "cancelled"):
        parts.append(f"{counts.get(status, 0)} {status}")
    if _need_psutil():
        try:
            top = _process_rows({"sort_by": "memory"}, 5)
            parts.append("top processes: " + "; ".join(_format_process(row) for row in top))
        except Exception:
            parts.append("top processes: unavailable")
    else:
        parts.append("top processes: install psutil to enable")
    return "Summary — " + ". ".join(parts) + "."


def _process_list(params: dict) -> str:
    if not _need_psutil():
        return "Process inspection requires psutil. Run: pip install psutil"
    limit = _limit(params, "limit", 10, 30)
    rows = _process_rows(params, limit)
    if not rows:
        return "No running processes could be read, sir."
    sort_by = str(params.get("sort_by", "memory")).lower()
    label = "CPU" if sort_by.startswith("cpu") else "RAM"
    return f"Top {len(rows)} processes by {label}:\n" + "\n".join(
        _format_process(row) for row in rows
    )


def _find_process(params: dict) -> str:
    if not _need_psutil():
        return "Process inspection requires psutil. Run: pip install psutil"
    needle = str(params.get("process") or params.get("name") or "").strip().lower()
    pid_value = params.get("pid")
    if not needle and pid_value in (None, ""):
        return "Tell me a process name or PID to look for, sir."
    rows = []
    for proc in _iter_processes():
        info = _proc_info(proc)
        if info:
            rows.append(info)
    if pid_value not in (None, ""):
        try:
            wanted = int(pid_value)
        except (TypeError, ValueError):
            return "That PID is not a number, sir."
        matches = [row for row in rows if row["pid"] == wanted]
    else:
        matches = [row for row in rows if needle in row["name"].lower()]
    if not matches:
        return f"No running process matching '{needle or pid_value}', sir."
    lines = [
        f"PID {row['pid']} {row['name']} ({row['status']}) — {row['cpu']:.1f}% CPU, "
        f"{row['rss_mb']:.0f} MB RAM, uptime {_uptime_text(row['uptime'])}"
        for row in matches[:10]
    ]
    return "\n".join(lines)


def _is_critical(row: dict) -> bool:
    name = str(row.get("name", "")).strip().lower()
    base = name.replace(".exe", "")
    return (
        int(row.get("pid", 0) or 0) <= 4
        or name in CRITICAL_NAMES
        or base in CRITICAL_NAMES
    )


def _protected_pids() -> set[int]:
    pids = {int(os.getpid())}
    try:
        pids.add(int(os.getppid()))
    except (AttributeError, OSError, ValueError):
        pass
    return pids


def _kill_process(params: dict) -> str:
    if not _need_psutil():
        return "Process control requires psutil. Run: pip install psutil"

    pid_value = params.get("pid")
    needle = str(params.get("process") or params.get("name") or "").strip().lower()
    if pid_value in (None, "") and not needle:
        return "Tell me the PID or process name to end, sir."

    rows = []
    for proc in _iter_processes():
        info = _proc_info(proc)
        if info:
            rows.append(info)
    protected = _protected_pids()

    if pid_value not in (None, ""):
        try:
            wanted = int(pid_value)
        except (TypeError, ValueError):
            return "That PID is not a number, sir."
        matches = [row for row in rows if row["pid"] == wanted]
        if not matches:
            try:
                psutil.Process(wanted)
                matches = [{"pid": wanted, "name": "process", "status": "running",
                            "cpu": 0.0, "rss_mb": 0.0, "uptime": 0.0}]
            except Exception:
                return f"No process with PID {wanted}, sir."
    else:
        matches = [row for row in rows if needle in row["name"].lower()]
        exact = [row for row in matches
                 if row["name"].lower() == needle
                 or row["name"].lower().removesuffix(".exe") == needle]
        if exact:
            matches = exact
        elif len({row["name"].lower() for row in matches}) > 1 \
                and not bool(params.get("all_matches")):
            listing = ", ".join(
                f"{row['pid']}:{row['name']}" for row in matches[:12]
            )
            return (
                f"'{needle}' matches several different apps: {listing}. "
                "Please specify the exact process name or PID, sir."
            )
    if not matches:
        return f"No running process matching '{needle or pid_value}', sir."

    blocked = [row for row in matches
               if _is_critical(row) or row["pid"] in protected]
    if blocked:
        names = ", ".join(f"{row['pid']}:{row['name']}" for row in blocked[:5])
        return f"I won't end critical or FRIDAY-owned processes: {names}, sir."

    no_such = _ps_error("NoSuchProcess")
    denied = _ps_error("AccessDenied")
    force = bool(params.get("force", True))
    terminated, failed, messages = [], [], []
    handles = []
    for row in matches:
        try:
            handles.append((row, psutil.Process(row["pid"])))
        except no_such:
            messages.append(f"{row['pid']} already exited")
        except Exception as exc:
            failed.append(f"{row['name']} ({exc})")
    for row, proc in handles:
        try:
            proc.terminate()
            terminated.append(row)
        except no_such:
            messages.append(f"{row['pid']} already exited")
        except denied:
            failed.append(f"{row['name']} (access denied)")
        except Exception as exc:
            failed.append(f"{row['name']} ({exc})")

    if handles and force:
        try:
            _, survivors = psutil.wait_procs(
                [proc for _, proc in handles], timeout=2.0
            )
        except Exception:
            survivors = []
        for proc in survivors:
            try:
                proc.kill()
            except Exception:
                pass

    parts = [f"Ended {len(terminated)} process(es): " + ", ".join(
        f"{row['pid']}:{row['name']}" for row in terminated
    )] if terminated else []
    if messages:
        parts.append("; ".join(messages))
    if failed:
        parts.append("could not end: " + "; ".join(failed))
    return (" sir." if not parts else "; ".join(parts) + ". ") + (
        "Done, sir." if terminated else "Nothing was ended, sir."
    )


def task_manager(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Query or control background agent tasks and OS processes."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action", "list")).strip().lower()

    if action in ("list", "all", "tasks"):
        return _agent_list(params)
    if action in ("status", "check"):
        return _agent_status(params)
    if action in ("cancel", "stop", "abort"):
        return _agent_cancel(params)
    if action in ("summary", "overview"):
        return _summary(params)
    if action in ("processes", "list_processes", "top", "ps"):
        return _process_list(params)
    if action in ("find_process", "search_process", "process_info"):
        return _find_process(params)
    if action in ("kill", "end", "kill_process", "terminate"):
        return _kill_process(params)
    return (
        f"Unknown task action: {action}, sir. Use list | status | cancel | "
        "summary | processes | find_process | kill"
    )
