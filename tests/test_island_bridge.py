"""QtCore-only tests for producer / GUI-thread isolation."""
import threading
from PySide6.QtCore import QCoreApplication
from ui.bridge import UiBridge, UiDispatcher, EventBus, UiState

# Use QApplication whenever QtGui is available, so this module can coexist
# with widget tests in the same pytest process. A QCoreApplication cannot be
# upgraded to QApplication later.
try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None
_APP = None


def get_app():
    global _APP
    if _APP is None:
        _APP = QCoreApplication.instance() or (QApplication([]) if QApplication else QCoreApplication([]))
    return _APP


def pump(app, dispatcher):
    for _ in range(100):
        app.processEvents()
        if not dispatcher.pending():
            break


def test_queued_bridge_updates_are_delivered_on_owner_thread():
    app = get_app()
    bridge = UiBridge(); owner = threading.get_ident(); received = []
    bridge.state_changed.connect(lambda state: received.append((state, threading.get_ident())))
    def worker():
        bridge.set_state('PROCESSING')
        bridge.setAmplitude(3)
        bridge.ingest('You: hello')
        bridge.set_live_transcript('hello')
    t = threading.Thread(target=worker); t.start(); t.join()
    assert bridge.state == UiState.IDLE and bridge.dispatcher.pending() == 4
    pump(app, bridge.dispatcher)
    assert bridge.state == UiState.THINKING and received == [('THINKING', owner)]
    assert bridge.amplitude == 1 and bridge.live_transcript == 'hello'
    assert bridge.logs[-1].channel == 'user'


def test_dispatcher_bounded_fifo_and_shutdown():
    app = get_app()
    dispatcher = UiDispatcher(max_pending=3); seen = []
    for i in range(6): dispatcher.call(seen.append, i)
    assert dispatcher.dropped == 3
    pump(app, dispatcher)
    assert seen == [3, 4, 5]
    dispatcher.stop(); assert not dispatcher.call(seen.append, 6)


def test_bus_delivers_only_on_drain_and_subscriber_failure_does_not_abort():
    bus = EventBus(max_pending=4); seen = []
    bus.subscribe('x', lambda _: 1/0)
    bus.subscribe('x', seen.append)
    t = threading.Thread(target=lambda: [bus.publish('x', i) for i in range(6)])
    t.start(); t.join()
    assert seen == [] and bus.dropped == 2
    assert bus.flush() == 4 and seen == [2, 3, 4, 5]


def test_mute_transition_amplitude_nan_and_connection():
    app = get_app()
    bridge = UiBridge()
    bridge.set_state('SPEAKING'); bridge.set_muted(True); bridge.set_muted(False)
    bridge.setAmplitude(float('nan')); bridge.set_connecting(); bridge.set_connection(True)
    pump(app, bridge.dispatcher)
    assert bridge.state == UiState.LISTENING and not bridge.muted
    assert bridge.amplitude == 0 and bridge.connected and not bridge.connecting


def test_concurrent_producers_preserve_per_producer_order():
    app = get_app()
    dispatcher = UiDispatcher(max_pending=3000); seen = []
    def produce(worker):
        for n in range(300):
            dispatcher.call(seen.append, (worker, n, threading.get_ident()))
    threads = [threading.Thread(target=produce, args=(i,)) for i in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not seen  # work posted before the loop is never run on producers
    pump(app, dispatcher)
    assert len(seen) == 1200 and dispatcher.dropped == 0
    for i in range(4):
        assert [n for worker, n, _ in seen if worker == i] == list(range(300))
