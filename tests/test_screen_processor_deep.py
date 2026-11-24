"""100%-offline combinatorial tests for actions.screen_processor."""

from __future__ import annotations

import io
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

from actions import screen_processor as sp
from config.models import MODEL_FLASH, MODEL_FLASH_LITE


class FakeShot:
    def __init__(self, area):
        self.rgb = b"rgb"
        self.size = (int(area["width"]), int(area["height"]))


class FakeMSS:
    monitors = [
        {"left": 0, "top": 0, "width": 3840, "height": 1080},
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        {"left": 1920, "top": 0, "width": 1920, "height": 1080},
    ]
    grabbed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def grab(self, area):
        self.grabbed.append(dict(area))
        return FakeShot(area)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    FakeMSS.grabbed = []
    monkeypatch.setattr(sp, "_MSS_OK", True)
    monkeypatch.setattr(sp.mss, "MSS", FakeMSS, raising=False)
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), "blue").save(buf, format="PNG")
    monkeypatch.setattr(sp.mss.tools, "to_png", lambda rgb, size: buf.getvalue(), raising=False)
    monkeypatch.setattr(sp, "_CAMERA_INDEX_CACHE", {}, raising=False)
    monkeypatch.setattr(sp, "_SHARED_CAMERA_CHECK", False)
    yield


def _png(width=3200, height=1800):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "blue").save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Detail presets / JPEG quality
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("detail,expected,quality", [
    ("fast", (800, 450), 68),
    ("balanced", (1280, 720), 80),
    ("high", (1920, 1080), 88),
    ("ocr", (1920, 1080), 88),
])
def test_detail_presets_resize_and_set_quality(detail, expected, quality, monkeypatch):
    saved = {}
    original = Image.Image.save

    def capture(self, fp, **kwargs):
        saved.update(kwargs)
        return original(self, fp, **kwargs)

    monkeypatch.setattr(Image.Image, "save", capture)
    jpeg = sp._to_jpeg(_png(), detail)
    assert Image.open(io.BytesIO(jpeg)).size == expected
    assert saved["quality"] == quality


# ---------------------------------------------------------------------------
# Capture modes
# ---------------------------------------------------------------------------

def test_monitor_selection_and_clamping():
    sp._capture_screenshot(monitor=1, detail="fast")
    assert FakeMSS.grabbed[-1]["width"] == 1920
    sp._capture_screenshot(monitor=0, detail="fast")
    assert FakeMSS.grabbed[-1]["width"] == 3840
    sp._capture_screenshot(monitor=2, detail="fast")
    assert FakeMSS.grabbed[-1]["left"] == 1920
    sp._capture_screenshot(monitor=99, detail="fast")
    assert FakeMSS.grabbed[-1]["left"] == 1920


def test_active_window_crop(monkeypatch):
    monkeypatch.setattr(sp, "_active_window_rect", lambda: {
        "left": 100, "top": 100, "width": 600, "height": 400})
    sp._capture_screenshot(window=True, detail="fast")
    assert FakeMSS.grabbed[-1] == {"left": 100, "top": 100,
                                   "width": 600, "height": 400}


def test_region_capture_and_bad_bounds(monkeypatch):
    sp._capture_screenshot(region=[50, 60, 200, 100], detail="fast")
    assert FakeMSS.grabbed[-1] == {"left": 50, "top": 60,
                                   "width": 200, "height": 100}
    sp._capture_screenshot(region=[0, 0, -5, 100], detail="fast")
    assert FakeMSS.grabbed[-1]["width"] == 1920


# ---------------------------------------------------------------------------
# Shared camera and standalone release
# ---------------------------------------------------------------------------

def test_shared_vision_gesture_frame_reuses_camera(monkeypatch):
    monkeypatch.setattr(sp, "_SHARED_CAMERA_CHECK", True)
    packet = types.SimpleNamespace(frame="FRAME")
    camera = types.SimpleNamespace(latest=lambda: packet)
    service = types.SimpleNamespace(_running=True, _camera=camera)
    fake_vg = types.ModuleType("actions.vision_gesture")
    fake_vg._service = service
    monkeypatch.setitem(sys.modules, "actions.vision_gesture", fake_vg)
    import actions
    monkeypatch.setattr(actions, "vision_gesture", fake_vg, raising=False)
    opened = []

    class NoCamera:
        def __init__(self, *a, **k):
            opened.append(a)

    monkeypatch.setattr(sp.cv2, "VideoCapture", NoCamera, raising=False)
    monkeypatch.setattr(sp, "_frame_to_jpeg", lambda frame, detail: b"shared")
    assert sp._capture_camera() == b"shared"
    assert opened == []


