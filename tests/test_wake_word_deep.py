"""Fully offline combinatorial tests for actions.wake_word.

No microphone, PortAudio, speaker or real model is ever used.
"""

from __future__ import annotations

import inspect
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from actions import wake_word as ww


class _Model:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.frames = []
        self.resets = 0
        self.scores = []
        _Model.instances.append(self)

    def predict(self, frame):
        self.frames.append(np.array(frame))
        return {"hey_friday": self.scores.pop(0) if self.scores else 0.0}

    def reset(self):
        self.resets += 1


def _service(threshold=0.6, patience=2, cooldown=0.0, min_rms=0):
    service = ww.WakeWordService()
    service._model = _Model()
    service._threshold = threshold
    service._patience = patience
    service._cooldown_s = cooldown
    service._min_rms = min_rms
    return service


def _loud(n=ww.CHUNK):
    return np.full(n, 5000, dtype=np.int16)


# ---------------------------------------------------------------------------
# Exact 1280-sample frame streaming
# ---------------------------------------------------------------------------

def test_single_frame_is_predicted_exactly():
    service = _service()
    service._feed(_loud())
    assert len(service._model.frames) == 1
    assert service._model.frames[0].shape == (1280,)


def test_1280_by_1_block_is_flattened():
    service = _service()
    service._feed(_loud().reshape(-1, 1))
    assert len(service._model.frames) == 1
    assert service._model.frames[0].shape == (1280,)


def test_two_half_chunks_become_one_frame_with_no_overlap():
    service = _service()
    service._feed(_loud(640))
    assert service._model.frames == []
    service._feed(_loud(640))
    assert len(service._model.frames) == 1
    assert np.array_equal(service._model.frames[0], _loud())


def test_2560_samples_produce_exactly_two_frames():
    service = _service()
    service._feed(_loud(2560))
    assert len(service._model.frames) == 2
    assert all(frame.shape == (1280,) for frame in service._model.frames)
    assert service._residual.size == 0


def test_residual_is_carried_across_uneven_chunks():
    service = _service()
    service._feed(_loud(1000))
    service._feed(_loud(1000))
    assert len(service._model.frames) == 1
    assert service._residual.size == 720


@pytest.mark.parametrize("block", [np.empty(0, dtype=np.int16), _loud(100)])
def test_short_blocks_do_not_call_predict(block):
    service = _service()
    service._feed(block)
    assert service._model.frames == []


# ---------------------------------------------------------------------------
# Signature compatibility and VAD handling
# ---------------------------------------------------------------------------

def test_model_init_params_supports_wrapped_legacy_signature():
    def old_init(self, wakeword_model_paths, vad_threshold=0.0):
        pass

    def wrapped(*args, **kwargs):
        return old_init(*args, **kwargs)

    wrapped.__wrapped__ = old_init
    Fake = type("Fake", (), {"__init__": wrapped})
    params = ww.WakeWordService._model_init_params(Fake)
    assert "wakeword_model_paths" in params and "vad_threshold" in params


def test_vad_threshold_forwarded_when_supported_and_falls_back(monkeypatch, capsys):
    class VadModel(_Model):
        def __init__(self, wakeword_models, vad_threshold=0.0,
                     inference_framework="onnx", **kwargs):
            super().__init__(wakeword_models=wakeword_models,
                             vad_threshold=vad_threshold,
                             inference_framework=inference_framework, **kwargs)
            if vad_threshold > 0:
                raise RuntimeError("silero_vad.onnx missing")

    service = ww.WakeWordService()
    service._vad_threshold = 0.5
    result = service._new_model(VadModel, "hey_friday.onnx")
    assert result.kwargs.get("vad_threshold", 0.0) == 0.0
    assert "retrying without VAD" in capsys.readouterr().out

    class NoVad(_Model):
        def __init__(self, wakeword_models, inference_framework="onnx", **kwargs):
            assert "vad_threshold" not in kwargs
            super().__init__(wakeword_models=wakeword_models,
                             inference_framework=inference_framework, **kwargs)

    service._vad_threshold = 0.5
    assert "vad_threshold" not in service._new_model(NoVad, "hey_friday.onnx").kwargs


