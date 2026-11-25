"""Deep combinatorial tests for all 6 agent/ modules."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from agent.error_handler import ErrorDecision, analyze_error, generate_fix
from agent.executor import AgentExecutor, _call_tool, _call_tool_with_timeout
from agent.loop_guard import LoopGuard
from agent.planner import _fallback_plan
from agent.resilience import classify_error, run_sync_tool
from agent.task_queue import TaskPriority, TaskQueue, TaskStatus


# ---------------------------------------------------------------------------
# agent/loop_guard.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("max_identical", [2, 3, 5])
def test_loop_guard_identical_calls(max_identical):
    lg = LoopGuard(max_identical=max_identical, ping_pong=2)
    for i in range(max_identical - 1):
        assert lg.check("web_search", {"query": "hello"}) is None
    blocked = lg.check("web_search", {"query": "hello"})
    assert blocked is not None and "Loop guard" in blocked


def test_loop_guard_clamps_max_identical_below_two():
    lg = LoopGuard(max_identical=0, ping_pong=1)
    assert lg.max_identical == 2
    assert lg.ping_pong == 2


@pytest.mark.parametrize("ping_pong", [2, 3])
def test_loop_guard_ping_pong_honors_parameter(ping_pong):
    lg = LoopGuard(max_identical=3, ping_pong=ping_pong)
    seq = ["tool_a", "tool_b"] * ping_pong
    for name in seq[:-1]:
        assert lg.check(name, {"x": 1}) is None
    blocked = lg.check(seq[-1], {"x": 1})
    assert blocked is not None and "ping-pong" in blocked


def test_loop_guard_different_args_and_three_tool_rotation_allowed():
    lg = LoopGuard(max_identical=2, ping_pong=2)
    assert lg.check("t1", {"a": 1}) is None
    assert lg.check("t1", {"a": 2}) is None
    assert lg.check("t2", {"a": 1}) is None
    assert lg.check("t3", {"a": 1}) is None
    assert lg.check("t1", {"a": object()}) is None  # non-JSON-serializable


# ---------------------------------------------------------------------------
# agent/resilience.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg,expected", [
    ("429 Too Many Requests", "retryable"),
    ("503 Service Unavailable", "retryable"),
    ("Connection reset by peer", "retryable"),
    ("quota exceeded", "retryable"),
    ("ValueError: bad input", "fatal"),
    ("PermissionError: denied", "fatal"),
])
def test_resilience_classify_error(msg, expected):
    assert classify_error(RuntimeError(msg)) == expected


def test_resilience_run_sync_tool_retry_and_fatal():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("429 rate limit")
        return "ok"

    res = asyncio.run(run_sync_tool(flaky, "flaky_tool", timeout=5, attempts=3))
    assert res.ok is True and res.content == "ok" and calls["n"] == 2

    def fatal():
        raise ValueError("bad arg")

    res2 = asyncio.run(run_sync_tool(fatal, "fatal_tool", timeout=0, attempts=0))
    assert res2.ok is False and res2.error_kind == "fatal"


# ---------------------------------------------------------------------------
# agent/planner.py & agent/error_handler.py
# ---------------------------------------------------------------------------

def test_planner_fallback_plan():
    plan = _fallback_plan("check weather")
    assert isinstance(plan, dict)
    assert len(plan.get("steps", [])) >= 1


def test_error_handler_analyze_and_generate_fix(monkeypatch):
    import sys, types
    if "google.generativeai" not in sys.modules:
        g_mod = sys.modules.setdefault("google", types.ModuleType("google"))
        ga_mod = types.ModuleType("google.generativeai")
        ga_mod.configure = MagicMock()
        ga_mod.GenerativeModel = MagicMock()
        monkeypatch.setitem(sys.modules, "google.generativeai", ga_mod)
        g_mod.generativeai = ga_mod
    monkeypatch.setattr("agent.error_handler._get_api_key", lambda: "AIzaFakeKey")
    step = {"step_number": 1, "tool": "web_search", "description": "search", "parameters": {"query": "x"}, "critical": True}
    # Fallback when no API key / exception
    res = analyze_error(step, "429 quota", attempt=1, max_attempts=3)
    assert res["decision"] in (ErrorDecision.RETRY, ErrorDecision.REPLAN, ErrorDecision.ABORT, ErrorDecision.SKIP)
    fixed = generate_fix(step, "some error", "goal")
    assert isinstance(fixed, dict)


# ---------------------------------------------------------------------------
# agent/executor.py
# ---------------------------------------------------------------------------

def test_executor_call_tool_timeout_does_not_hang():
    import agent.executor as ex

    def slow_tool(*_a, **_kw):
        time.sleep(5)
        return "late"

    orig_timeout = ex.TOOL_TIMEOUT_S
    ex.TOOL_TIMEOUT_S = 0.05
    try:
        with patch.object(ex, "_call_tool", side_effect=slow_tool):
            t0 = time.monotonic()
            out = _call_tool_with_timeout("web_search", {"query": "hi"}, None)
            elapsed = time.monotonic() - t0
            assert "timed out" in out.lower()
            assert elapsed < 1.0
    finally:
        ex.TOOL_TIMEOUT_S = orig_timeout


def test_executor_clear_memory_blocked_in_background_and_forget_wired():
    res = _call_tool("clear_memory", {}, None)
    assert "confirmation" in res.lower() or "blocked" in res.lower() or "refused" in res.lower()

    with patch("memory.memory_manager.forget_memory", return_value="Forgot x") as m:
        out = _call_tool("forget_memory", {"category": "notes", "key": "x"}, None)
        assert out == "Forgot x"
        m.assert_called_once_with(category="notes", key="x")


def test_executor_read_pdf_wired():
    with patch("actions.pdf_reader.read_pdf", return_value="PDF text") as m:
        out = _call_tool("read_pdf", {"file_path": "a.pdf"}, None)
        assert out == "PDF text"
        m.assert_called_once()


# ---------------------------------------------------------------------------
# agent/task_queue.py
# ---------------------------------------------------------------------------

def test_task_queue_priority_and_history_pruning():
    q = TaskQueue(max_concurrent=1)
    q._MAX_TASK_HISTORY = 5
    for i in range(10):
        tid = q.submit(f"goal {i}", priority=TaskPriority.LOW)
        q._tasks[tid].status = TaskStatus.COMPLETED
        q._tasks[tid].completed_at = float(i)
    # Next submit triggers pruning
    q.submit("goal final", priority=TaskPriority.HIGH)
    assert len(q._tasks) <= 6


def test_agent_llm_calls_use_retry_helper_and_shared_flash(monkeypatch):
    import api_keys
    from pathlib import Path
    from types import SimpleNamespace
    from config.models import MODEL_FLASH
    from agent import planner, error_handler, executor

    seen = []
    responses = [
        '{"goal":"g","steps":[]}',
        '{"decision":"retry","reason":"temporary","max_retries":1}',
        'print("fixed")',
    ]
    def generate(model, contents, config=None, **kwargs):
        seen.append((model, contents, config))
        return SimpleNamespace(text=responses.pop(0))
    monkeypatch.setattr(api_keys, "generate_with_retry", generate)
    assert planner.create_plan("goal")["goal"] == "g"
    decision = error_handler.analyze_error(
        {"step": 1, "tool": "x", "description": "do it", "parameters": {}},
        "temporary", attempt=1, max_attempts=3,
    )
    assert decision["decision"] == ErrorDecision.RETRY
    assert error_handler.generate_fix(
        {"step": 1, "tool": "x", "description": "do it", "parameters": {}},
        "failure", "different route",
    )["tool"] == "code_helper"
    assert all(call[0] == MODEL_FLASH for call in seen)
    for module in (planner, error_handler, executor):
        assert "google.generativeai" not in Path(module.__file__).read_text(encoding="utf-8")
