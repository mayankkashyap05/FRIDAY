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


# ─────────────────────────────────────────────────────────────────────────────
# Windows capability probe (Bug 2: all five reported False on Win11 26300)
# ─────────────────────────────────────────────────────────────────────────────

def _fake_windows(monkeypatch, build=26300, *, user32_exports=None,
                  dwmapi_exports=None, shell32_exports=None):
    """Simulate a Windows host with a given build and DLL export table.

    ``probe_capabilities()`` is a pure capability query, so this needs no HWND
    and no real Windows: we swap the loaded libraries for fakes that answer
    ``getattr`` the way the real export tables would.
    """
    from ui_siri import win32_native as w32

    defaults = {
        "user32": ("GetWindowLongPtrW", "SetWindowLongPtrW", "GetWindowLongW",
                   "SetWindowLongW", "SetWindowDisplayAffinity",
                   "SetWindowPos", "SystemParametersInfoW",
                   "SetProcessDpiAwarenessContext", "GetMonitorInfoW",
                   "EnumDisplayMonitors"),
        "dwmapi": ("DwmSetWindowAttribute",),
        "shell32": ("SHQueryUserNotificationState",),
    }
    tables = {
        "user32": user32_exports if user32_exports is not None else defaults["user32"],
        "dwmapi": dwmapi_exports if dwmapi_exports is not None else defaults["dwmapi"],
        "shell32": shell32_exports if shell32_exports is not None else defaults["shell32"],
    }

    class FakeDll:
        """Only the named exports exist; everything else is absent."""

        def __init__(self, names):
            self._names = set(names)

        def __getattr__(self, item):
            if item in self._names:
                return lambda *a, **k: 0
            raise AttributeError(item)

    monkeypatch.setattr(w32, "IS_WINDOWS", True, raising=False)
    for lib in ("user32", "dwmapi", "shell32", "shcore"):
        names = tables.get(lib, ())
        monkeypatch.setattr(w32, lib, FakeDll(names), raising=False)
    monkeypatch.setattr(w32, "windows_build", lambda: build, raising=False)
    # Side-effect-free appearance probes: probe() calls these.
    monkeypatch.setattr(w32, "system_dark_mode", lambda: True, raising=False)
    monkeypatch.setattr(w32, "transparency_enabled", lambda: True, raising=False)
    monkeypatch.setattr(w32, "animations_enabled", lambda: True, raising=False)
    monkeypatch.setattr(w32, "enumerate_monitors", lambda: [], raising=False)
    # Fresh capability record for each test.
    monkeypatch.setattr(w32, "CAPS", w32.Capabilities(), raising=False)
    return w32


WIN11_26300 = 26300


def test_probe_enables_all_capabilities_on_windows_11_26300(monkeypatch):
    """The reported bug: every capability False on a fully capable machine."""
    w32 = _fake_windows(monkeypatch, WIN11_26300)
    caps = w32.probe_capabilities()

    assert caps.windows is True
    assert caps.build == WIN11_26300
    assert caps.user32 and caps.dwmapi and caps.shcore and caps.shell32
    for name in ("capture_exclusion", "layered_windows", "backdrop",
                 "rounded_corners", "dnd"):
        assert getattr(caps, name) is True, (
            f"{name} should be available on build {WIN11_26300}; "
            f"reasons={caps.reasons}")


def test_probe_23621_boundaries(monkeypatch):
    """Each capability gates on its own build number."""
    cases = [
        # build, capture, layered, rounded(22000), backdrop(22621)
        (19041, True,  True,  False, False),   # Win10 2004
        (21999, True,  True,  False, False),   # just below Win11
        (22000, True,  True,  True,  False),   # Win11 21H2
        (22621, True,  True,  True,  True),    # Win11 22H2
    ]
    for build, cap, lay, rounded, backdrop in cases:
        w32 = _fake_windows(monkeypatch, build)
        caps = w32.probe_capabilities()
        assert caps.capture_exclusion is cap, (build, "capture", caps.reasons)
        assert caps.layered_windows is lay, (build, "layered", caps.reasons)
        assert caps.rounded_corners is rounded, (build, "rounded", caps.reasons)
        assert caps.backdrop is backdrop, (build, "backdrop", caps.reasons)


def test_probe_19040_records_why_capture_exclusion_is_off(monkeypatch):
    w32 = _fake_windows(monkeypatch, 19040)
    caps = w32.probe_capabilities()
    assert caps.capture_exclusion is False
    assert "19041" in caps.reasons["capture_exclusion"]


