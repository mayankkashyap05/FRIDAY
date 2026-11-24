"""Fully offline combinatorial tests for actions.vision_click."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from actions import vision_click as vc
from config.models import MODEL_FLASH


@pytest.fixture(autouse=True)
def _no_real_input(monkeypatch):
    class FakeGUI(types.ModuleType):
        def __init__(self):
            super().__init__("pyautogui")
            self.calls = []
            self.size = lambda: (1920, 1080)

        def _add(self, name, *args, **kwargs):
            self.calls.append((name, args, kwargs))

        def click(self, *a, **k):
            self._add("click", *a, **k)

        def doubleClick(self, *a, **k):
            self._add("doubleClick", *a, **k)

        def tripleClick(self, *a, **k):
            self._add("tripleClick", *a, **k)

        def moveTo(self, *a, **k):
            self._add("moveTo", *a, **k)

        def write(self, *a, **k):
            self._add("write", *a, **k)

        def press(self, *a, **k):
            self._add("press", *a, **k)

    gui = FakeGUI()
    monkeypatch.setitem(sys.modules, "pyautogui", gui)
    return gui


def _patch_grounding(monkeypatch, payload, models=None):
    import api_keys

    calls = []

    def fake_generate(model, contents, config=None):
        calls.append((model, contents, config))
        return SimpleNamespace(text=json.dumps(payload))

    monkeypatch.setattr(api_keys, "generate_with_retry", fake_generate)
    return calls


def _patch_screenshot(monkeypatch, *, orig=(1920, 1080), img=(1920, 1080),
                      geometry=(0, 0, 1920, 1080), monitor_arg=False):
    calls = []

    def shot(*args):
        calls.append(args)
        return (b"jpeg", orig[0], orig[1], img[0], img[1], *geometry)

    monkeypatch.setattr(vc, "_screenshot_jpeg", shot)
    monkeypatch.setattr(vc, "_scale_factors", lambda geom: (1.0, 1.0))
    return calls


def _coords(text):
    for token in (f"({text[0]}, {text[1]})", f"({text[0]},"):
        if token in text:
            return token
    return ""


# ---------------------------------------------------------------------------
# Native grounding
# ---------------------------------------------------------------------------

def test_box_2d_center_and_model_routing(monkeypatch):
    calls = _patch_grounding(monkeypatch, {
        "found": True, "box_2d": [100, 200, 300, 400], "point": None,
        "label": "Send button", "confidence": 0.9,
    })
    _patch_screenshot(monkeypatch)
    result = vc.vision_click_action({"target": "Send button"})
    assert "(576, 216)" in result
    assert calls[0][0] == MODEL_FLASH
    assert "Send button" in calls[0][1][-1]


def test_point_inside_box_is_used(monkeypatch):
    _patch_grounding(monkeypatch, {
        "found": True, "box_2d": [200, 700, 300, 800],
        "point": [250, 750], "label": "Search box",
    })
    _patch_screenshot(monkeypatch)
    assert "(1440, 270)" in vc.vision_click_action({"target": "Search box"})


def test_point_outside_box_falls_back_to_center(monkeypatch):
    _patch_grounding(monkeypatch, {
        "found": True, "box_2d": [100, 100, 300, 300],
        "point": [900, 900], "label": "Icon",
    })
    _patch_screenshot(monkeypatch)
    assert "(384, 216)" in vc.vision_click_action({"target": "Icon"})


@pytest.mark.parametrize("payload", [
    {"found": False, "label": "NOT_FOUND"},
    {"found": False},
])
def test_not_found_never_clicks(monkeypatch, payload, _no_real_input):
    _patch_grounding(monkeypatch, payload)
    _patch_screenshot(monkeypatch)
    result = vc.vision_click_action({"target": "Missing button"})
    assert "couldn't find" in result.lower()
    assert _no_real_input.calls == []


def test_legacy_pixel_json_and_tuple_are_scaled(monkeypatch):
    _patch_grounding(monkeypatch, {"x": 640, "y": 360})
    _patch_screenshot(monkeypatch, orig=(2560, 1440), img=(1280, 720),
                      geometry=(0, 0, 2560, 1440))
    assert "(1280, 720)" in vc.vision_click_action(
        {"target": "Send", "only_coords": True}
    )

    monkeypatch.setattr(vc, "_find_coordinates", lambda img, target: (640, 360))
    result = vc.vision_click_action({"target": "Send", "only_coords": True})
    assert "(1280, 720)" in result


def test_api_failure_is_honest(monkeypatch):
    _patch_screenshot(monkeypatch)
    monkeypatch.setattr(vc, "_find_coordinates",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("Vision call failed")))
    assert "Vision click failed" in vc.vision_click_action({"target": "Send"})


# ---------------------------------------------------------------------------
# Multi-monitor + DPI mapping
# ---------------------------------------------------------------------------

def test_secondary_monitor_with_dpi_scaling(monkeypatch):
    _patch_grounding(monkeypatch, {
        "found": True, "box_2d": [400, 400, 600, 600], "label": "Icon",
    })
    shot_calls = []

    def shot(*args):
        shot_calls.append(args)
        return (b"jpeg", 2560, 1440, 1280, 720, 2560, 0, 1920, 1080)

    monkeypatch.setattr(vc, "_screenshot_jpeg", shot)

    def geometry(index=1):
        if int(index) == 1:
            return {"left": 0, "top": 0, "width": 2560, "height": 1440}
        return {"left": 2560, "top": 0, "width": 1920, "height": 1080}

    monkeypatch.setattr(vc, "_monitor_geometry", geometry)
    # 2560 physical pixels shown as 1707 logical pixels → 150% Windows DPI.
    monkeypatch.setattr(vc, "_scale_factors", lambda geom: (1707 / 2560, 960 / 1440))
    result = vc.vision_click_action({"target": "Icon", "monitor": 2, "only_coords": True})
    assert shot_calls == [(2,)]
    # x = 2560*0.667 + 0.5*1920*0.667 ≈ 2347 ; y = 0.5*1080*0.667 = 360
    assert "(2347, 360)" in result


def test_negative_monitor_left_and_virtual_desktop_clamping(monkeypatch):
    _patch_grounding(monkeypatch, {
        "found": True, "box_2d": [500, 500, 500, 500], "label": "Center",
    })

    def shot(monitor=1):
        if monitor == 0:
            return (b"jpeg", 4480, 1440, 1280, 411, 0, 0, 4480, 1440)
        return (b"jpeg", 1920, 1080, 1280, 720, -1920, 0, 1920, 1080)

    monkeypatch.setattr(vc, "_screenshot_jpeg", shot)
    monkeypatch.setattr(vc, "_scale_factors", lambda geom: (1.0, 1.0))
    assert "(-960, 540)" in vc.vision_click_action(
        {"target": "Center", "monitor": 2, "only_coords": True}
    )
    _patch_grounding(monkeypatch, {
        "found": True, "box_2d": [500, 1000, 500, 1000], "label": "Right edge",
    })
    assert "(4479, 720)" in vc.vision_click_action(
        {"target": "Right edge", "monitor": 0, "only_coords": True}
    )


def test_monitor_number_is_validated(monkeypatch):
    _patch_grounding(monkeypatch, {"found": True, "box_2d": [1, 1, 2, 2]})
    calls = _patch_screenshot(monkeypatch)
    vc.vision_click_action({"target": "X", "monitor": "bad", "only_coords": True})
    assert calls == [()]  # invalid values fall back to primary


# ---------------------------------------------------------------------------
# Interaction actions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("left", "click"), ("single", "click"), ("double", "doubleClick"),
    ("2", "doubleClick"), ("twice", "doubleClick"), ("triple", "tripleClick"),
    ("3", "tripleClick"), ("right", "click"), ("context", "click"),
    ("middle", "click"), ("hover", "moveTo"),
])
def test_click_modes(monkeypatch, _no_real_input, value, expected):
    _patch_grounding(monkeypatch, {"found": True, "box_2d": [100, 100, 200, 200]})
    _patch_screenshot(monkeypatch)
    result = vc.vision_click_action({"target": "Icon", "click": value})
    assert result
    assert any(call[0] == expected for call in _no_real_input.calls)
    if value in ("right", "context"):
        assert _no_real_input.calls[0][2]["button"] == "right"
    if value in ("middle",):
        assert _no_real_input.calls[0][2]["button"] == "middle"


def test_only_coords_does_not_move_mouse(monkeypatch, _no_real_input):
    _patch_grounding(monkeypatch, {"found": True, "box_2d": [100, 100, 200, 200]})
    _patch_screenshot(monkeypatch)
    result = vc.vision_click_action({"target": "Icon", "only_coords": "true"})
    assert "screen coordinates" in result
    assert _no_real_input.calls == []


def test_type_text_and_press_enter(monkeypatch, _no_real_input):
    _patch_grounding(monkeypatch, {"found": True, "box_2d": [100, 100, 200, 200]})
    _patch_screenshot(monkeypatch)
    result = vc.vision_click_action({
        "target": "Search box", "type_text": "hello", "press_enter": True,
    })
    assert "Typed: hello" in result and "Pressed Enter" in result
    names = [call[0] for call in _no_real_input.calls]
    assert names == ["click", "write", "press"]


# ---------------------------------------------------------------------------
# Graceful degradation and source invariants
# ---------------------------------------------------------------------------

def test_missing_inputs_and_dependencies(monkeypatch):
    assert "Tell me what to click" in vc.vision_click_action(None)
    assert "Tell me what to click" in vc.vision_click_action({"target": " "})
    monkeypatch.setattr(vc, "_MSS", False)
    assert "mss is not installed" in vc.vision_click_action({"target": "Send"})


def test_missing_pyautogui_reports_failure(monkeypatch):
    _patch_grounding(monkeypatch, {"found": True, "box_2d": [100, 100, 200, 200]})
    _patch_screenshot(monkeypatch)
    monkeypatch.setitem(sys.modules, "pyautogui", None)
    assert "click failed" in vc.vision_click_action({"target": "Icon"})


def test_source_uses_model_constants_not_retired_ids():
    source = Path(vc.__file__).read_text(encoding="utf-8")
    assert "MODEL_FLASH" in source and "generate_with_retry" in source
    assert "gemini-2.5-flash" not in source
