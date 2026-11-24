# actions/wake_word.py
# Friday — hands-free "Hey Friday" wake word activation.
#
# Local openwakeword engine with true 1280-sample streaming, noise gating,
# patience, cooldown and off-audio-thread callbacks. Every optional dependency
# (openwakeword, numpy, sounddevice, pyaudio) is imported lazily/guarded.

from __future__ import annotations

import importlib
import inspect
import json
import threading
import time
from pathlib import Path

try:
    import numpy as np
except ImportError:                                    # pragma: no cover - environment
    np = None

_OWW = None  # None=not checked yet, False=unavailable, Model class=available


def _get_oww():
    """Lazily import openwakeword; returns the Model class or None."""
    global _OWW
    if _OWW is None:
        try:
            from openwakeword.model import Model
            _OWW = Model
        except Exception:
            _OWW = False
    return _OWW or None


def _sd():
    """Return sounddevice only when its PortAudio backend is really available."""
    try:
        import sounddevice as sd
        return sd
    except Exception:
        return None


def _pyaudio():
    try:
        import pyaudio
        return pyaudio
    except Exception:
        return None


BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"

RATE = 16000
CHANNELS = 1
CHUNK = 1280          # 80ms @ 16kHz — one openwakeword prediction frame
DTYPE = "int16"
SAMPLE_NUMS = 15      # legacy constant retained for compatibility
DETECT_THRESHOLD = 0.6
DEFAULT_COOLDOWN_S = 2.5
DEFAULT_PATIENCE = 2
DEFAULT_MIN_RMS = 120