def test_onnx_inference_framework_used_on_supported_versions(monkeypatch, tmp_path):
    monkeypatch.setattr(ww, "MODELS_DIR", tmp_path)
    (tmp_path / ww.WakeWordService()._model_file).write_bytes(b"model")
    class OnnxModel:
        def __init__(self, wakeword_models=None, inference_framework="tflite"):
            self.kwargs = {"wakeword_models": wakeword_models,
                           "inference_framework": inference_framework}

        def predict(self, frame):
            return {"hey_friday": 0.0}

    monkeypatch.setattr(ww, "_get_oww", lambda: OnnxModel)
    service = ww.WakeWordService()
    assert service.load() is True
    assert service._model.kwargs["inference_framework"] == "onnx"
    assert "wakeword_models" in service._model.kwargs


def test_missing_custom_model_has_record_train_guidance(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ww, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(ww, "_get_oww", lambda: _Model)
    assert ww.WakeWordService().load() is False
    assert "record the wake word" in capsys.readouterr().out


def test_no_openwakeword_is_graceful(monkeypatch):
    monkeypatch.setattr(ww, "_get_oww", lambda: None)
    assert ww.WakeWordService().load() is False
    assert "openwakeword" in ww.WakeWordService().start().lower()


# ---------------------------------------------------------------------------
# False-trigger protection and callback isolation
# ---------------------------------------------------------------------------

def test_noise_gate_skips_quiet_frames():
    service = _service()
    service._min_rms = 120
    service._feed(np.full(ww.CHUNK, 5, dtype=np.int16))
    assert service._model.frames == [] and service._hits == 0


def test_patience_requires_consecutive_detections():
    service = _service(patience=3, min_rms=0)
    service._model.scores = [0.9, 0.1, 0.9, 0.9]
    fired = []
    service._on_detect = lambda: fired.append(1)
    service._feed(_loud(ww.CHUNK * 4))
    assert service._hits == 2 and fired == []


def test_cooldown_reset_and_buffer_clearing():
    fired = []
    service = _service(patience=1, cooldown=5.0, min_rms=0)
    service._on_detect = lambda: fired.append(1)
    service._model.scores = [0.9, 0.9]
    service._audio_buffer = [b"stale"]
    service._feed(_loud(ww.CHUNK))
    time.sleep(0.05)
    service._feed(_loud(ww.CHUNK))
    assert len(fired) == 1
    assert service._model.resets == 1
    assert service._audio_buffer == [] and service._residual.size == 0
    service._cooldown_s = 0
    service._model.scores = [0.9]
    service._feed(_loud(ww.CHUNK))
    assert len(fired) == 2


def test_callback_runs_off_audio_thread_with_exception_isolation(capsys):
    service = _service(patience=1)
    audio_thread = threading.current_thread()
    saw_thread = []

    def bad_callback():
        saw_thread.append(threading.current_thread() is audio_thread)
        raise RuntimeError("callback failure")

    service._on_detect = bad_callback
    service._model.scores = [0.9]
    service._feed(_loud())
    for _ in range(50):
        if saw_thread:
            break
        time.sleep(0.01)
    assert saw_thread == [False]
    assert "callback error" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Controls, actions, diagnostics and compatibility
# ---------------------------------------------------------------------------

def test_pause_drop_audio_and_resume():
    service = _service()
    assert "paused" in service.pause().lower()
    service._feed(_loud(ww.CHUNK))
    assert service._model.frames == [] and service._residual.size == 0
    assert "resumed" in service.resume().lower()
    service._feed(_loud(ww.CHUNK))
    assert len(service._model.frames) == 1


@pytest.mark.parametrize("value,expected", [("0.75", 0.75), (0.1, 0.1), (0.99, 0.99)])
def test_set_threshold_updates_value(value, expected):
    service = ww.WakeWordService()
    assert "set to" in service.set_threshold(value)
    assert service._threshold == expected


@pytest.mark.parametrize("value", ["nope", 0.05, 1.5, None])
def test_set_threshold_rejects_bad_values(value):
    service = ww.WakeWordService()
    assert "between" in service.set_threshold(value)
    assert service._threshold == ww.DETECT_THRESHOLD


@pytest.mark.parametrize("value,expected", [(3, 3.0), ("bad", 2.5), (-1, 0.0)])
def test_set_cooldown(value, expected):
    service = ww.WakeWordService()
    service.set_cooldown(value)
    assert service._cooldown_s == expected


def test_list_input_devices_uses_sounddevice_when_available(monkeypatch):
    fake_sd = SimpleNamespace(query_devices=lambda: [
        {"name": "Built-in Mic", "max_input_channels": 2},
        {"name": "Speaker", "max_input_channels": 0},
    ])
    monkeypatch.setattr(ww, "_sd", lambda: fake_sd)
    result = ww.WakeWordService().list_input_devices()
    assert "Built-in Mic" in result and "Speaker" not in result


def test_stats_reports_state_and_configuration():
    service = _service()
    service._running = True
    stats = service.stats()
    assert stats["state"] == "active"
    assert stats["threshold"] == 0.6 and stats["detections"] == 0
    service._paused = True
    assert service.stats()["state"] == "paused"


def test_start_stop_are_thread_safe_idempotent(monkeypatch):
    service = ww.WakeWordService()

    def fake_loop():
        while service._running:
            time.sleep(0.01)

    monkeypatch.setattr(ww, "_get_oww", lambda: _Model)
    monkeypatch.setattr(ww, "_sd", lambda: None)
    monkeypatch.setattr(ww, "_pyaudio", lambda: None)
    monkeypatch.setattr(service, "load", lambda: True)
    monkeypatch.setattr(service, "_loop", fake_loop)
    assert "active" in service.start().lower()
    assert "already" in service.start().lower()
    assert "stopped" in service.stop().lower()
    assert service._running is False
    assert service._thread is None


@pytest.mark.parametrize("params,expected", [
    ({"action": "pause"}, "paused"),
    ({"action": "resume"}, "resumed"),
    ({"action": "devices"}, "input devices"),
    ({"action": "diagnostics"}, '"phrase"'),
    ({"action": "test"}, '"threshold"'),
    ({"action": "status"}, "Wake word is"),
    ({"action": "set_cooldown", "cooldown": 4}, "4.0 seconds"),
    ({"action": "nonsense"}, "Unknown wake word action"),
])
def test_wake_word_actions(monkeypatch, params, expected):
    monkeypatch.setattr(ww, "_pick_engine", lambda: ("friday", ww._service))
    monkeypatch.setattr(ww, "_load_matcher", lambda: None)
    monkeypatch.setattr(ww._service, "list_input_devices", lambda: "Available input devices:")
    assert expected.lower() in ww.wake_word(params).lower()


def test_set_threshold_action_and_parameter_shapes(monkeypatch):
    monkeypatch.setattr(ww, "_pick_engine", lambda: ("friday", ww._service))
    monkeypatch.setattr(ww, "_load_matcher", lambda: None)
    assert "0.70" in ww.wake_word({"action": "sensitivity", "threshold": 0.7})
    assert "0.80" in ww.wake_word({"action": "set_threshold", "value": "0.8"})
    assert "wake word is" in ww.wake_word("bad parameters").lower()


def test_load_matcher_ast_contract():
    """test_windows_bringup.py enforces this contract independently."""
    import ast

    source = open(ww.__file__, encoding="utf-8").read()
    tree = ast.parse(source)
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and
                n.name == "_load_matcher")
    names = {node.attr for node in ast.walk(func)
             if isinstance(node, ast.Attribute)}
    assert names == {"import_module"}
    assert not any(isinstance(node, ast.ImportFrom) and
                   getattr(node, "module", "") == "actions.friday_wake"
                   for node in ast.walk(tree))


def test_pick_engine_prefers_available_matcher(monkeypatch):
    matcher = SimpleNamespace(available=True)
    monkeypatch.setattr(ww, "_load_matcher", lambda: matcher)
    name, engine = ww._pick_engine()
    assert name == "friday" and engine is matcher

    monkeypatch.setattr(ww, "_load_matcher", lambda: None)
    assert ww._pick_engine()[1] is ww._service


def test_start_propagates_callback_to_custom_engine(monkeypatch):
    class Engine:
        available = True
        _running = False
        _on_detect = None

        def configure(self, on_detect=None):
            self._on_detect = on_detect

        def start(self):
            return "started"

        def stop(self):
            return "stopped"

    engine = Engine()
    ww._service._on_detect = lambda: None
    monkeypatch.setattr(ww, "_load_matcher", lambda: engine)
    monkeypatch.setattr(ww, "_pick_engine", lambda: ("friday", engine))
    assert ww.wake_word({"action": "start"}) == "started"
    assert engine._on_detect is ww._service._on_detect
