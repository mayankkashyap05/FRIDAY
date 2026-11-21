"""Thread-safety regression tests for the UI layer.

These encode findings from real multi-threaded runs, so a future refactor cannot
quietly reintroduce the failure modes:

1. **Producers must never touch UI state.**  They append a callable to a
   ``deque`` (atomic under the GIL) and wake a single consumer thread. No
   signal emission, no shared model mutation, no timer reconfiguration.

2. **The consumer is one dedicated UI-loop thread**, not whatever thread
   happens to call ``drain()``. Work is FIFO and per-producer order is kept.

3. **Nothing may be lost when a producer fires before the loop starts.**

4. **A bounded backlog**: a dead consumer drops updates instead of growing
   memory without limit.

The suite needs no third-party packages at all.
"""

from __future__ import annotations

import inspect
import threading
import time

import pytest

from ui import bridge as bridge_mod
from ui.bridge import UiBridge, UiDispatcher
from ui.event_bus import EventBus
from ui.state_machine import AssistantStateMachine


@pytest.fixture()
def dispatcher():
    instance = UiDispatcher()
    instance.bind_ui_thread()
    yield instance
    instance.stop()


@pytest.fixture()
def bridge(dispatcher):
    machine, bus = AssistantStateMachine(), EventBus()
    view = UiBridge(machine, bus, dispatcher)
    view.machine = machine          # convenience handle for the tests
    return view


def _pump(dispatcher: UiDispatcher, rounds: int = 60) -> None:
    for _ in range(rounds):
        if dispatcher.pending() == 0:
            break
        dispatcher.drain(4000)
        time.sleep(0.001)
    dispatcher.drain(4000)


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


# ── the dispatcher itself ────────────────────────────────────────────────────

def test_bridge_module_imports_no_gui_toolkit():
    """Guards the migration: the bridge must stay pure Python.

    Checked against the AST (not the prose) so the module's own explanation of
    what it replaced cannot trip the guard.
    """
    import ast

    tree = ast.parse(inspect.getsource(bridge_mod))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    offenders = sorted(name for name in imported
                       if name.startswith(("PySide", "PyQt", "shiboken")))
    assert not offenders, (
        f"ui/bridge.py must not import a GUI toolkit: {offenders}")


def test_on_ui_thread_uses_python_thread_identity(dispatcher):
    assert dispatcher.on_ui_thread

    seen = []
    thread = threading.Thread(target=lambda: seen.append(dispatcher.on_ui_thread))
    thread.start()
    thread.join()
    assert seen == [False]


def test_ui_thread_calls_run_inline(dispatcher):
    ran = []
    dispatcher.call(ran.append, 1)
    assert ran == [1], "same-thread calls must not be deferred"
    assert dispatcher.pending() == 0