class FakeCapture:
    opened, released = 0, 0

    def __init__(self, *args):
        FakeCapture.opened += 1

    def isOpened(self):
        return True

    def read(self):
        return True, "FRAME"

    def release(self):
        FakeCapture.released += 1


def test_standalone_camera_releases_and_never_writes_api_keys(monkeypatch, tmp_path):
    FakeCapture.opened = FakeCapture.released = 0
    config = tmp_path / "api_keys.json"
    config.write_text('{"gemini_api_key": "keep-me"}', encoding="utf-8")
    monkeypatch.setattr(sp, "API_CONFIG_PATH", config)
    monkeypatch.setattr(sp, "_shared_camera_frame", lambda: None)
    monkeypatch.setattr(sp.cv2, "VideoCapture", FakeCapture, raising=False)
    monkeypatch.setattr(sp, "_frame_to_jpeg", lambda frame, detail: b"camera")
    assert sp._capture_camera(camera_index=2) == b"camera"
    assert FakeCapture.opened == 1 and FakeCapture.released == 1
    assert config.read_text(encoding="utf-8") == '{"gemini_api_key": "keep-me"}'
    assert sp._get_camera_index() == 2


# ---------------------------------------------------------------------------
# Gemini routing, output options and safe defaults
# ---------------------------------------------------------------------------

def _fake_genai(monkeypatch):
    fake = types.ModuleType("google.genai")
    fake.types = types.SimpleNamespace(Part=types.SimpleNamespace(
        from_bytes=lambda *, data, mime_type: ("part", data, mime_type)))
    monkeypatch.setitem(sys.modules, "google.genai", fake)


def test_analyze_uses_flash_with_multipart_content(monkeypatch):
    _fake_genai(monkeypatch)
    calls = []
    monkeypatch.setattr(sp, "generate_with_retry", lambda **kw: (
        calls.append(kw) or types.SimpleNamespace(text="seen")))
    answer = sp._analyze_image_text(b"jpg", "image/jpeg", "What is this?")
    assert answer == "seen"
    assert calls[0]["model"] == MODEL_FLASH
    assert calls[0]["contents"][0][2] == "image/jpeg"
    assert calls[0]["api_version"] == "v1beta"


def test_quota_failover_to_flash_lite(monkeypatch):
    _fake_genai(monkeypatch)
    models = []

    def fake(**kwargs):
        models.append(kwargs["model"])
        if kwargs["model"] == MODEL_FLASH:
            raise RuntimeError("429 quota exceeded")
        return types.SimpleNamespace(text="fallback")

    monkeypatch.setattr(sp, "generate_with_retry", fake)
    assert sp._analyze_image_text(b"jpg", "image/jpeg", "q") == "fallback"
    assert models == [MODEL_FLASH, MODEL_FLASH_LITE]


def test_save_path_and_empty_question(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "_analyze_image_text", lambda *a, **k: "answer")
    target = tmp_path / "nested" / "shot.jpg"
    result = sp.screen_process({"text": "read", "save_path": str(target),
                                "angle": "screen", "detail": "fast"})
    assert result == "answer" and target.read_bytes().startswith(b"\xff\xd8")
    assert "question" in sp.screen_process(None).lower()


def test_window_angle_uses_active_window_capture(monkeypatch):
    monkeypatch.setattr(sp, "_active_window_rect", lambda: {
        "left": 10, "top": 20, "width": 300, "height": 200})
    monkeypatch.setattr(sp, "_analyze_image_text", lambda *a, **k: "ok")
    assert sp.screen_process({"text": "q", "angle": "window"}) == "ok"
    assert FakeMSS.grabbed[-1]["left"] == 10
