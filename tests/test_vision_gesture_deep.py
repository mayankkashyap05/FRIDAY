"""Fully offline combinatorial tests for actions.vision_gesture."""

from __future__ import annotations

import sys
import time
import types
from types import SimpleNamespace

import pytest

from actions import vision_gesture as vg


def _lm(x, y, z=0.0):
    return SimpleNamespace(x=x, y=y, z=z)


def _hand(flags=(False, False, False, False, False), *, handedness=None,
          thumb_pos=None):
    """Build 21 landmarks. flags = [thumb, index, middle, ring, pinky]."""
    pinky_mcp_x = 0.40 if str(handedness).lower().startswith("right") else 0.68
    points = [_lm(0.5, 0.9)]                      # 0 wrist
    # 1-4 thumb chain
    points += [_lm(0.52, 0.82), _lm(0.54, 0.78), _lm(0.55, 0.75)]
    if thumb_pos is not None:
        points.append(_lm(*thumb_pos[:2]))
    elif flags[0]:
        x = 0.85 if str(handedness).lower().startswith("right") else 0.25
        points.append(_lm(x, 0.65))
    else:
        folded_x = 0.45 if str(handedness).lower().startswith("right") else 0.59
        points.append(_lm(folded_x, 0.76))
    bases = [(5, 0.60), (9, 0.60), (13, 0.62), (17, 0.62)]
    tips = [8, 12, 16, 20]
    pips = [6, 10, 14, 18]
    for (mcp_idx, base_y), tip, pip in zip(bases, tips, pips):
        points.append(_lm(0.5, base_y))            # MCP
        points.append(_lm(0.5, base_y - 0.02))     # PIP
        points.append(_lm(0.5, base_y - 0.05))     # DIP
        points.append(_lm(0.5, base_y - 0.25 if False else base_y + 0.05))
    # Overwrite positions 5..20 exactly (indices above produced 5..20).
    for offset, (base_y, tip_idx, pip_idx) in enumerate(
        [(0.60, 8, 6), (0.60, 12, 10), (0.62, 16, 14), (0.62, 20, 18)]
    ):
        flag = flags[offset + 1]
        points[pip_idx] = _lm(0.5, base_y - 0.02)
        points[tip_idx] = _lm(0.52, base_y - 0.25 if flag else base_y + 0.08)
    points[17] = _lm(pinky_mcp_x, 0.62)
    assert len(points) == 21
    return points


def _service():
    service = vg.VisionService()
    service._speak = None
    service._player = None
    return service


# ---------------------------------------------------------------------------
# 1€ filter + active region
# ---------------------------------------------------------------------------

def test_one_euro_filter_suppresses_jitter():
    filt = vg._OneEuroFilter(min_cutoff=0.1, beta=0.0, d_cutoff=1.0)
    raw, smoothed = [], []
    for index in range(60):
        value = 0.5 + (0.01 if index % 2 else -0.01)
        raw.append(value)
        smoothed.append(filt(value, timestamp=index / 30.0))
    jitter_in = max(raw) - min(raw)
    jitter_out = max(smoothed[10:]) - min(smoothed[10:])
    assert jitter_out < jitter_in * 0.8


def test_one_euro_filter_tracks_fast_movement():
    filt = vg._OneEuroFilter(min_cutoff=1.0, beta=20.0, d_cutoff=1.0)
    value = None
    for index in range(12):
        value = filt(0.9 if index else 0.1, timestamp=index / 30.0)
    assert value is not None and abs(value - 0.9) < 0.08
    assert filt._alpha(1.0, 0.001) < filt._alpha(1.0, 1.0)


@pytest.mark.parametrize("value,expected", [(0.0, 0.0), (0.12, 0.0), (0.5, 0.5),
                                            (0.88, 1.0), (1.0, 1.0)])
def test_active_region_reaches_screen_corners(value, expected):
    assert vg._map_active_region(value) == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# Handedness-aware counting + gestures
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flags", [
    (False, False, False, False, False),
    (True, False, False, False, False),
    (True, True, False, False, False),
    (True, True, True, False, False),
    (True, True, True, True, False),
    (True, True, True, True, True),
])
@pytest.mark.parametrize("handedness", [None, "Left", "Right"])
def test_finger_count_matches_flags(flags, handedness):
    hand = _hand(flags, handedness=handedness)
    # Our fixture sets the thumb on the left for Left/None; for Right the
    # mirrored thumb position is used.
    assert vg._finger_count(hand, handedness) == sum(flags)


@pytest.mark.parametrize("flags,expected", [
    ((False, False, False, False, False), "fist"),
    ((True, True, True, True, True), "open hand"),
    ((False, True, False, False, False), "pointing"),
    ((False, True, True, False, False), "peace sign"),
    ((False, False, False, False, False,), "pinch / grab"),
])
def test_classify_basic_gestures(flags, expected):
    hand = _hand(flags)
    if expected == "pinch / grab":
        hand[4] = _lm(0.53, 0.61)
        hand[8] = _lm(0.52, 0.62)
    assert vg._classify_gesture(hand) == expected