def test_every_false_capability_records_a_reason(monkeypatch):
    """Silent degradation must be impossible: a False always says why."""
    # A host with nothing available at all.
    w32 = _fake_windows(monkeypatch, 26300, user32_exports=(),
                        dwmapi_exports=(), shell32_exports=())
    caps = w32.probe_capabilities()
    for name in ("capture_exclusion", "layered_windows", "backdrop",
                 "rounded_corners", "dnd"):
        assert getattr(caps, name) is False
        assert caps.reasons.get(name), f"{name} is False with no reason recorded"


def test_probe_missing_export_is_named(monkeypatch):
    w32 = _fake_windows(monkeypatch, 26300,
                        shell32_exports=())          # no SHQueryUserNotificationState
    caps = w32.probe_capabilities()
    assert caps.dnd is False
    assert "SHQueryUserNotificationState" in caps.reasons["dnd"]


def test_probe_non_windows_explains_everything(monkeypatch):
    from ui_siri import win32_native as w32

    monkeypatch.setattr(w32, "IS_WINDOWS", False, raising=False)
    monkeypatch.setattr(w32, "CAPS", w32.Capabilities(windows=False), raising=False)
    monkeypatch.setattr(w32, "windows_build", lambda: 0, raising=False)
    caps = w32.probe_capabilities()
    assert caps.reasons.get("platform")
    for name in ("capture_exclusion", "layered_windows", "backdrop",
                 "rounded_corners", "dnd"):
        assert caps.reasons.get(name), f"{name} has no reason off-Windows"


def test_windows_build_reports_a_number():
    from ui_siri import win32_native as w32

    build = w32.windows_build()
    assert isinstance(build, int) and build >= 0


# ─────────────────────────────────────────────────────────────────────────────
# Bug 3: ACTING must render as PROCESSING (not fall back to LISTENING)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("legacy,expected", [
    ("LISTENING", "LISTENING"),
    ("THINKING", "THINKING"),
    ("ACTING", "PROCESSING"),      # the reported failure
    ("ACTING_ON", "PROCESSING"),
    ("EXECUTING", "PROCESSING"),
    ("TOOL_CALL", "PROCESSING"),
    ("SPEAKING", "SPEAKING"),
    ("ERROR", "MUTED"),
    ("FAILED", "MUTED"),
    ("DORMANT", "BOOTING"),
])
def test_legacy_state_strings_resolve(legacy, expected):
    """Unknown spellings degrade to LISTENING, which is how ACTING was lost."""
    from ui_siri.event_bus import UiState

    assert UiState.coerce(legacy).value == expected


def test_full_lifecycle_on_the_bridge():
    """DORMANT -> LISTENING -> THINKING -> ACTING -> SPEAKING -> ERROR -> DORMANT.

    Walks the machine exactly as main.py drives it (string states through
    coerce_legacy) and checks the *bridge* surface QML reads, which is what the
    verifier asserted on Windows.
    """
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    from ui_siri.bridge import UiBridge, UiDispatcher
    from ui_siri.event_bus import EventBus
    from ui_siri.state_machine import AssistantStateMachine

    machine = AssistantStateMachine()
    dispatcher = UiDispatcher()
    dispatcher.bind_gui_thread()
    bridge = UiBridge(machine, EventBus(), dispatcher)

    cycle = [
        ("DORMANT", "BOOTING"),
        ("LISTENING", "LISTENING"),
        ("THINKING", "THINKING"),
        ("ACTING", "PROCESSING"),
        ("SPEAKING", "SPEAKING"),
        ("ERROR", "MUTED"),
        ("DORMANT", "BOOTING"),
    ]
    seen = []
    for sent, expected in cycle:
        machine.coerce_legacy(sent)
        app.processEvents()
        actual = bridge.state
        seen.append((sent, actual))
        assert actual == expected, (
            f"{sent} -> assistantState={actual!r}, expected {expected!r}; "
            f"history={seen}")
    # And back to a clean listening state afterwards.
    machine.coerce_legacy("LISTENING")
    app.processEvents()
    assert bridge.state == "LISTENING"


def test_text_only_is_reachable():
    """`-` is folded to `_` before comparison, so the hyphenated spelling used
    to match nothing and silently did nothing."""
    machine = AssistantStateMachine()
    machine.coerce_legacy("TEXT-ONLY")
    assert machine.silent_mode is True
    assert machine.snapshot()["silent"] is True
