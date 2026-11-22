"""Phase 2/3 tests — WebSocket transport, bridge, frame pump, lifecycle.

Everything here runs headless: the UI is exposed over a loopback WebSocket and
the Electron window is optional, so the full Python side (token auth, snapshot +
delta protocol, 60 Hz amplitude throttling, binary JPEG frames with
latest-frame-wins backpressure, ``root.after`` from foreign threads, API-key
bootstrap, smart reading-grace dismissal, idempotent shutdown) is exercised with
no GUI stack at all.

Run: ``python -m pytest tests/test_ui_electron.py -q``
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from _archive.ui_legacy import FridayUI
from _archive.ui_legacy import protocol
from _archive.ui_legacy.bridge import UiBridge, UiDispatcher
from _archive.ui_legacy.controller import FramePump, UiController
from _archive.ui_legacy.event_bus import Card, CardKind, EventBus
from _archive.ui_legacy.state_machine import AssistantStateMachine

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "ui" / "app"
TOKEN = "test-token-abcdefghijklmnopqrstuvwxyz"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _connect(url: str, token: str = TOKEN, **kwargs):
    from websockets.sync.client import connect

    separator = "&" if "?" in url else "?"
    return connect(f"{url}{separator}token={token}", open_timeout=5,
                   close_timeout=1, **kwargs)


def _next_json(ws, timeout: float = 5.0) -> dict:
    raw = ws.recv(timeout=timeout)
    assert isinstance(raw, str), f"expected JSON, got binary: {raw!r}"
    return json.loads(raw)


def _wait_for(ws, predicate, timeout: float = 5.0) -> dict:
    """Read messages until ``predicate(message)`` is true."""
    deadline = time.monotonic() + timeout
    seen: list[dict] = []
    while time.monotonic() < deadline:
        message = _next_json(ws, timeout=max(0.1, deadline - time.monotonic()))
        seen.append(message)
        if predicate(message):
            return message
    raise AssertionError(f"no matching message; saw {seen[-8:]}")


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@pytest.fixture()
def controller(tmp_path, monkeypatch):
    monkeypatch.setattr("ui.API_FILE", tmp_path / "api_keys.json")
    instance = UiController(enable_overlay=False, token=TOKEN)
    instance.start()
    try:
        yield instance
    finally:
        instance.shutdown()


# ─────────────────────────────────────────────────────────────────────────────
# Token authentication
# ─────────────────────────────────────────────────────────────────────────────

def test_ws_server_binds_loopback_only(controller):
    stats = controller.ws.stats()
    assert stats["port"] > 0
    assert controller.ws.host == "127.0.0.1"
    assert controller.ws_url == f"ws://127.0.0.1:{stats['port']}"


def test_ws_accepts_a_valid_token_and_pushes_a_snapshot(controller):
    with _connect(controller.ws_url) as ws:
        message = _next_json(ws)
    assert message["t"] == "snapshot"
    for key in ("state", "cards", "activities", "transcript", "persona",
                "vision", "arcade", "setup", "inspector", "theme", "wake"):
        assert key in message, f"snapshot is missing {key!r}"
    assert message["state"]["state"] in {
        "BOOTING", "LISTENING", "THINKING", "PROCESSING", "SPEAKING", "MUTED"}
    # The server observes the close asynchronously; wait for it rather than
    # racing the socket thread on a busy CI machine.
    assert _wait_until(lambda: controller.ws.stats()["clients"] == 0, timeout=2), (
        "the client never left the server's connection table")


def test_ws_rejects_an_invalid_token(controller):
    import websockets.exceptions as ws_errors

    ConnectionClosed = ws_errors.ConnectionClosed
    # ``InvalidStatus`` is the modern name; websockets < 14 only has the (then
    # non-deprecated) ``InvalidStatusCode``. Resolve them without ever touching
    # the deprecated ``websockets.legacy`` shim, so this test is warning-free on
    # every supported version.
    rejected_exception = getattr(ws_errors, "InvalidStatus", None)
    if rejected_exception is None:  # pragma: no cover - websockets < 14
        rejected_exception = ws_errors.InvalidStatusCode
    bad_status = (rejected_exception, ConnectionClosed)

    with pytest.raises(bad_status + (ConnectionClosed,)) as excinfo:
        with _connect(controller.ws_url, token="wrong-token") as ws:
            ws.recv(timeout=2)

    error = excinfo.value
    if isinstance(error, ConnectionClosed):
        assert error.rcvd is not None and error.rcvd.code == protocol.CLOSE_UNAUTHORIZED
    assert controller.ws.stats()["rejected"] >= 1


def test_ws_rejects_a_missing_token(controller):
    from websockets.exceptions import ConnectionClosed, InvalidStatus
    from websockets.sync.client import connect

    with pytest.raises((InvalidStatus, ConnectionClosed)):
        with connect(controller.ws_url, open_timeout=5) as ws:
            ws.recv(timeout=2)


def test_ws_accepts_the_token_as_a_bearer_header(controller):
    from websockets.sync.client import connect

    with connect(controller.ws_url, additional_headers={
            "Authorization": f"Bearer {TOKEN}"}) as ws:
        assert _next_json(ws)["t"] == "snapshot"


def test_token_is_per_launch_and_256_bit():
    first = protocol.new_token()
    second = protocol.new_token()
    assert first != second
    assert len(first) >= 43, "token_urlsafe(32) is 43 chars"


# ─────────────────────────────────────────────────────────────────────────────
# Delta protocol
# ─────────────────────────────────────────────────────────────────────────────

def test_transcript_delta_is_broadcast(controller):
    with _connect(controller.ws_url) as ws:
        _next_json(ws)  # snapshot
        controller.dispatcher.call(controller.bridge.ingest, "SYS: hello there")
        message = _wait_for(ws, lambda m: m["t"] == "transcript_append")
    assert message["text"] == "hello there"
    assert message["channel"] == "system"


def test_state_delta_is_broadcast(controller):
    with _connect(controller.ws_url) as ws:
        _next_json(ws)
        controller.dispatcher.call(controller.state.coerce_legacy, "THINKING")
        message = _wait_for(ws, lambda m: m["t"] == "state"
                            and m.get("state") == "THINKING")
    assert message["statusLabel"] in ("THINKING", "TEXT-ONLY", "MUTED")
    assert 0.0 <= message["energy"] <= 1.0


def test_cards_and_activities_delta(controller):
    with _connect(controller.ws_url) as ws:
        _next_json(ws)
        controller.dispatcher.call(
            controller.bridge.ingest,
            {"type": "weather", "city": "Dibrugarh", "temp_c": 29})
        cards = _wait_for(ws, lambda m: m["t"] == "cards")
        assert cards["items"][0]["kind"] == "weather"
        assert cards["items"][0]["payload"]["temp_c"] == 29

        controller.dispatcher.call(controller.bridge.ingest,
                                   "[Code] Build started...")
        activities = _wait_for(ws, lambda m: m["t"] == "activities")
        assert activities["activeCount"] >= 1
        assert activities["items"][0]["kind"] == "code"


def test_snapshot_tracks_state_after_a_disconnect(controller):
    with _connect(controller.ws_url) as ws:
        _next_json(ws)
    controller.dispatcher.call(controller.bridge.write_log if False else
                               controller.bridge.ingest, "You: open notepad")
    controller.dispatcher.call(controller.state.coerce_legacy, "PROCESSING")
    assert _wait_until(lambda: len(controller.bridge.transcript.items()) >= 1)

    with _connect(controller.ws_url) as ws:
        snapshot = _next_json(ws)
    assert snapshot["state"]["state"] == "PROCESSING"
    assert snapshot["transcript"], "reconnect snapshot must include history"


def test_renderer_messages_reach_the_backend(controller):
    """A typed turn lands in the backend's ``on_text_command`` sink."""
    seen: list[str] = []
    delivered = threading.Event()

    def on_text_command(text: str) -> None:
        seen.append(text)
        delivered.set()

    controller.on_text_command = on_text_command

    with _connect(controller.ws_url) as ws:
        _next_json(ws)
        ws.send(json.dumps({"t": protocol.C_SUBMIT_TEXT, "text": "what time is it"}))
        assert _wait_until(lambda: controller.bridge.state == "THINKING")
        assert delivered.wait(5), "the command never reached the backend"

    assert seen == ["what time is it"]


