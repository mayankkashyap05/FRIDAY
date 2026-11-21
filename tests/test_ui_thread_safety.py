"""Thread-safety regression tests for the Qt UI layer.

These encode findings from real multi-threaded runs against PySide6, so a future
refactor cannot quietly reintroduce the failure modes:

1. **Producers must touch no Qt object at all.**  In particular
   ``QThread.currentThread()`` must never be called from a background thread:
   Qt then *adopts* that foreign thread and allocates a wrapper object on every
   call, which is both slow and (observed) a source of native crashes under
   contention.  ``UiDispatcher`` therefore tracks the GUI thread with
   ``threading.get_ident()``.

2. **``Signal.emit()`` must not be the cross-thread transport.**  With several
   producer threads emitting concurrently, PySide6 aborts with
   ``Fatal Python error: bool_dealloc``.  The dispatcher uses a plain ``deque``
   (atomic append under the GIL) plus a GUI-thread ``QTimer`` drain instead.

3. **``QTimer`` mutation is GUI-thread only.**  ``setInterval()`` from a
   producer thread yields ``Timers cannot be started from another thread``;
   the adaptive interval is therefore computed inside ``drain()``.

4. **Nothing may be lost when a producer fires before the event loop starts.**

The suite needs only ``QtCore`` (no ``QtGui``), so it runs in a headless CI
container.
"""

from __future__ import annotations

import inspect
import threading
import time

import pytest

pytest.importorskip("PySide6.QtCore")

from PySide6.QtCore import QTimer  # noqa: E402

from ui_siri import bridge as bridge_mod  # noqa: E402
from ui_siri.bridge import UiBridge, UiDispatcher  # noqa: E402
from ui_siri.event_bus import EventBus  # noqa: E402
from ui_siri.state_machine import AssistantStateMachine  # noqa: E402


@pytest.fixture()
def bridge(qt_app):
    machine, bus, dispatcher = AssistantStateMachine(), EventBus(), UiDispatcher()
    dispatcher.bind_gui_thread()
    dispatcher.start()
    view = UiBridge(machine, bus, dispatcher)
    view.machine = machine          # convenience handle for the tests
    yield view
    dispatcher.stop()


def _pump(dispatcher: UiDispatcher, qt_app, rounds: int = 60) -> None:
    for _ in range(rounds):
        dispatcher.drain(4000)
        qt_app.processEvents()
        time.sleep(0.001)


# ── the dispatcher itself ────────────────────────────────────────────────────

def test_dispatcher_never_references_qthread():
    """Guards finding #1 at the source level.

    ``QThread`` in this module would mean thread identity is being resolved
    through Qt, which is exactly what crashed.
    """
    source = inspect.getsource(bridge_mod)
    assert "QThread" not in source, (
        "UiDispatcher must track thread identity with threading.get_ident(), "
        "not QThread.currentThread()"
    )


def test_on_gui_thread_uses_python_thread_identity(qt_app):
    dispatcher = UiDispatcher()
    dispatcher.bind_gui_thread()
    assert dispatcher.on_gui_thread

    seen = []
    thread = threading.Thread(target=lambda: seen.append(dispatcher.on_gui_thread))
    thread.start()
    thread.join()
    assert seen == [False]


def test_gui_thread_calls_run_inline(qt_app):
    dispatcher = UiDispatcher()
    dispatcher.bind_gui_thread()
    ran = []
    dispatcher.call(ran.append, 1)
    assert ran == [1], "same-thread calls must not be deferred"
    assert dispatcher.pending() == 0


def test_cross_thread_calls_are_queued_then_drained_in_order(qt_app):
    dispatcher = UiDispatcher()
    dispatcher.bind_gui_thread()
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

    _pump(dispatcher, qt_app)
    assert len(ran) == 300
    assert dispatcher.pending() == 0
    # Per-producer ordering is preserved even though producers interleave.
    for tid in range(6):
        seq = [i for owner, i in ran if owner == tid]
        assert seq == list(range(50))


def test_queued_before_event_loop_nothing_is_lost(qt_app):
    """Finding #4: a producer may fire before exec() starts."""
    dispatcher = UiDispatcher()          # never bound: treated as GUI thread
    ran = []
    dispatcher.call(ran.append, "early")
    assert ran == ["early"] or dispatcher.pending() == 1


def test_dispatcher_survives_callable_exceptions(qt_app):
    dispatcher = UiDispatcher()
    dispatcher.bind_gui_thread()
    dispatcher.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert dispatcher.stats()["executed"] == 1


def test_dispatcher_drops_instead_of_growing_without_bound(qt_app):
    dispatcher = UiDispatcher()
    dispatcher.bind_gui_thread()
    dispatcher._max_queue = 10
    # Pretend we are a producer thread (nothing is ever drained here).
    dispatcher._gui_ident = -1
    for i in range(40):
        dispatcher.call(lambda: None)
    stats = dispatcher.stats()
    assert stats["dropped"] > 0, stats
    assert dispatcher.pending() <= 11


# ── end-to-end: backend threads -> models ───────────────────────────────────

def test_bridge_ingest_is_thread_safe_under_load(bridge, qt_app):
    """The real workload: many producers logging while the GUI drains."""
    dispatcher = bridge.dispatcher
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
    _pump(dispatcher, qt_app)

    assert dispatcher.pending() == 0
    assert dispatcher.stats()["executed"] == 8 * 60 * 2
    # Transcript saturates at its cap rather than growing without bound.
    assert bridge.transcript.count() <= 400
    assert bridge.transcript.count() > 0


def test_models_stay_within_capacity(bridge, qt_app):
    for i in range(600):
        bridge.ingest(f"[SYS] event {i}")
    for _ in range(4):
        _pump(bridge.dispatcher, qt_app, rounds=5)

    assert bridge.cards.count() <= 8
    assert bridge.activities.active_count() <= 16
    assert bridge.transcript.count() <= 400


def test_timer_interval_is_only_touched_on_the_gui_thread():
    """Finding #3, at the source level."""
    source = inspect.getsource(UiDispatcher.call)
    assert "setInterval" not in source, (
        "call() runs on producer threads and must not start/restart a QTimer"
    )
    assert "emit" not in source, (
        "call() must not emit a Signal; concurrent emits abort PySide6"
    )


# ── live-frame plumbing (regression: preview froze on frame one) ────────────

def test_frame_revision_is_a_working_qml_property(bridge, qt_app):
    """QML appends `frameRevision` to image provider URLs.

    Without this the inspector's camera preview is decoded once and never
    refreshed, because QQuickImageProvider URLs are revision-based.
    """
    assert bridge.frameRevision == 0

    seen = []
    bridge.framesChanged.connect(lambda: seen.append(bridge.frameRevision))

    bridge.notify_frames()
    bridge.notify_frames()

    assert bridge.frameRevision == 2
    assert seen == [1, 2], "each batch must notify QML exactly once"


def test_push_frame_notifies_from_a_producer_thread(bridge, qt_app):
    """Frames arrive on the backend thread; the revision bump must not."""
    dispatcher = bridge.dispatcher
    thread = threading.Thread(target=dispatcher.call, args=(bridge.notify_frames,))
    thread.start()
    thread.join()

    assert bridge.frameRevision == 0, "a producer thread must not touch the bridge"
    _pump(dispatcher, qt_app)
    assert bridge.frameRevision == 1
