"""Core event and state contracts exercised against the native Qt bridge."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import pytest
from PySide6.QtWidgets import QApplication
from ui.bridge.event_bus import EventBus
from ui.bridge.protocol import UiState, classify_log
from ui.bridge.bridge import UiBridge

_APP = None

@pytest.fixture
def app():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def test_bus_delivers_only_on_drain_from_any_thread():
    bus = EventBus(); seen = []; bus.subscribe('log', seen.append)
    threads = [threading.Thread(target=lambda: [bus.publish('log', i) for i in range(50)]) for _ in range(4)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert seen == [] and bus.pending() == 200
    assert bus.flush() == 200
    assert sorted(seen) == sorted(list(range(50)) * 4)


def test_bus_wildcard_and_unsubscribe():
    bus = EventBus(); hits = []; unsub = bus.subscribe('*', hits.append)
    bus.publish('a', 1); bus.publish('b', 2); bus.flush()
    assert hits == [('a', 1), ('b', 2)]
    unsub(); bus.publish('c', 3); bus.flush(); assert len(hits) == 2


def test_bus_survives_throwing_subscriber():
    bus = EventBus(); seen = []
    def boom(_): raise RuntimeError('subscriber exploded')
    bus.subscribe('t', boom); bus.subscribe('t', seen.append); bus.publish('t', 7)
    assert bus.drain() == 1 and seen == [7]


def test_bus_drain_and_capacity_are_bounded():
    bus = EventBus(max_pending=20)
    for i in range(100): bus.publish('t', i)
    assert bus.pending() == 20 and bus.dropped == 80
    assert bus.drain(10) == 10 and bus.pending() == 10


@pytest.mark.parametrize('raw,channel', [
    ('You: open notepad', 'user'), ('Friday: done', 'assistant'), ('SYS: online', 'system'),
    ('ERR: failed', 'error'), ('SECURITY: needs approval', 'security'), ('Vision: hand', 'vision'),
    ('[Monitor] alert', 'system')])
def test_log_classification_retains_full_text(raw, channel):
    event = classify_log(raw)
    assert event.channel == channel and event.text == raw


@pytest.mark.parametrize('name,expected', [
    ('ONLINE', UiState.LISTENING), ('PROCESSING', UiState.THINKING), ('working', UiState.THINKING),
    ('LISTENING', UiState.LISTENING), ('SPEAKING', UiState.SPEAKING), ('MUTED', UiState.MUTED),
    ('ERROR', UiState.ERROR), ('', UiState.IDLE), ('unrecognized', UiState.IDLE)])
def test_state_strings_are_coerced(name, expected):
    assert UiState.coerce(name) == expected


def test_state_coercion_preserves_enum_members():
    assert UiState.coerce(UiState.LISTENING) is UiState.LISTENING


def test_bridge_full_lifecycle_and_mute(app):
    bridge = UiBridge(); states = []; bridge.state_changed.connect(states.append)
    bridge.set_state('ONLINE'); bridge.set_state('PROCESSING'); bridge.set_state('SPEAKING')
    bridge.set_muted(True); bridge.set_muted(False)
    assert not states
    for _ in range(5): app.processEvents()
    assert states == ['LISTENING', 'THINKING', 'SPEAKING', 'MUTED', 'LISTENING']
    assert bridge.state == UiState.LISTENING and not bridge.muted
    bridge.dispatcher.stop()


def test_bridge_routes_security_logs_and_persona_to_subscribers(app):
    bridge = UiBridge(); logs = []; personas = []
    bridge.log_added.connect(logs.append); bridge.persona_changed.connect(lambda *v: personas.append(v))
    bridge.ingest('SECURITY: requires approval'); bridge.set_persona('normal', 'calm', 'Aoede')
    for _ in range(5): app.processEvents()
    assert logs[0].channel == 'security' and logs[0].text == 'SECURITY: requires approval'
    assert personas == [('normal', 'calm', 'Aoede')]
    bridge.dispatcher.stop()


def test_windows_overlay_fallback_is_safe(app):
    from ui.island import win32_overlay
    from ui.island.voice_hud import VoiceHUD
    if not win32_overlay.IS_WINDOWS:
        hud = VoiceHUD()
        assert win32_overlay.foreground_is_fullscreen(hud) is False
        assert win32_overlay.foreground_window() == (0, '')
        hud.close()