def test_renderer_intents_toggle_mute_and_clear_cards(controller):
    with _connect(controller.ws_url) as ws:
        _next_json(ws)
        controller.dispatcher.call(
            controller.bridge.ingest, {"type": "text", "title": "Hi", "body": "x"})
        _wait_for(ws, lambda m: m["t"] == "cards")
        assert controller.bridge.cardCount == 1

        ws.send(json.dumps({"t": protocol.C_CLEAR_CARDS}))
        assert _wait_until(lambda: controller.bridge.cardCount == 0)

        ws.send(json.dumps({"t": protocol.C_TOGGLE_MUTE}))
        assert _wait_until(lambda: controller.bridge.muted)


def test_window_dismissed_hides_the_stage(controller):
    controller.show()
    assert _wait_until(lambda: controller.visible)
    with _connect(controller.ws_url) as ws:
        _next_json(ws)
        ws.send(json.dumps({"t": protocol.C_WINDOW_DISMISSED}))
        assert _wait_until(lambda: not controller.visible)


def test_cancel_hides_the_stage_and_clears_the_live_transcript(controller):
    """Escape is a dismissal: cancel the turn *and* contract the overlay."""
    controller.show()
    assert _wait_until(lambda: controller.visible)
    controller.bridge.set_live_transcript("reading the previous line")
    assert controller.bridge.lastEvent == "reading the previous line"

    with _connect(controller.ws_url) as ws:
        _next_json(ws)
        ws.send(json.dumps({"t": protocol.C_CANCEL}))
        cleared = _wait_for(
            ws, lambda m: m["t"] == protocol.T_LIVE_TRANSCRIPT and m["text"] == "",
            timeout=5)

    assert cleared["text"] == ""
    assert _wait_until(lambda: not controller.visible)
    assert controller.bridge.invoked is False
    assert controller.bridge.lastEvent == ""