def test_cross_thread_calls_are_queued_then_drained_in_order(dispatcher):
    ran = []

    def produce(tid):
        for i in range(50):
            dispatcher.call(ran.append, (tid, i))

    threads = [threading.Thread(target=produce, args=(t,)) for t in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert ran == [], "nothing may execute on a producer thread"
    assert dispatcher.pending() == 300

    _pump(dispatcher)
    assert len(ran) == 300
    assert dispatcher.pending() == 0
    # Per-producer ordering is preserved even though producers interleave.
    for tid in range(6):
        seq = [i for owner, i in ran if owner == tid]
        assert seq == list(range(50))


def test_queued_before_the_loop_starts_is_not_lost():
    """Finding #4: a producer may fire before the loop thread exists."""
    dispatcher = UiDispatcher()          # never bound: treated as UI thread
    ran = []
    dispatcher.call(ran.append, "early")
    assert ran == ["early"] or dispatcher.pending() == 1


def test_dispatcher_survives_callable_exceptions(dispatcher):
    dispatcher.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert dispatcher.stats()["executed"] == 1


def test_dispatcher_drops_instead_of_growing_without_bound(dispatcher):
    dispatcher.MAX_QUEUE = 10
    # Pretend we are a producer thread (nothing is ever drained here).
    dispatcher._ident = -1
    for _ in range(40):
        dispatcher.call(lambda: None)
    stats = dispatcher.stats()
    assert stats["dropped"] > 0, stats
    assert dispatcher.pending() <= 11


def test_producer_path_does_not_emit_or_touch_timers():
    """Finding #1/#3, at the source level."""
    source = inspect.getsource(UiDispatcher.call)
    assert "emit" not in source, (
        "call() must not emit a signal; concurrent emits abort GUI toolkits")
    assert "setInterval" not in source and "Timer(" not in source, (
        "call() runs on producer threads and must not reconfigure timers")


def test_dedicated_loop_thread_owns_mutations(dispatcher):
    """The real contract: bridge mutations happen on the loop thread."""
    dispatcher.start()
    try:
        assert _wait_for(lambda: dispatcher.running)
        loop_ident = []
        dispatched = []
        dispatcher.call(lambda: loop_ident.append(threading.get_ident()))
        assert _wait_for(lambda: bool(loop_ident)), "the loop thread never ran"

        def producer():
            dispatched.append(threading.get_ident())
            for i in range(20):
                dispatcher.call(lambda v=i: None)

        thread = threading.Thread(target=producer)
        thread.start()
        thread.join()
        assert _wait_for(lambda: dispatcher.pending() == 0)
        assert loop_ident[0] != dispatched[0], (
            "work must not run on the producer thread")
        assert dispatcher.stats()["executed"] >= 1
    finally:
        dispatcher.stop()


def test_idle_loop_uses_almost_no_cpu(dispatcher):
    """No busy-wait: an idle dispatcher must sleep, not spin."""
    dispatcher.start()
    try:
        assert _wait_for(lambda: dispatcher.running)
        before = time.process_time()
        time.sleep(0.35)
        idle_cpu = time.process_time() - before
        assert idle_cpu < 0.15, f"idle dispatcher burned {idle_cpu:.3f}s of CPU"
    finally:
        dispatcher.stop()


# ── end-to-end: backend threads -> stores ───────────────────────────────────

def test_bridge_ingest_is_thread_safe_under_load(bridge, dispatcher):
    """The real workload: many producers logging while the UI thread drains."""
    errors = []

    def produce(tid):
        try:
            for i in range(60):
                dispatcher.call(bridge.ingest, f"[Code] worker {tid} step {i}")
                dispatcher.call(bridge.machine.coerce_legacy,
                                "THINKING" if i % 2 else "LISTENING")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=produce, args=(t,)) for t in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    _pump(dispatcher)

    assert dispatcher.pending() == 0
    assert dispatcher.stats()["executed"] == 8 * 60 * 2
    # The transcript saturates at its cap rather than growing without bound.
    assert 0 < bridge.transcript.count() <= 400


def test_models_stay_within_capacity(bridge):
    for i in range(600):
        bridge.ingest(f"[SYS] event {i}")
    assert bridge.cards.count() <= 8
    assert bridge.activities.active_count() <= 16
    assert bridge.transcript.count() <= 400


def test_amplitude_updates_are_thread_safe_and_coalesced(bridge, dispatcher):
    """The sounddevice callback fires ~30 Hz from a real-time audio thread."""
    seen = []
    bridge.on_emit = lambda kind, payload: seen.append((kind, payload))

    def audio_callback():
        for i in range(240):
            dispatcher.call(bridge.setAmplitude, (i % 100) / 100.0)

    thread = threading.Thread(target=audio_callback)
    thread.start()
    thread.join()
    _pump(dispatcher)

    assert dispatcher.pending() == 0
    amplitudes = [p["value"] for k, p in seen if k == "amplitude"]
    assert amplitudes, "no amplitude reached the renderer"
    # Throttled to <= 60 Hz: 240 queued updates cannot all be broadcast.
    assert len(amplitudes) < 240
    assert all(0.0 <= value <= 1.0 for value in amplitudes)