def test_classify_thumbs_up_and_down():
    up = _hand((True, False, False, False, False), thumb_pos=(0.25, 0.60))
    assert vg._classify_gesture(up) == "thumbs up"
    down = _hand((True, False, False, False, False), thumb_pos=(0.25, 1.10))
    assert vg._classify_gesture(down) == "thumbs down"


def test_classify_ok_sign_and_pinch():
    ok_hand = _hand((True, False, True, True, True))
    ok_hand[4] = _lm(0.53, 0.61)
    ok_hand[8] = _lm(0.52, 0.62)
    assert vg._classify_gesture(ok_hand) == "ok sign"

    grab = _hand((True, False, False, False, False))
    grab[4] = _lm(0.53, 0.61)
    grab[8] = _lm(0.52, 0.62)
    assert vg._classify_gesture(grab) == "pinch / grab"


def test_three_four_finger_gestures_and_legacy_name():
    assert vg._classify_gesture(_hand((True, True, True, False, False))) == \
        "three fingers"
    assert vg._classify_gesture(_hand((True, True, True, True, False))) == \
        "four fingers"
    assert vg._gesture_name(3, False, False) == "three fingers"
    assert vg._gesture_name(0, True, False) == "pinch / grab"


def test_gesture_debounce_requires_three_frames_and_fires_once(monkeypatch):
    service = _service()
    seen = []
    monkeypatch.setattr(service, "_tell", lambda text, cooldown=0: seen.append(text))
    hand = _hand((False, True, True, False, False))
    for _ in range(2):
        service._handle_gesture([hand])
    assert seen == []
    service._handle_gesture([hand])
    assert len(seen) == 1 and "peace sign" in seen[0]
    service._handle_gesture([hand])
    assert len(seen) == 1

    service._handle_gesture([_hand((False, False, False, False, False))])
    assert service._gesture_frames == 1


# ---------------------------------------------------------------------------
# Air mouse, volume, focus, motion
# ---------------------------------------------------------------------------

class _FakePyAutoGUI(types.ModuleType):
    def __init__(self):
        super().__init__("pyautogui")
        self.calls = []
        self.size = lambda: (1920, 1080)

    def moveTo(self, x, y, duration=None):
        self.calls.append(("moveTo", x, y, duration))

    def click(self, *args, **kwargs):
        self.calls.append(("click", args, kwargs.get("button", "left")))

    def mouseDown(self):
        self.calls.append(("mouseDown",))

    def mouseUp(self):
        self.calls.append(("mouseUp",))

    def write(self, *args, **kwargs):
        self.calls.append(("write", args, kwargs))

    def press(self, *args):
        self.calls.append(("press", args))


def _airmouse_hand(thumb_index_distance=0.02, thumb_middle_distance=0.3):
    hand = _hand((False, False, False, False, False))
    hand[8] = _lm(0.50, 0.45)          # index fingertip
    hand[12] = _lm(0.70, 0.45)         # middle fingertip
    hand[4] = _lm(0.50 + thumb_index_distance, 0.45 + thumb_middle_distance / 10)
    return hand


def test_air_mouse_click_once_then_drag_and_release(monkeypatch):
    fake = _FakePyAutoGUI()
    monkeypatch.setattr(vg, "_get_pyautogui", lambda: fake)
    service = _service()
    monkeypatch.setattr(service, "_tell", lambda *a, **k: None)
    hand = _airmouse_hand()
    service._handle_air_mouse([hand])
    clicks = [call for call in fake.calls if call[0] == "click"]
    assert len(clicks) == 1
    assert fake.calls[0][0] == "moveTo" and fake.calls[0][3] == 0

    service._pinch_since -= 1.0
    service._handle_air_mouse([hand])
    assert any(call[0] == "mouseDown" for call in fake.calls)
    assert len([call for call in fake.calls if call[0] == "click"]) == 1

    released = _airmouse_hand(thumb_index_distance=0.6)
    service._handle_air_mouse([released])
    assert any(call[0] == "mouseUp" for call in fake.calls)
    assert service._drag_active is False and service._pinch_active is False


def test_air_mouse_right_click_on_middle_pinch(monkeypatch):
    fake = _FakePyAutoGUI()
    monkeypatch.setattr(vg, "_get_pyautogui", lambda: fake)
    service = _service()
    monkeypatch.setattr(service, "_tell", lambda *a, **k: None)
    hand = _hand((False, False, False, False, False))
    hand[4] = _lm(0.71, 0.46)
    hand[12] = _lm(0.70, 0.45)
    hand[8] = _lm(0.30, 0.50)
    service._handle_air_mouse([hand])
    assert any(call[0] == "click" and call[2] == "right" for call in fake.calls)
    service._handle_air_mouse([hand])
    assert len([c for c in fake.calls if c[0] == "click" and c[2] == "right"]) == 1