def test_live_transcript_is_cleared_on_plain_dismissal(controller):
    controller.show()
    assert _wait_until(lambda: controller.visible)
    controller.bridge.set_live_transcript("still on screen")
    controller.hide()
    assert _wait_until(lambda: not controller.visible)
    assert controller.bridge.lastEvent == ""


def test_unknown_message_types_are_ignored(controller):
    with _connect(controller.ws_url) as ws:
        _next_json(ws)
        ws.send(json.dumps({"t": "definitely_not_real"}))
        ws.send("not json at all")
        ws.send(json.dumps({"t": protocol.C_PING}))
        pong = _wait_for(ws, lambda m: m["t"] == "pong")
    assert "ts" in pong


# ─────────────────────────────────────────────────────────────────────────────
# Amplitude throttling
# ─────────────────────────────────────────────────────────────────────────────

def _bridge() -> tuple[UiBridge, list[tuple[str, dict]]]:
    seen: list[tuple[str, dict]] = []
    dispatcher = UiDispatcher()
    dispatcher.bind_ui_thread()
    bridge = UiBridge(AssistantStateMachine(), EventBus(), dispatcher)
    bridge.on_emit = lambda kind, payload: seen.append((kind, payload))
    return bridge, seen


def test_amplitude_is_capped_at_60_hz():
    bridge, seen = _bridge()
    started = time.monotonic()
    for i in range(600):
        bridge.setAmplitude((i % 100) / 100.0)
    elapsed = time.monotonic() - started
    amplitudes = [payload for kind, payload in seen if kind == "amplitude"]
    assert elapsed < 0.5, "the loop itself must be fast for this bound to mean anything"
    assert len(amplitudes) <= int(elapsed * 60) + 2, (
        f"{len(amplitudes)} updates in {elapsed:.3f}s exceeds the 60 Hz cap")
    assert len(amplitudes) >= 1


def test_amplitude_ignores_tiny_changes():
    bridge, seen = _bridge()
    bridge.setAmplitude(0.5)
    before = len(seen)
    for delta in (0.001, 0.002, -0.003):
        bridge.setAmplitude(0.5 + delta)
    assert len(seen) == before, "sub-epsilon jitter must not be broadcast"


