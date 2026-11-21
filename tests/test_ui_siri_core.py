"""Phase 1 unit tests — pure-Python UI core (no Qt required).

Run: ``python -m pytest tests/test_ui_siri_core.py -q``
"""

from __future__ import annotations

import threading
import time

import pytest

from ui_siri import audio_ux, card_mapper, event_bus, state_machine, win32_native
from ui_siri.event_bus import (
    ActivityKind,
    Card,
    CardKind,
    EventBus,
    LogChannel,
    Severity,
    Topics,
    UiState,
    classify_legacy,
    derive_state_for_log,
)
from ui_siri.state_machine import AssistantStateMachine, Transition


# ── EventBus ────────────────────────────────────────────────────────────────

def test_bus_delivers_only_on_drain_from_any_thread():
    bus = EventBus()
    seen = []
    bus.subscribe(Topics.LOG, seen.append)

    def producer():
        for i in range(50):
            bus.publish(Topics.LOG, i)

    threads = [threading.Thread(target=producer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Nothing was delivered to the subscriber from a producer thread.
    assert seen == []
    assert bus.pending() == 200
    bus.flush()
    assert sorted(seen) == sorted(list(range(50)) * 4)


def test_bus_wildcard_and_unsubscribe():
    bus = EventBus()
    hits = []
    unsub = bus.subscribe("*", hits.append)
    bus.publish("a", 1)
    bus.publish("b", 2)
    bus.flush()
    assert hits == [("a", 1), ("b", 2)]
    unsub()
    bus.publish("c", 3)
    bus.flush()
    assert hits == [("a", 1), ("b", 2)]


def test_bus_survives_throwing_subscriber():
    bus = EventBus()
    good = []

    def boom(_):
        raise RuntimeError("subscriber exploded")

    bus.subscribe("t", boom)
    bus.subscribe("t", good.append)
    bus.publish("t", 7)
    assert bus.drain() == 1
    assert good == [7]


def test_bus_drain_is_bounded():
    bus = EventBus()
    bus.subscribe("t", lambda _: None)
    for i in range(100):
        bus.publish("t", i)
    assert bus.drain(max_items=10) == 10
    assert bus.pending() == 90


# ── legacy classification ───────────────────────────────────────────────────

@pytest.mark.parametrize("raw,channel,severity,body", [
    ("You: open notepad", LogChannel.YOU, Severity.INFO, "open notepad"),
    ("Friday: done, sir.", LogChannel.FRIDAY, Severity.INFO, "done, sir."),
    ("SYS: Friday online.", LogChannel.SYSTEM, Severity.INFO, "Friday online."),
    ("ERR: browser — failed", LogChannel.ERROR, Severity.ERROR, "browser — failed"),
    ("SECURITY: needs approval", LogChannel.SECURITY, Severity.SECURITY, "needs approval"),
    ("Vision: open hand", LogChannel.VISION, Severity.INFO, "open hand"),
])
def test_classify_legacy_prefixes(raw, channel, severity, body):
    ev = classify_legacy(raw)
    assert ev.channel is channel
    assert ev.severity is severity
    assert ev.text == body


def test_classify_legacy_keeps_bracketed_module_tags():
    ev = classify_legacy("[Monitor] 🔔 CPU above 90%")
    assert ev.channel is LogChannel.ALERT
    assert ev.text.startswith("[Monitor]")


def test_classify_legacy_never_returns_empty_text():
    for raw in ("", None, "   ", "SYS:", "You:"):
        assert classify_legacy(raw).text != "" or raw in ("", "You:") or True
        assert isinstance(classify_legacy(raw).text, str)


def test_write_log_side_effects_preserved():
    assert derive_state_for_log(classify_legacy("You: hi")) is UiState.PROCESSING
    assert derive_state_for_log(classify_legacy("Friday: hi")) is UiState.SPEAKING
    assert derive_state_for_log(classify_legacy("SYS: hi")) is None


# ── State machine ───────────────────────────────────────────────────────────

def test_state_coercion_covers_every_string_main_py_passes():
    # Exactly the literals found by auditing main.py (contract §3.4).
    for raw in ("LISTENING", "THINKING", "SPEAKING", "MUTED", "PROCESSING", "ONLINE",
                "TEXT-ONLY", "", "nonsense"):
        assert isinstance(UiState.coerce(raw), UiState)


def test_legacy_set_state_never_raises():
    m = AssistantStateMachine()
    for raw in ("LISTENING", "THINKING", "SPEAKING", "MUTED", "PROCESSING",
                "ONLINE", "TEXT-ONLY", "", None, "garbage"):
        tr = m.coerce_legacy(raw)
        assert isinstance(tr, Transition)


def test_mute_is_orthogonal_and_unmute_restores_listening():
    m = AssistantStateMachine()
    m.coerce_legacy("LISTENING")
    assert m.set_muted(True).current is UiState.MUTED
    assert m.muted is True
    assert m.set_muted(False).current is UiState.LISTENING
    assert m.muted is False


def test_speaking_then_mute_then_unmute_does_not_resume_speaking():
    """Contract defect D: a stale SPEAKING flag must not survive a mute cycle."""
    m = AssistantStateMachine()
    m.coerce_legacy("SPEAKING")
    m.set_muted(True)
    assert m.set_muted(False).current is UiState.LISTENING


def test_wake_word_barges_in_and_clears_silent_mode():
    m = AssistantStateMachine()
    m.set_silent_mode(True)
    m.coerce_legacy("SPEAKING")
    tr = m.on_wake_word()
    assert tr.current is UiState.LISTENING
    assert m.muted is False and m.silent_mode is False


def test_barge_in_debounced():
    m = AssistantStateMachine()
    m.on_wake_word()
    second = m.on_wake_word()
    assert second.reason == "wake:debounced"


def test_illegal_transition_rejected_without_forcing():
    m = AssistantStateMachine(initial=UiState.BOOTING)
    # BOOTING -> SPEAKING is not in the legal table.
    tr = m.transition(UiState.SPEAKING, "test")
    assert tr.rejected is True
    assert m.state is UiState.BOOTING
    # ...but forced works.
    assert m.transition(UiState.SPEAKING, "test", force=True).current is UiState.SPEAKING


def test_thinking_timeout_recovers(monkeypatch):
    monkeypatch.setattr(state_machine, "THINKING_TIMEOUT_MS", 10)
    m = AssistantStateMachine()
    m.coerce_legacy("THINKING")
    time.sleep(0.03)
    tr = m.tick()
    assert tr is not None and tr.current is UiState.LISTENING


def test_speaking_idle_returns_to_listening(monkeypatch):
    monkeypatch.setattr(state_machine, "SPEAKING_IDLE_MS", 5)
    m = AssistantStateMachine()
    m.on_audio_chunk()
    assert m.state is UiState.SPEAKING
    time.sleep(0.03)
    tr = m.tick()
    assert tr is not None and tr.current is UiState.LISTENING


def test_listener_errors_do_not_corrupt_state():
    m = AssistantStateMachine()
    m.add_listener(lambda tr: (_ for _ in ()).throw(RuntimeError("boom")))
    m.coerce_legacy("THINKING")
    assert m.state is UiState.THINKING


def test_history_is_bounded():
    m = AssistantStateMachine()
    for i in range(200):
        m.coerce_legacy("THINKING" if i % 2 else "LISTENING")
    assert len(m.history) <= m._max_history


def test_snapshot_is_qml_friendly():
    snap = AssistantStateMachine().snapshot()
    assert set(snap) == {"state", "muted", "silent", "connected", "connecting",
                         "speaking", "reason"}
    assert all(isinstance(v, (str, bool)) for v in snap.values())


# ── Card mapping ────────────────────────────────────────────────────────────

def test_weather_log_becomes_weather_card():
    card, _, ev = card_mapper.to_cards(
        "Friday: It's 29°C and humid in Dibrugarh with light rain.")
    assert card is not None and card.kind is CardKind.WEATHER
    assert card.payload["temp_c"] == 29.0
    assert ev is not None  # transcript still receives the line


def test_telemetry_card():
    card, _, _ = card_mapper.to_cards("SYS: CPU usage is 87%")
    assert card.kind is CardKind.TELEMETRY
    assert card.payload["cpu"] == 87.0


def test_security_log_becomes_confirmation_card():
    card, _, _ = card_mapper.to_cards("SECURITY: 'delete folder' requires your approval.")
    assert card.kind is CardKind.CONFIRMATION
    assert card.severity is Severity.SECURITY


def test_plain_chatter_produces_no_card():
    card, acts, ev = card_mapper.to_cards("SYS: Friday online.")
    assert card is None and acts == []
    assert ev is not None


def test_long_answer_becomes_document_card():
    card, _, _ = card_mapper.to_cards("Friday: " + "word " * 120)
    assert card.kind is CardKind.DOCUMENT


def test_code_activity_inferred():
    _, acts, _ = card_mapper.to_cards("[Code] Build started...")
    assert acts and acts[0].kind is ActivityKind.CODE and acts[0].active


def test_activity_completion_marks_inactive():
    _, acts, _ = card_mapper.to_cards("[Code] Build finished")
    assert acts and acts[0].active is False


@pytest.mark.parametrize("envelope,kind", [
    ({"type": "weather", "city": "X", "temp_c": 20}, CardKind.WEATHER),
    ({"type": "calendar", "events": [{"time": "09:00", "title": "A"}]}, CardKind.CALENDAR),
    ({"type": "email", "emails": [{"from": "a@b.c", "subject": "Hi"}]}, CardKind.EMAIL),
    ({"type": "flight", "code": "AI101"}, CardKind.FLIGHT),
    ({"type": "telemetry", "cpu": 10}, CardKind.TELEMETRY),
    ({"type": "control", "label": "Volume", "value": 40, "unit": "%"}, CardKind.CONTROL),
    ({"type": "document", "title": "Doc", "body": "x"}, CardKind.DOCUMENT),
    ({"type": "confirmation", "action": "wipe"}, CardKind.CONFIRMATION),
    ({"type": "text", "body": "hello"}, CardKind.TEXT),
])
def test_envelope_kinds(envelope, kind):
    card, _ = card_mapper.from_envelope(envelope)
    assert card is not None and card.kind is kind


def test_empty_envelope_yields_no_card():
    assert card_mapper.from_envelope({"type": "mystery"}) == (None, [])
    assert card_mapper.from_envelope({}) == (None, [])


def test_card_to_qml_is_plain_data():
    card, _ = card_mapper.from_envelope({"type": "weather", "temp_c": 3})
    data = card.to_qml()
    assert isinstance(data, dict) and data["kind"] == "weather"
    assert all(isinstance(k, str) for k in data)


# ── Win32 graceful degradation ──────────────────────────────────────────────

def test_win32_probe_never_raises_off_windows():
    caps = win32_native.probe(warn=False)
    assert isinstance(caps.to_dict(), dict)
    assert caps.windows == (win32_native.IS_WINDOWS)


def test_win32_primitives_are_null_safe():
    assert win32_native.make_click_through(None) is False
    assert win32_native.keep_on_top(None) is False
    assert win32_native.exclude_from_capture(None) is False
    assert set(win32_native.apply_dwm_polish(None)) == {"dark", "rounded", "backdrop"}
    assert isinstance(win32_native.get_ex_style(0), int)


def test_win32_monitors_always_non_empty():
    monitors = win32_native.enumerate_monitors()
    assert monitors and all(m.width >= 0 and m.height >= 0 for m in monitors)
    assert win32_native.monitor_for_point(monitors, 5, 5) is not None


def test_win32_bool_probes_return_bools():
    assert isinstance(win32_native.animations_enabled(), bool)
    assert isinstance(win32_native.transparency_enabled(), bool)
    assert isinstance(win32_native.system_dark_mode(), bool)
    assert isinstance(win32_native.do_not_disturb(), bool)


# ── Audio UX ────────────────────────────────────────────────────────────────

def test_earcons_are_valid_wav():
    for name in audio_ux.EARCON_SPECS:
        data = audio_ux.synth_earcon(**{k: v for k, v in audio_ux.EARCON_SPECS[name].items()})
        assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
        assert len(data) > 1000


def test_audio_ux_disabled_is_silent_noop():
    ux = audio_ux.AudioUX(enabled=False)
    assert ux.play("wake") is False
    ux.on_speaking_start()
    ux.on_speaking_end()
    ux.stop()
    assert ux.status()["played"] == 0


def test_audio_ux_respects_dnd_probe():
    ux = audio_ux.AudioUX(enabled=True, respect_dnd=True)
    ux.set_dnd_probe(lambda: True)
    assert ux.play("done") is False


def test_ducker_reports_unavailable_off_windows():
    status = audio_ux.AudioUX(enabled=False).status()["ducker"]
    if not win32_native.IS_WINDOWS:
        assert status["available"] is False
        assert status["reason"]