class WakeWordService:
    """Streaming local wake-word detector with false-trigger protection."""

    def __init__(self, model_file: str = "hey_friday_v0.1.onnx",
                 phrase: str = "Hey Friday"):
        self._running = False
        self._thread = None
        self._on_detect = None
        self._model = None
        self._model_path = None
        self._model_file = model_file
        self._phrase = phrase
        self._lock = threading.RLock()
        self._audio_buffer = []          # legacy attribute kept for compatibility
        self._residual = self._empty_samples()
        self._threshold = DETECT_THRESHOLD
        self._vad_threshold = 0.0
        self._cooldown_s = DEFAULT_COOLDOWN_S
        self._patience = DEFAULT_PATIENCE
        self._min_rms = DEFAULT_MIN_RMS
        self._hits = 0
        self._detections = 0
        self._last_score = 0.0
        self._last_trigger_ts = 0.0
        self._last_error = ""
        self._paused = False
        self._device = None
        self._backend = ""

    @staticmethod
    def _empty_samples():
        return np.empty(0, dtype=np.int16) if np is not None else None

    @property
    def available(self) -> bool:
        return _get_oww() is not None and self._model is not None

    # -- model loading -------------------------------------------------------

    @staticmethod
    def _model_init_params(Model) -> set[str]:
        """Inspect openwakeword's real __init__ across wrapped versions."""
        init = getattr(Model, "__init__", None)
        candidates = [getattr(init, "__wrapped__", None), init]
        for cell in getattr(init, "__closure__", None) or ():
            value = cell.cell_contents
            if callable(value):
                candidates.append(value)
        best: set[str] = set()
        for candidate in candidates:
            if not callable(candidate):
                continue
            try:
                names = set(inspect.signature(candidate).parameters)
            except (TypeError, ValueError):
                continue
            if "wakeword_models" in names or "wakeword_model_paths" in names:
                return names
            best = best or names
        return best

    def _new_model(self, Model, candidate: str):
        """Build a Model with version-safe kwargs; ONNX on Windows by default."""
        params = self._model_init_params(Model)
        kwargs: dict = {}
        if "wakeword_models" in params:
            kwargs["wakeword_models"] = [candidate]
        elif "wakeword_model_paths" in params or not params:
            kwargs["wakeword_model_paths"] = [candidate]
        if "inference_framework" in params:
            kwargs["inference_framework"] = "onnx"
        if self._vad_threshold > 0 and "vad_threshold" in params:
            kwargs["vad_threshold"] = self._vad_threshold

        try:
            return Model(**kwargs)
        except Exception as exc:
            message = str(exc).lower()
            if "vad_threshold" in kwargs and any(
                hint in message for hint in ("vad", "silero", "onnx", "model")
            ):
                print(f"[WakeWord] VAD init failed ({exc}); retrying without VAD")
                kwargs.pop("vad_threshold", None)
                self._last_error = "VAD unavailable; loaded without vad_threshold"
                return Model(**kwargs)
            raise

    def load(self) -> bool:
        """Load the model without opening a microphone stream."""
        if self._model is not None:
            return True
        Model = _get_oww()
        if Model is None:
            self._last_error = "openwakeword not installed"
            return False
        candidate = str(MODELS_DIR / self._model_file)
        if not Path(candidate).exists():
            self._last_error = f"model not found: {candidate}"
            print(
                "[WakeWord] Model missing. Record/train it with: "
                "'record the wake word', then 'train the wake word'."
            )
            return False
        try:
            self._model = self._new_model(Model, candidate)
            self._model_path = candidate
            self._last_error = ""
            return True
        except Exception as exc:
            self._last_error = str(exc)
            print(f"[WakeWord] Load failed: {exc}")
            return False

    def configure(self, on_detect=None):
        self._on_detect = on_detect

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> str:
        Model = _get_oww()
        if Model is None:
            return "openwakeword is not installed. Run: pip install openwakeword"
        with self._lock:
            if self._running:
                return "Wake word is already listening, sir."
        if not self.load():
            return (
                "Wake word model not found in models/. Record and train it first "
                "by saying 'record the wake word', then 'train the wake word', sir."
            )
        with self._lock:
            if self._running:
                return "Wake word is already listening, sir."
            self._running = True
            self._paused = False
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="WakeWordThread"
            )
            self._thread.start()
        return (
            f"Wake word active ({self._phrase}). Say '{self._phrase}' to wake me."
        )

    def stop(self) -> str:
        with self._lock:
            self._running = False
            thread = self._thread
            self._thread = None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
        return "Wake word stopped, sir."

    def pause(self) -> str:
        with self._lock:
            self._paused = True
            self._hits = 0
        if self._residual is not None:
            self._residual = self._empty_samples()
        return "Wake word listening paused, sir."

    def resume(self) -> str:
        with self._lock:
            self._paused = False
            self._hits = 0
        return "Wake word listening resumed, sir."

    def set_threshold(self, value) -> str:
        try:
            threshold = float(value)
        except (TypeError, ValueError):
            return "Please give a threshold between 0.1 and 0.99, sir."
        if not 0.1 <= threshold <= 0.99:
            return "Please give a threshold between 0.1 and 0.99, sir."
        with self._lock:
            self._threshold = threshold
        return f"Wake word threshold set to {threshold:.2f}, sir."

    def set_cooldown(self, value) -> str:
        try:
            seconds = max(0.0, min(30.0, float(value)))
        except (TypeError, ValueError):
            return "Please give a cooldown in seconds, sir."
        with self._lock:
            self._cooldown_s = seconds
        return f"Wake word cooldown set to {seconds:.1f} seconds, sir."

    # -- audio backends ------------------------------------------------------

    def _loop(self):
        sd = _sd()
        if sd is not None:
            self._backend = "sounddevice"
            self._loop_sounddevice(sd)
            return
        wave_module = _pyaudio()
        if wave_module is not None:
            self._backend = "pyaudio"
            self._loop_pyaudio(wave_module)
            return
        self._last_error = "no audio backend available"
        print(f"[WakeWord] {self._last_error}")

    def _loop_sounddevice(self, sd):
        try:
            kwargs = {
                "samplerate": RATE, "channels": CHANNELS, "dtype": DTYPE,
                "blocksize": CHUNK, "callback": self._audio_callback,
            }
            if self._device is not None:
                kwargs["device"] = self._device
            with sd.InputStream(**kwargs):
                while self._running:
                    time.sleep(0.1)
        except Exception as exc:
            self._last_error = str(exc)
            print(f"[WakeWord] {exc}")

    def _loop_pyaudio(self, pyaudio):
        try:
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16, channels=CHANNELS, rate=RATE,
                input=True, frames_per_buffer=CHUNK, stream_callback=self._pa_callback,
            )
            stream.start_stream()
            while self._running:
                time.sleep(0.1)
            stream.stop_stream()
            stream.close()
            pa.terminate()
        except Exception as exc:
            self._last_error = str(exc)
            print(f"[WakeWord] {exc}")

    def _audio_callback(self, indata, frames, time_info, status):
        if self._running and not self._paused:
            self._feed(indata)

    def _pa_callback(self, in_data, frame_count, time_info, status):
        if self._running and not self._paused and np is not None:
            self._feed(np.frombuffer(in_data, dtype=np.int16))
        pyaudio = _pyaudio()
        return (None, pyaudio.paContinue if pyaudio else 0)

    # -- streaming detection -------------------------------------------------

    def _feed(self, block: np.ndarray):
        """Feed exact non-overlapping 1280-sample frames into Model.predict()."""
        if self._model is None or self._paused or np is None:
            return
        with self._lock:
            samples = np.asarray(block, dtype=np.int16).reshape(-1)
            combined = np.concatenate((self._residual, samples)) \
                if self._residual is not None and self._residual.size else samples
            usable = (len(combined) // CHUNK) * CHUNK
            frames = combined[:usable].reshape(-1, CHUNK) if usable else \
                np.empty((0, CHUNK), dtype=np.int16)
            self._residual = combined[usable:].copy()

        stem = Path(self._model_file).stem
        keys = {stem, "hey_friday"} if stem.startswith("hey_friday") else {stem}

        for frame in frames:
            rms = float(np.sqrt(np.mean(np.square(
                frame.astype(np.float64)
            ))))
            if rms < self._min_rms:
                with self._lock:
                    self._hits = 0
                continue

            try:
                prediction = self._model.predict(frame)
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)
                continue
            score = 0.0
            if isinstance(prediction, dict):
                score = max((float(prediction.get(key) or 0) for key in keys), default=0.0)
            with self._lock:
                self._last_score = score
                if score >= self._threshold:
                    self._hits += 1
                    ready = self._hits >= self._patience and \
                        (time.monotonic() - self._last_trigger_ts) >= self._cooldown_s
                else:
                    self._hits = 0
                    ready = False
            if ready:
                self._trigger()

    def _trigger(self):
        """Reset model/buffers and dispatch the callback off the audio thread."""
        try:
            if hasattr(self._model, "reset"):
                self._model.reset()
        except Exception as exc:
            self._last_error = str(exc)
        with self._lock:
            self._audio_buffer.clear()
            self._residual = self._empty_samples()
            self._hits = 0
            self._last_trigger_ts = time.monotonic()
            self._detections += 1
            callback = self._on_detect
        print(f"[WakeWord] {self._phrase} detected ({self._last_score:.2f})")
        if callback is not None:
            threading.Thread(
                target=self._safe_callback, args=(callback,), daemon=True,
                name="WakeWordCallback",
            ).start()

    @staticmethod
    def _safe_callback(callback):
        try:
            callback()
        except Exception as exc:
            print(f"[WakeWord] callback error: {exc}")

    # -- diagnostics ---------------------------------------------------------

    def list_input_devices(self) -> str:
        sd = _sd()
        if sd is not None:
            try:
                devices = sd.query_devices()
                lines = ["Available input devices:"]
                for index, device in enumerate(devices):
                    if isinstance(device, dict) and device.get("max_input_channels", 0) > 0:
                        lines.append(f"{index}: {device.get('name', 'unknown')}")
                return "\n".join(lines)
            except Exception as exc:
                return f"Could not list input devices: {exc}"
        pyaudio = _pyaudio()
        if pyaudio is not None:
            try:
                pa = pyaudio.PyAudio()
                lines = ["Available input devices:"]
                for index in range(pa.get_device_count()):
                    info = pa.get_device_info_by_index(index)
                    if info.get("maxInputChannels", 0) > 0:
                        lines.append(f"{index}: {info.get('name', 'unknown')}")
                pa.terminate()
                return "\n".join(lines)
            except Exception as exc:
                return f"Could not list input devices: {exc}"
        return "No audio backend is available on this machine, sir."

    def stats(self) -> dict:
        with self._lock:
            state = "active" if self._running else "inactive"
            return {
                "state": "paused" if self._paused and self._running else state,
                "phrase": self._phrase,
                "model_path": self._model_path,
                "threshold": self._threshold,
                "patience": self._patience,
                "cooldown": self._cooldown_s,
                "detections": self._detections,
                "last_score": round(self._last_score, 4),
                "last_error": self._last_error,
                "backend": self._backend or ("sounddevice" if _sd() else
                                             "pyaudio" if _pyaudio() else "none"),
            }