def test_amplitude_clamps_out_of_range_input():
    bridge, _ = _bridge()
    bridge.setAmplitude(9.0)
    assert bridge.micAmplitude == 1.0
    bridge.setAmplitude(-4.0)
    assert bridge.micAmplitude == 0.0
    bridge.setAmplitude("nan")  # must not raise
    bridge.setAmplitude(None)


# ─────────────────────────────────────────────────────────────────────────────
# Binary frames
# ─────────────────────────────────────────────────────────────────────────────

def _frame(width: int = 64, height: int = 48):
    np = pytest.importorskip("numpy")
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :, 2] = 255          # BGR red
    return frame


def test_encode_jpeg_accepts_bgr_numpy_and_keeps_colour():
    np = pytest.importorskip("numpy")
    from io import BytesIO

    from PIL import Image

    data = protocol.encode_jpeg(_frame())
    assert data and data[:2] == b"\xff\xd8", "not a JPEG (missing SOI)"
    image = Image.open(BytesIO(data)).convert("RGB")
    red, green, blue = image.getpixel((10, 10))
    assert red > 200 and green < 60 and blue < 60, (
        f"BGR input must render as red, got {(red, green, blue)}")


def test_encode_jpeg_accepts_pil_and_raw_bytes():
    PIL = pytest.importorskip("PIL.Image")
    image = PIL.new("RGB", (20, 10), (10, 200, 30))
    data = protocol.encode_jpeg(image)
    assert data[:2] == b"\xff\xd8"
    assert protocol.encode_jpeg(data) == data, "encoded bytes pass through"


def test_encode_jpeg_downsamples_large_frames():
    np = pytest.importorskip("numpy")
    from io import BytesIO

    from PIL import Image

    frame = np.zeros((1200, 2000, 3), dtype=np.uint8)
    data = protocol.encode_jpeg(frame)
    size = Image.open(BytesIO(data)).size
    assert max(size) <= protocol.JPEG_MAX_SIDE


def test_encode_jpeg_rejects_junk_without_raising():
    for junk in (None, "nope", 42, object(), b""):
        assert protocol.encode_jpeg(junk) is None


def test_binary_frame_carries_the_stream_prefix(controller):
    controller.dispatcher.call(controller.bridge.set_vision_preview_open, True)
    controller.dispatcher.call(controller.bridge.openInspector, "camera")
    assert _wait_until(lambda: controller.bridge.visionPreviewOpen)

    with _connect(controller.ws_url) as ws:
        _next_json(ws)

        # Frames are only produced for an open consumer; generously retry in
        # case the inspector delta is still in flight.
        deadline = time.monotonic() + 5
        payload = None
        while time.monotonic() < deadline and payload is None:
            controller.push_frame("vision", _frame())
            raw = ws.recv(timeout=1)
            if isinstance(raw, (bytes, bytearray)):
                payload = bytes(raw)
                break

    assert payload is not None, "no binary frame was delivered"
    stream, jpeg = protocol.split_frame(payload)
    assert stream == protocol.STREAM_VISION
    assert jpeg[:2] == b"\xff\xd8"


def test_frames_are_not_encoded_when_nobody_is_watching(controller):
    before = controller.frames.stats()["encoded"]
    for _ in range(5):
        assert controller.push_frame("vision", _frame()) is False
    time.sleep(0.05)
    assert controller.frames.stats()["encoded"] == before


def test_frame_pump_drops_stale_frames_under_backpressure():
    """Latest-frame-wins: a slow sender must never build a queue."""
    sent: list[bytes] = []
    gate = threading.Event()

    def slow_sender(stream: int, jpeg: bytes) -> None:
        gate.wait(0.2)
        sent.append(jpeg)

    pump = FramePump(slow_sender)
    pump.start()
    try:
        frame = _frame()
        for _ in range(200):
            pump.push("vision", frame)
        assert _wait_until(lambda: pump.stats()["encoded"] >= 1, timeout=5)
        assert pump.stats()["dropped"] > 0, pump.stats()
        assert len(sent) <= 5, "the pump encoded far more frames than it sent"
    finally:
        gate.set()
        pump.stop()


