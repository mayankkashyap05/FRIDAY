"""Fully offline combinatorial tests for actions.task_manager.

No real process is ever signalled: psutil and the agent queue are faked.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import safety
from actions import task_manager as tm


class FakeQueue:
    def __init__(self, statuses):
        self.statuses = statuses
        self.cancelled = []

    def get_all_statuses(self):
        return [dict(item) for item in self.statuses]

    def get_status(self, task_id):
        for item in self.statuses:
            if item["task_id"] == task_id:
                return dict(item)
        return None

    def cancel(self, task_id):
        if any(item["task_id"] == task_id for item in self.statuses):
            self.cancelled.append(task_id)
            return True
        return False


class FakeProc:
    def __init__(self, pid, name, cpu=1.0, rss=10 * 1024 * 1024,
                 status="running", age=300):
        self.info = {
            "pid": pid, "name": name, "status": status,
            "cpu_percent": cpu,
            "memory_info": SimpleNamespace(rss=rss),
            "create_time": 0,
        }
        self.terminate_calls = 0
        self.kill_calls = 0
        self.terminate_error = None
        self.age = age

    def terminate(self):
        if self.terminate_error:
            raise self.terminate_error
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1

    def cpu_percent(self, interval=None):
        return self.info["cpu_percent"]


def _task(tid, status, goal=None):
    return {
        "task_id": tid, "status": status,
        "goal": goal or f"goal {tid}", "result": None, "error": "",
    }


@pytest.fixture
def queue(monkeypatch):
    tasks = [
        _task("a1", "pending"), _task("b2", "running"),
        _task("c3", "completed"), _task("d4", "failed"),
        _task("e5", "cancelled"),
    ]
    fake = FakeQueue(tasks)
    monkeypatch.setattr(tm, "get_queue", lambda: fake)
    return fake


@pytest.fixture
def processes(monkeypatch):
    procs = [
        FakeProc(101, "chrome.exe", cpu=5.0, rss=900 * 1024 * 1024),
        FakeProc(102, "notepad.exe", cpu=1.0, rss=20 * 1024 * 1024),
        FakeProc(103, "python.exe", cpu=25.0, rss=300 * 1024 * 1024),
    ]
    by_pid = {proc.info["pid"]: proc for proc in procs}
    survivors = []

    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    fake = SimpleNamespace(
        process_iter=lambda attrs=None: list(procs),
        Process=lambda pid: by_pid[int(pid)],
        wait_procs=lambda targets, timeout: ([], survivors),
        cpu_percent=lambda interval=None: None,
        NoSuchProcess=NoSuchProcess,
        AccessDenied=AccessDenied,
    )
    monkeypatch.setattr(tm, "psutil", fake)
    monkeypatch.setattr(tm, "_PSUTIL", True)
    return SimpleNamespace(procs=procs, by_pid=by_pid, survivors=survivors,
                           fake=fake)


# ---------------------------------------------------------------------------
# Agent task matrix
# ---------------------------------------------------------------------------

def test_list_filters_and_limits(queue):
    result = tm.task_manager({"action": "list", "status_filter": "completed"})
    assert "c3" in result and "a1" not in result
    assert "1 completed" in result

    limited = tm.task_manager({"action": "tasks", "limit": 2})
    assert "a1" not in limited and "e5" in limited
    assert "1 pending" in limited.splitlines()[0]


def test_status_exact_latest_and_missing(queue):
    assert "goal b2" in tm.task_manager({"action": "status", "task_id": "b2"})
    assert "goal e5" in tm.task_manager({"action": "check", "task_id": "latest"})
    assert "goal e5" in tm.task_manager({"action": "status"})
    assert "No task found" in tm.task_manager({"action": "status", "task_id": "nope"})


def test_completed_status_reports_result(monkeypatch, queue):
    queue.statuses[2]["result"] = "all done"
    assert "all done" in tm.task_manager({"action": "status", "task_id": "c3"})


def test_cancel_and_cancel_all(queue):
    assert "cancelled" in tm.task_manager({"action": "cancel", "task_id": "a1"}).lower()
    assert queue.cancelled == ["a1"]
    result = tm.task_manager({"action": "cancel", "task_id": "all"})
    assert "Cancelled 2" in result
    assert set(queue.cancelled) == {"a1", "b2"}


def test_summary_combines_tasks_and_processes(queue, processes):
    result = tm.task_manager({"action": "summary"})
    assert "5 total" in result and "1 pending" in result
    assert "chrome.exe" in result


# ---------------------------------------------------------------------------
# OS process matrix
# ---------------------------------------------------------------------------

def test_processes_sort_by_memory_and_cpu(processes):
    by_memory = tm.task_manager({"action": "processes", "limit": 2})
    assert "chrome.exe" in by_memory.splitlines()[1]
    by_cpu = tm.task_manager({"action": "top", "sort_by": "cpu"})
    assert "python.exe" in by_cpu.splitlines()[1]
    assert len(by_cpu.splitlines()) == 4  # header + 3 processes


def test_process_limit_is_clamped(processes):
    result = tm.task_manager({"action": "top", "limit": 999})
    assert len(result.splitlines()) == 4


def test_find_process_by_name_and_pid(processes):
    by_name = tm.task_manager({"action": "find_process", "process": "NOTE"})
    assert "PID 101" not in by_name and "PID 102 notepad.exe" in by_name
    by_pid = tm.task_manager({"action": "process_info", "pid": 103})
    assert "PID 103 python.exe" in by_pid and "uptime" in by_pid
    assert "No running process" in tm.task_manager(
        {"action": "find_process", "name": "missing"}
    )


def test_kill_graceful_and_force_escalation(processes):
    result = tm.task_manager({"action": "kill", "pid": 102})
    assert "Ended 1 process" in result
    assert processes.by_pid[102].terminate_calls == 1

    processes.survivors.append(processes.by_pid[101])
    tm.task_manager({"action": "kill_process", "pid": 101, "force": True})
    assert processes.by_pid[101].kill_calls == 1


def test_no_force_does_not_escalate(processes):
    processes.survivors.append(processes.by_pid[103])
    tm.task_manager({"action": "end", "pid": 103, "force": False})
    assert processes.by_pid[103].kill_calls == 0


def test_access_denied_and_missing_process(processes):
    denied = processes.fake.AccessDenied("denied")
    processes.by_pid[102].terminate_error = denied
    assert "access denied" in tm.task_manager({"action": "kill", "pid": 102}).lower()

    fake = processes.fake
    fake.Process = lambda pid: (_ for _ in ()).throw(fake.NoSuchProcess())
    result = tm.task_manager({"action": "kill", "pid": 999})
    assert "already exited" in result or "No process" in result


# ---------------------------------------------------------------------------
# Security invariants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "lsass.exe", "csrss.exe", "svchost.exe", "System", "SYSTEM IDLE PROCESS",
    "smss.exe", "winlogon.exe", "dwm.exe", "init", "systemd",
])
def test_critical_process_names_are_blocked(processes, name):
    proc = FakeProc(900, name)
    processes.procs.append(proc)
    processes.by_pid[900] = proc
    result = tm.task_manager({"action": "kill", "process": name})
    assert "won't end critical" in result
    assert proc.terminate_calls == 0


@pytest.mark.parametrize("pid", [0, 1, 4])
def test_critical_pids_are_blocked(processes, pid):
    proc = FakeProc(pid, "harmless.exe")
    processes.procs.append(proc)
    processes.by_pid[pid] = proc
    result = tm.task_manager({"action": "kill", "pid": pid})
    assert "won't end critical" in result
    assert proc.terminate_calls == 0


def test_self_and_parent_pid_blocked(monkeypatch, processes):
    own = FakeProc(os.getpid(), "python.exe")
    processes.procs.append(own)
    processes.by_pid[own.info["pid"]] = own
    result = tm.task_manager({"action": "kill", "pid": os.getpid()})
    assert "won't end critical" in result
    assert own.terminate_calls == 0


def test_ambiguous_multi_app_kill_refused_until_disambiguated(processes):
    result = tm.task_manager({"action": "kill", "process": "e"})
    assert "several different apps" in result
    assert all(proc.terminate_calls == 0 for proc in processes.procs)

    allowed = tm.task_manager({"action": "kill", "process": "e", "all_matches": True})
    assert "Ended" in allowed
    assert all(proc.terminate_calls == 1 for proc in processes.procs)


def test_exact_name_kills_all_instances(processes):
    second = FakeProc(104, "notepad.exe")
    processes.procs.append(second)
    processes.by_pid[104] = second
    result = tm.task_manager({"action": "kill", "process": "notepad.exe"})
    assert "Ended 2 process" in result
    assert processes.by_pid[102].terminate_calls == 1
    assert second.terminate_calls == 1


def test_safety_still_classifies_kill_as_high():
    for action in ("kill", "end", "kill_process"):
        assert safety.classify("task_manager", {"action": action}) == safety.Risk.HIGH


def test_psutil_absent_degrades_without_breaking_tasks(monkeypatch, queue):
    monkeypatch.setattr(tm, "_PSUTIL", False)
    assert "pip install psutil" in tm.task_manager({"action": "processes"})
    assert "pip install psutil" in tm.task_manager({"action": "kill", "pid": 9})
    assert "a1" in tm.task_manager({"action": "list"})


def test_unknown_action_and_bad_parameters(queue, processes):
    assert "a1" in tm.task_manager(None)
    assert "Unknown task action" in tm.task_manager({"action": "bogus"})
    assert "Please provide a task_id" in tm.task_manager({"action": "cancel"})
    assert "not a number" in tm.task_manager({"action": "kill", "pid": "abc"})
