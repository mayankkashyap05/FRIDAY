"""Native Qt delivery: producers enqueue, only the owning thread mutates UI."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import time
import pytest
from PySide6.QtWidgets import QApplication
from ui.bridge.bridge import UiBridge, UiDispatcher

_APP = None

@pytest.fixture
def app():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP

@pytest.fixture
def dispatcher(app):
    instance = UiDispatcher()
    yield instance
    instance.stop()


def pump(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate(): return
        time.sleep(.001)
    assert predicate()


def test_on_ui_thread_matches_qt_owner(dispatcher):
    assert dispatcher.on_ui_thread
    seen = []
    t = threading.Thread(target=lambda: seen.append(dispatcher.on_ui_thread)); t.start(); t.join()
    assert seen == [False]


def test_owner_calls_are_also_queued_in_fifo_order(app, dispatcher):
    ran = []
    dispatcher.call(ran.append, 1); dispatcher.call(ran.append, 2)
    assert ran == [] and dispatcher.pending() == 2
    pump(app, lambda: len(ran) == 2)
    assert ran == [1, 2]


def test_cross_thread_calls_are_queued_then_drained_in_order(app, dispatcher):
    ran = []; owner = threading.get_ident()
    def produce(tid):
        for i in range(50): dispatcher.call(lambda t=tid, n=i: ran.append((t, n, threading.get_ident())))
    threads = [threading.Thread(target=produce, args=(t,)) for t in range(6)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert ran == [] and dispatcher.pending() == 300
    pump(app, lambda: len(ran) == 300)
    assert all(ident == owner for _, _, ident in ran)
    for tid in range(6): assert [i for t, i, _ in ran if t == tid] == list(range(50))


def test_queued_before_event_processing_is_not_lost(app, dispatcher):
    ran = []; dispatcher.call(ran.append, 'early')
    assert not ran
    pump(app, lambda: bool(ran)); assert ran == ['early']


def test_dispatcher_survives_callable_exceptions(app, dispatcher):
    ran = []
    def fail(): raise RuntimeError('boom')
    dispatcher.call(fail); dispatcher.call(ran.append, 'after')
    pump(app, lambda: bool(ran)); assert ran == ['after']


def test_dispatcher_drops_instead_of_growing_without_bound(app):
    dispatcher = UiDispatcher(max_pending=10); ran = []
    for i in range(40): dispatcher.call(ran.append, i)
    assert dispatcher.dropped == 30 and dispatcher.pending() == 10
    pump(app, lambda: len(ran) == 10)
    assert ran == list(range(30, 40)); dispatcher.stop()


def test_producer_does_not_mutate_model(app):
    bridge = UiBridge(); seen = []; owner = threading.get_ident()
    bridge.log_added.connect(lambda entry: seen.append((entry.text, threading.get_ident())))
    t = threading.Thread(target=lambda: bridge.ingest('You: hi')); t.start(); t.join()
    assert seen == [] and len(bridge.logs) == 0
    pump(app, lambda: bool(seen))
    assert seen == [('You: hi', owner)]
    bridge.dispatcher.stop()


def test_bridge_ingest_is_thread_safe_under_load(app):
    bridge = UiBridge(); delivered = []; owner = threading.get_ident()
    bridge.log_added.connect(lambda entry: delivered.append((entry.text, threading.get_ident())))
    def produce(tid):
        for i in range(60): bridge.ingest(f'SYS: {tid} {i}')
    threads = [threading.Thread(target=produce, args=(t,)) for t in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not delivered
    pump(app, lambda: len(delivered) == 480)
    assert len(bridge.logs) == 200 and all(ident == owner for _, ident in delivered)
    bridge.dispatcher.stop()


def test_amplitude_updates_are_queued_and_clamped(app):
    bridge = UiBridge(); seen = []; owner = threading.get_ident()
    bridge.amplitude_changed.connect(lambda value: seen.append((value, threading.get_ident())))
    t = threading.Thread(target=lambda: [bridge.setAmplitude(v) for v in (-1, .5, 2, float('nan'))])
    t.start(); t.join(); assert seen == []
    pump(app, lambda: len(seen) == 4)
    assert [v for v, _ in seen] == [0, .5, 1, 0]
    assert all(ident == owner for _, ident in seen)
    bridge.dispatcher.stop()


def test_stop_discards_pending_and_rejects_new_callbacks(app, dispatcher):
    ran = []; dispatcher.call(ran.append, 1); dispatcher.stop()
    assert dispatcher.call(ran.append, 2) is False
    app.processEvents(); assert not ran and not dispatcher.pending()


def test_panel_close_waits_for_worker_and_suppresses_result(app):
    from ui.panels.common import PanelServices
    entered = threading.Event(); release = threading.Event(); delivered = []
    dispatcher = UiDispatcher()
    services = PanelServices(dispatcher)
    def work():
        entered.set()
        release.wait(2)
        return 'done'
    assert services.run(work, lambda *args: delivered.append(args))
    assert entered.wait(1)
    closer_result = []
    closer = threading.Thread(target=lambda: closer_result.append(services.close(1)))
    closer.start()
    assert services._closed.wait(1)
    assert not services.run(lambda: None, lambda *_: None)
    release.set(); closer.join(2)
    app.processEvents()
    assert closer_result == [True] and delivered == []
    dispatcher.stop()


def test_panel_close_timeout_prevents_late_delivery(app):
    from ui.panels.common import PanelServices
    entered = threading.Event(); release = threading.Event(); delivered = []
    dispatcher = UiDispatcher(); services = PanelServices(dispatcher)
    def work():
        entered.set(); release.wait(2); return 42
    assert services.run(work, lambda *args: delivered.append(args))
    assert entered.wait(1)
    assert services.close(0) is False
    release.set()
    assert services.close(2) is True
    app.processEvents()
    assert not delivered
    dispatcher.stop()


def test_controller_shutdown_disconnects_and_stops_timers(headless_ui, qt_app):
    controller = headless_ui; hud = controller.hud
    assert hud._timer.isActive()
    controller.shutdown(); controller.shutdown()
    assert not hud._timer.isActive() and not hud._fullscreen_timer.isActive()
    controller.bridge._set_amplitude(.75)
    assert hud.audio_level == 0.0
    assert not controller.bridge.setAmplitude(.5)
    qt_app.processEvents()