def test_volume_is_depth_normalized_and_has_deadband(monkeypatch):
    calls = []
    fake_settings = types.ModuleType("actions.computer_settings")
    fake_settings.volume_set = lambda value: calls.append(value)
    monkeypatch.setitem(sys.modules, "actions.computer_settings", fake_settings)
    service = _service()
    monkeypatch.setattr(service, "_tell", lambda *a, **k: None)

    def ratio_hand(ratio, scale=0.3):
        hand = _hand((False, False, False, False, False))
        hand[9] = _lm(0.5, 0.9 - scale)
        hand[4] = _lm(0.5, 0.85)
        hand[8] = _lm(0.5 + ratio * scale, 0.85)
        return hand

    service._handle_volume([ratio_hand(0.15)])
    first = calls[-1]
    service._handle_volume([ratio_hand(0.15, scale=0.9)])
    assert abs(calls[-1] - first) <= 3
    before = len(calls)
    service._handle_volume([ratio_hand(0.151, scale=0.9)])
    assert len(calls) == before


def test_focus_mode_alerts_after_grace_and_on_return(monkeypatch):
    service = _service()
    service._focus_grace = 0.01
    seen = []
    monkeypatch.setattr(service, "_tell", lambda text, cooldown=0: seen.append(text))
    service._handle_focus(None, [])
    time.sleep(0.02)
    service._handle_focus(None, [])
    assert any("left the frame" in text.lower() for text in seen)
    service._handle_focus(None, [_hand()])
    assert any("welcome back" in text.lower() for text in seen)


def test_motion_sensitivity_and_reset_on_mode_switch(monkeypatch):
    service = _service()
    seen = []
    monkeypatch.setattr(service, "_tell", lambda text, cooldown=0: seen.append(text))
    frame_a = __import__("numpy").zeros((120, 160, 3), dtype="uint8")
    frame_b = frame_a.copy()
    frame_b[:30, :30] = 255
    service._handle_motion(frame_a)
    service._handle_motion(frame_b, sensitivity=0.025)
    assert seen and "Motion detected" in seen[-1]
    seen.clear()
    service._prev_gray = None
    service._handle_motion(frame_a)
    service._handle_motion(frame_b, sensitivity=0.5)
    assert seen == []
    service._prev_gray = frame_a
    assert "focus" in service.set_mode("focus")
    assert service._prev_gray is None


# ---------------------------------------------------------------------------
# Camera sharing, timestamps, teardown, actions
# ---------------------------------------------------------------------------

def test_capture_frames_uses_shared_camera_when_running(monkeypatch):
    service = _service()
    service._running = True
    packets = [SimpleNamespace(sequence=index, frame=f"frame-{index}")
               for index in range(1, 5)]
    service._camera = SimpleNamespace(latest=lambda: packets.pop(0) if packets else None)
    monkeypatch.setattr(service, "_open_camera", lambda *a, **k: pytest.fail("second camera"))
    frames = service._capture_frames(3)
    assert frames == ["frame-1", "frame-2", "frame-3"]


def test_mediapipe_timestamps_are_strictly_monotonic(monkeypatch):
    service = _service()
    monkeypatch.setattr(vg.time, "monotonic", lambda: 1000.0)
    stamps = [service._next_mp_ts_ms() for _ in range(5)]
    assert stamps == sorted(set(stamps)) and len(stamps) == 5
    assert all(b > a for a, b in zip(stamps, stamps[1:]))


def test_loop_closes_mediapipe_tools_on_teardown(monkeypatch):
    service = _service()

    class FakeCamera:
        def __init__(self, _config):
            self.stops = 0

        def start(self):
            return True

        def latest(self):
            return None

        def stop(self):
            self.stops += 1

    class FakeTool:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    camera = FakeCamera(None)
    holistic, faces = FakeTool(), FakeTool()
    monkeypatch.setattr(vg, "CameraManager", lambda config: camera)
    monkeypatch.setattr(service, "_build_tools", lambda: setattr(
        service, "_holistic", holistic) or setattr(service, "_faces", faces) or True)
    monkeypatch.setattr(vg.time, "sleep", lambda _: setattr(service, "_running", False))
    service._running = True
    service._loop()
    assert holistic.closed and faces.closed
    assert service._holistic is None and service._faces is None
    assert camera.stops == 1 and service._running is False


def test_action_aliases_and_parameter_shapes(monkeypatch):
    monkeypatch.setattr(vg._service, "set_mode", lambda mode: f"switched {mode}")
    assert "switched focus" in vg.vision_gesture({"action": "mode", "mode": "focus"})
    status = vg.vision_gesture({"action": "status", "sensitivity": 0.05, "grace": 3})
    assert "Vision service is" in status
    assert vg.vision_gesture(None).startswith("Vision service")
    assert "Unknown vision action" in vg.vision_gesture({"action": "bogus"})
    assert "focus" in vg.MODES


def test_camera_index_reads_config(monkeypatch, tmp_path):
    monkeypatch.setattr(vg, "BASE_DIR", tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "api_keys.json").write_text('{"camera_index": 2}')
    assert _service()._camera_index() == 2