def test_frame_pump_encodes_off_the_producer_thread():
    from _archive.ui_legacy.protocol import STREAM_VISION

    threads: list[int] = []
    pump = FramePump(lambda stream, jpeg: threads.append(threading.get_ident()))
    pump.start()
    try:
        pump.push("vision", _frame())
        assert _wait_until(lambda: bool(threads), timeout=5)
    finally:
        pump.stop()
    assert threads[0] != threading.get_ident()
    assert STREAM_VISION == 1


# ─────────────────────────────────────────────────────────────────────────────
# root.after / API key bootstrap (facade behaviour)
# ─────────────────────────────────────────────────────────────────────────────

def test_root_after_with_a_delay_runs_on_the_ui_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_UI_HEADLESS", "1")
    monkeypatch.setattr("ui.API_FILE", tmp_path / "keys.json")
    monkeypatch.setattr("ui.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("ui.THEME_FILE", tmp_path / "ui_theme.json")
    facade = FridayUI("face.png")
    try:
        facade.controller.start()
        ran = threading.Event()
        seen_thread: list[int] = []

        def callback() -> None:
            seen_thread.append(threading.get_ident())
            ran.set()

        def poster() -> None:
            facade.root.after(20, callback)

        thread = threading.Thread(target=poster)
        thread.start()
        thread.join()
        assert ran.wait(5), "delayed root.after never ran"
        assert seen_thread[0] != threading.get_ident(), (
            "the callback must run on the UI loop, not the poster thread")
        assert facade.root.after_cancel("token") is None
    finally:
        facade.quit()
        facade.controller.shutdown()


def test_wait_for_api_key_unblocks_when_a_key_appears(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_UI_HEADLESS", "1")
    monkeypatch.setattr("ui.API_FILE", tmp_path / "api_keys.json")
    monkeypatch.setattr("ui.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("ui.THEME_FILE", tmp_path / "ui_theme.json")
    facade = FridayUI("face.png")
    try:
        facade.controller.start()
        assert facade._api_key_ready is False

        def add_key() -> None:
            time.sleep(0.05)
            (tmp_path / "api_keys.json").write_text(
                '{"gemini_api_key": "abc123"}', encoding="utf-8")

        thread = threading.Thread(target=add_key)
        thread.start()
        facade.wait_for_api_key()
        thread.join()

        assert facade._api_key_ready is True
        assert facade.diagnostics()["api_key_ready"] is True
    finally:
        facade.quit()
        facade.controller.shutdown()


def test_submit_api_key_writes_the_config(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_UI_HEADLESS", "1")
    monkeypatch.setattr("ui.API_FILE", tmp_path / "api_keys.json")
    monkeypatch.setattr("ui.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("ui.THEME_FILE", tmp_path / "ui_theme.json")
    facade = FridayUI("face.png")
    try:
        facade.controller.start()
        assert facade.submit_api_key("   ") is False
        assert facade.submit_api_key(" good-key ") is True
        assert json.loads((tmp_path / "api_keys.json").read_text()) == {
            "gemini_api_key": "good-key"}
    finally:
        facade.quit()
        facade.controller.shutdown()


# ─────────────────────────────────────────────────────────────────────────────
# Smart reading-grace dismissal
# ─────────────────────────────────────────────────────────────────────────────

def _settle_listening(controller) -> None:
    """The controller alone starts BOOTING; main.py always drives LISTENING."""
    controller.dispatcher.call(controller.state.coerce_legacy, "LISTENING")
    assert _wait_until(lambda: controller.bridge.state == "LISTENING")


def test_reading_grace_dismisses_an_idle_stage(controller):
    _settle_listening(controller)
    controller.show()
    assert _wait_until(lambda: controller.visible)
    # Keep the test fast: the production grace is 4.5–14 s.
    controller._dismissal_grace_ms = 20
    controller.dispatcher.call(lambda: setattr(
        controller, "_dismiss_deadline", time.monotonic() + 0.02))
    assert _wait_until(lambda: controller._dismiss_deadline is not None)
    time.sleep(0.05)
    controller.dispatcher.call(controller._check_dismissal)
    assert _wait_until(lambda: not controller.visible), (
        "an idle stage must dissolve after the reading grace")


def test_reading_grace_is_blocked_by_a_confirmation_card(controller):
    _settle_listening(controller)
    controller.show()
    assert _wait_until(lambda: controller.visible)
    controller.dispatcher.call(
        controller.bridge.ingest,
        {"type": "confirmation", "action": "delete the folder"})
    assert _wait_until(lambda: controller.bridge.awaitingConfirmation)
    controller.dispatcher.call(lambda: setattr(
        controller, "_dismiss_deadline", time.monotonic() - 1))
    controller.dispatcher.call(controller._check_dismissal)
    time.sleep(0.05)
    assert controller.visible, "never dissolve while a decision is pending"


def test_pointer_or_typing_keeps_the_stage_alive(controller):
    _settle_listening(controller)
    controller.show()
    assert _wait_until(lambda: controller.visible)
    controller._pointer_inside = True
    controller.dispatcher.call(lambda: setattr(
        controller, "_dismiss_deadline", time.monotonic() - 1))
    controller.dispatcher.call(controller._check_dismissal)
    time.sleep(0.05)
    assert controller.visible
    controller._pointer_inside = False
    controller.dispatcher.call(lambda: setattr(
        controller, "_dismiss_deadline", time.monotonic() - 1))
    controller.dispatcher.call(controller._check_dismissal)
    assert _wait_until(lambda: not controller.visible)


def test_reading_grace_scales_with_content_length(controller):
    _settle_listening(controller)
    short = controller._reading_grace_ms()
    controller.dispatcher.call(
        controller.bridge.ingest,
        "Friday: " + ("a long answer " * 120))
    assert _wait_until(lambda: controller.bridge.cardCount > 0), (
        "the long answer should have produced a document card")
    long = controller._reading_grace_ms()
    assert 4500 <= short <= 14000
    assert long > short
    assert long <= 14000


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def test_headless_never_spawns_electron(controller):
    assert controller._electron is None
    diagnostics = controller.diagnostics()
    assert diagnostics["headless"] is True
    assert diagnostics["electron"]["running"] is False
    assert diagnostics["ws"]["running"] is True


def test_shutdown_is_idempotent(controller):
    with _connect(controller.ws_url) as ws:
        _next_json(ws)
    controller.shutdown()
    controller.shutdown()          # must be a no-op, not an exception
    assert controller.ws.running is False
    assert controller.dispatcher.running is False


def test_shutdown_farewell_is_logged_exactly_once(controller, capsys):
    """Every teardown path may race here; the user sees one line, not three."""
    capsys.readouterr()  # drop anything the fixture printed while starting

    controller.start()
    threading.Thread(target=controller.request_quit, daemon=True).start()
    assert controller.wait(5)

    controller.request_quit()   # already closed: must stay silent
    controller.shutdown()       # idempotent: must stay silent

    farewell = "Tray stopped, audio restored, UI closed."
    output = capsys.readouterr().out
    assert output.count(farewell) == 1, output


def test_shutdown_message_reaches_the_renderer(controller):
    with _connect(controller.ws_url) as ws:
        _next_json(ws)
        controller.shutdown()
        message = _wait_for(ws, lambda m: m["t"] == "shutdown", timeout=5)
    assert message == {"t": "shutdown"}


def test_diagnostics_shape(controller):
    data = controller.diagnostics()
    assert {"theme", "ws", "frames", "electron", "bus", "dispatcher",
            "win32", "headless"} <= set(data)
    assert data["theme"] == "siri"


def test_request_quit_unblocks_wait(controller):
    controller.start()
    threading.Thread(target=controller.request_quit, daemon=True).start()
    assert controller.wait(5), "request_quit must release the main loop"


def test_make_ui_fails_loudly_without_electron(monkeypatch, capsys):
    """No build, no Electron, no silent blank window."""
    monkeypatch.delenv("FRIDAY_UI_HEADLESS", raising=False)
    monkeypatch.setattr("ui.controller.electron_executable", lambda *a, **k: None)

    import _archive.ui_legacy as ui_legacy

    with pytest.raises(SystemExit) as excinfo:
        ui_legacy.make_ui("face.png")
    message = str(excinfo.value)
    assert "npm install" in message and "npm run build" in message
    assert str(APP) in message
    combined = "".join(capsys.readouterr())
    assert "Traceback (most recent call last)" in combined


# ─────────────────────────────────────────────────────────────────────────────
# Install diagnostics: "the package exists" is not "the binary exists"
# ─────────────────────────────────────────────────────────────────────────────

def test_electron_executable_resolves_through_the_packages_path_txt(tmp_path):
    """The electron package names its binary in path.txt; trust that first."""
    from _archive.ui_legacy.controller import electron_executable

    pkg = tmp_path / "node_modules" / "electron"
    dist = pkg / "dist"
    dist.mkdir(parents=True)
    name = "electron.exe" if os.name == "nt" else "electron"
    binary = dist / name
    binary.write_bytes(b"MZ")
    (pkg / "path.txt").write_text(name + "\n", encoding="utf-8")

    assert electron_executable(tmp_path) == binary


def test_electron_executable_never_returns_the_cmd_shim(tmp_path):
    """A batch shim would leave a zombie Electron behind and flash a console."""
    from _archive.ui_legacy.controller import electron_executable

    dist = tmp_path / "node_modules" / "electron" / "dist"
    dist.mkdir(parents=True)
    (dist / "electron.cmd").write_text("@echo off\r\n", encoding="utf-8")

    assert electron_executable(tmp_path) is None


def test_renderer_built_requires_both_build_artefacts(tmp_path):
    from _archive.ui_legacy.controller import renderer_built

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    assert renderer_built(tmp_path) is False, \
        "index.html without dist/electron/main.js cannot boot Electron"

    (dist / "electron").mkdir()
    (dist / "electron" / "main.js").write_text("// main", encoding="utf-8")
    assert renderer_built(tmp_path) is True


def test_install_status_separates_a_missing_binary_from_a_missing_build(tmp_path):
    from _archive.ui_legacy.controller import ui_install_status

    empty = ui_install_status(tmp_path)
    assert empty["renderer"] is False
    assert empty["package"] is False
    assert empty["binary"] == ""

    (tmp_path / "node_modules" / "electron" / "dist").mkdir(parents=True)
    (tmp_path / "dist" / "electron").mkdir(parents=True)
    (tmp_path / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "dist" / "electron" / "main.js").write_text("//", encoding="utf-8")

    half = ui_install_status(tmp_path)
    assert half["renderer"] is True
    assert half["package"] is True
    assert half["binary"] == "", "a package without its binary is still not runnable"
    assert half["binaryExpected"].endswith(("electron.exe", "electron"))


def test_missing_ui_help_names_the_real_repair(tmp_path):
    """npm exits 0 even when Electron's download fails; say what to run."""
    from _archive.ui_legacy.controller import missing_ui_help

    (tmp_path / "node_modules" / "electron" / "dist").mkdir(parents=True)
    message = missing_ui_help(tmp_path)

    assert "electron binary" in message
    assert "npm rebuild electron" in message, "the package-without-binary case needs its own fix"
    assert str(tmp_path) in message
    # The generic first-run commands must stay, for a fresh clone.
    assert "npm install" in message and "npm run build" in message


def test_setup_verifies_the_electron_binary_instead_of_trusting_npm():
    source = (REPO / "setup.py").read_text(encoding="utf-8")
    assert "ensure_electron_binary" in source
    assert "rebuild" in source

    package = json.loads((APP / "package.json").read_text(encoding="utf-8"))
    assert "postinstall" in package["scripts"], \
        "npm install must re-check the Electron binary"
    assert (APP / "scripts" / "ensure-electron.mjs").is_file()


def test_make_ui_does_not_stack_a_second_generic_install_block(monkeypatch):
    """One diagnosis, one set of commands — not two contradicting ones."""
    monkeypatch.delenv("FRIDAY_UI_HEADLESS", raising=False)
    monkeypatch.setattr("ui.controller.electron_executable", lambda *a, **k: None)

    import _archive.ui_legacy as ui_legacy

    with pytest.raises(SystemExit) as excinfo:
        ui_legacy.make_ui("face.png")
    message = str(excinfo.value)
    assert "Install and build the frontend with:" not in message
    assert "electron binary" in message


# ─────────────────────────────────────────────────────────────────────────────
# Frontend asset integrity (the renderer is the UI; a missing file is fatal)
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_APP_FILES = (
    "package.json",
    "tsconfig.json",
    "tsconfig.electron.json",
    "vite.config.mts",
    "index.html",
    "electron/main.ts",
    "electron/preload.ts",
    "src/main.tsx",
    "src/index.css",
    "src/types/protocol.ts",
    "src/hooks/useFridaySocket.ts",
    "src/hooks/useInteractiveRegion.ts",
    "src/shaders/siriOrb.frag.glsl",
    "src/components/SiriOrb.tsx",
    "src/components/StageApp.tsx",
    "src/components/SnippetStack.tsx",
    "src/components/LiveActivityBar.tsx",
    "src/components/LiveTranscript.tsx",
    "src/components/TypeToSiriPill.tsx",
    "src/components/SetupCard.tsx",
    "src/components/InspectorApp.tsx",
    "src/components/DevPreviewPanel.tsx",
)

REQUIRED_CARDS = (
    "BaseGlassCard.tsx",
    "TextCard.tsx",
    "WeatherBriefingCard.tsx",
    "CalendarCard.tsx",
    "EmailDigestCard.tsx",
    "FlightCard.tsx",
    "TelemetryCard.tsx",
    "ControlPillCard.tsx",
    "DocumentQACard.tsx",
    "ConfirmationCard.tsx",
)


@pytest.mark.parametrize("relative", REQUIRED_APP_FILES)
def test_frontend_asset_exists(relative):
    assert (APP / relative).is_file(), f"ui/app/{relative} is missing"


@pytest.mark.parametrize("name", REQUIRED_CARDS)
def test_card_component_exists(name):
    assert (APP / "src" / "components" / "cards" / name).is_file()


def test_package_json_declares_the_required_stack():
    package = json.loads((APP / "package.json").read_text(encoding="utf-8"))
    dependencies = {**package.get("dependencies", {}),
                    **package.get("devDependencies", {})}
    for name in ("electron", "vite", "react", "react-dom", "typescript",
                 "tailwindcss", "@tailwindcss/vite", "motion",
                 "lucide-react", "vitest", "@testing-library/react"):
        assert name in dependencies, f"package.json is missing {name}"
    scripts = package["scripts"]
    for script in ("dev", "build", "test", "typecheck"):
        assert script in scripts, f"package.json is missing the {script!r} script"


def test_vite_build_targets_the_electron_loader():
    package = json.loads((APP / "package.json").read_text(encoding="utf-8"))
    assert package.get("main") == "dist/electron/main.js"
    config = (APP / "vite.config.mts").read_text(encoding="utf-8")
    assert "base: './'" in config, "file:// loading needs relative asset URLs"
    assert "tailwindcss" in config
    assert "emptyOutDir: false" in config, (
        "vite must not delete the compiled Electron entrypoints")


def test_electron_security_defaults():
    main = (APP / "electron" / "main.ts").read_text(encoding="utf-8")
    preload = (APP / "electron" / "preload.ts").read_text(encoding="utf-8")
    for needle in ("contextIsolation: true", "nodeIntegration: false",
                   "sandbox: true", "transparent: true", "frame: false",
                   "skipTaskbar: true"):
        assert needle in main, f"electron/main.ts is missing {needle}"
    assert "setIgnoreMouseEvents" in main
    assert "contextBridge.exposeInMainWorld" in preload
    assert "ipcRenderer.invoke" in preload


def test_shader_is_glsl_300_es_and_premultiplies_alpha():
    shader = (APP / "src" / "shaders" / "siriOrb.frag.glsl").read_text(
        encoding="utf-8")
    assert "#version 300 es" in shader
    assert "precision highp float;" in shader
    assert "uVoice" in shader and "uEnergy" in shader and "uTime" in shader
    assert "vec4(color * alpha, alpha)" in shader, (
        "premultiplied output is what keeps the halo fringe-free")


def test_orb_canvas_contract():
    orb = (APP / "src" / "components" / "SiriOrb.tsx").read_text(encoding="utf-8")
    assert "premultipliedAlpha: true" in orb
    assert "devicePixelRatio" in orb
    assert "webglcontextlost" in orb
    assert "webglcontextrestored" in orb
    assert "reducedMotion" in orb or "reduced-motion" in orb