_service = WakeWordService()


def _load_matcher():
    """Return the custom "Hey Friday" matcher, or ``None``.

    Import stays inside this function so the tray thread never dies when
    onnxruntime or the trained model is absent.
    """
    for module_name in ("actions.friday_wake",):
        try:
            module = importlib.import_module(module_name)
            matcher = getattr(module, "_matcher", None)
        except Exception:
            continue
        if matcher is not None:
            return matcher
    return None


def _pick_engine():
    """Return the wake-word engine that should be active right now."""
    matcher = _load_matcher()
    try:
        if matcher is not None and matcher.available:
            return "friday", matcher
    except Exception:
        pass
    return "friday", _service


def wake_word(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Control the wake word listener (Hey Friday)."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()

    matcher = _load_matcher()

    if action in ("record", "record_wake_word"):
        if matcher is None:
            return ("Custom wake-word recording is unavailable: "
                    "actions.friday_wake could not be imported.")
        try:
            clips = int(params.get("clips") or params.get("count") or 5)
        except (TypeError, ValueError):
            clips = 5
        return matcher.record(n_clips=clips)

    if action in ("train", "train_wake_word", "train_model"):
        if matcher is None:
            return "Custom wake-word training is unavailable: actions.friday_wake could not be imported."
        try:
            steps = int(params.get("steps") or params.get("epochs") or 900)
        except (TypeError, ValueError):
            steps = 900
        return matcher.train(steps=steps)

    engine_name, engine = _pick_engine()

    def target():
        return engine if hasattr(engine, "set_threshold") else _service

    if action == "start":
        if params.get("device") is not None:
            try:
                _service._device = int(params["device"])
            except (TypeError, ValueError):
                pass
        if matcher is not None and engine is matcher \
                and not engine._on_detect and _service._on_detect:
            engine.configure(on_detect=_service._on_detect)
        return engine.start()
    if action == "stop":
        return engine.stop()
    if action in ("status", "info"):
        state = "active" if engine._running else "inactive"
        detail = target().stats()
        return (
            f"Wake word is {state} ({detail['phrase']}). "
            f"Threshold {detail['threshold']:.2f}, cooldown {detail['cooldown']:.1f}s, "
            f"backend {detail['backend']}."
        )
    if action == "pause":
        return target().pause()
    if action == "resume":
        return target().resume()
    if action in ("set_threshold", "sensitivity"):
        value = params.get("threshold", params.get("value"))
        return target().set_threshold(value)
    if action == "set_cooldown":
        return target().set_cooldown(params.get("cooldown", params.get("value")))
    if action in ("devices", "list_devices"):
        return target().list_input_devices()
    if action in ("diagnostics", "test"):
        return json.dumps(target().stats(), ensure_ascii=False, indent=2)

    return (
        f"Unknown wake word action: {action}, sir. Use start | stop | status | "
        "pause | resume | set_threshold | devices | diagnostics | record | train"
    )
