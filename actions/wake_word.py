# actions/wake_word.py
# Friday — hands-free "Hey Friday" wake word activation.
#
# Local openwakeword engine with true 1280-sample streaming, noise gating,
# patience, cooldown and off-audio-thread callbacks. Every optional dependency
# (openwakeword, numpy, sounddevice, pyaudio) is imported lazily/guarded.

from __future__ import annotations

import argparse
import contextlib
import functools
import importlib
import inspect
import json
import sys
import threading
import time
from pathlib import Path

try:
    import numpy as np
except ImportError:                                    # pragma: no cover - environment
    np = None

_OWW = None  # None=not checked yet, False=unavailable, Model class=available
# Model paths whose "missing" hint was already printed. Module level on purpose:
# startup probes more than one engine instance, and the warning must appear at
# most once per path per process.
_WARNED_MISSING: set[str] = set()


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
MELSPEC_MODEL_FILE = "melspectrogram.onnx"
EMBEDDING_MODEL_FILE = "embedding_model.onnx"
CONFIG_FILE_NAME = "wake_word.json"

RATE = 16000
CHANNELS = 1
CHUNK = 1280          # 80ms @ 16kHz — one openwakeword prediction frame
DTYPE = "int16"
SAMPLE_NUMS = 15      # legacy constant retained for compatibility
DETECT_THRESHOLD = 0.6
DEFAULT_COOLDOWN_S = 2.5
DEFAULT_PATIENCE = 2
DEFAULT_MIN_RMS = 120

_OWW_INIT_LOCK = threading.Lock()


def resolve_feature_models(
    models_dir: Path | str | None = None,
    *,
    must_exist: bool = False,
) -> tuple[Path, Path]:
    """Return local (melspectrogram.onnx, embedding_model.onnx) paths.

    openwakeword's Python 3.11+/3.13 wheels omit bundled ONNX resources inside
    site-packages, while FRIDAY ships both models in ``models/``. Resolving
    against ``models_dir`` (default: ``MODELS_DIR``) guarantees both training
    and runtime inference use the exact same local ONNX feature front-end.
    """
    base = Path(models_dir) if models_dir is not None else MODELS_DIR
    mel_path = base / MELSPEC_MODEL_FILE
    emb_path = base / EMBEDDING_MODEL_FILE
    if must_exist:
        missing = [str(p) for p in (mel_path, emb_path) if not p.is_file()]
        if missing:
            raise FileNotFoundError(
                "Missing openWakeWord feature model(s) in "
                f"{base}: {', '.join(missing)}. Ensure "
                f"{MELSPEC_MODEL_FILE} and {EMBEDDING_MODEL_FILE} are present."
            )
    return mel_path, emb_path


@contextlib.contextmanager
def _oww_feature_models_context(mel_path: Path, emb_path: Path):
    """Ensure openwakeword.model.Model wires local ONNX feature models."""
    oww_model_mod = sys.modules.get("openwakeword.model")
    orig_af = getattr(oww_model_mod, "AudioFeatures", None) if oww_model_mod else None
    if oww_model_mod is None or not callable(orig_af):
        yield
        return

    try:
        af_sig = inspect.signature(orig_af)
        af_params = set(af_sig.parameters)
        af_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in af_sig.parameters.values()
        )
    except (TypeError, ValueError):
        af_params = set()
        af_var_kw = True

    @functools.wraps(orig_af)
    def _patched_af(*args, **kwargs):
        if af_var_kw or "melspec_model_path" in af_params:
            kwargs.setdefault("melspec_model_path", str(mel_path))
        if af_var_kw or "embedding_model_path" in af_params:
            kwargs.setdefault("embedding_model_path", str(emb_path))
        if af_var_kw or "inference_framework" in af_params:
            kwargs.setdefault("inference_framework", "onnx")
        return orig_af(*args, **kwargs)

    with _OWW_INIT_LOCK:
        setattr(oww_model_mod, "AudioFeatures", _patched_af)
        try:
            yield
        finally:
            setattr(oww_model_mod, "AudioFeatures", orig_af)


def _prime_oww_model(model) -> None:
    """Replace openwakeword's random reset() buffer with steady-state silence.

    openwakeword's ``AudioFeatures.reset()`` seeds ``melspectrogram_buffer``
    with ones and ``feature_buffer`` with 41 random embeddings (plus zeroes out
    frames 0..4 when ``len(prediction_buffer) < 5``). Priming with exact
    16-frame silence state matches steady-state live streaming from frame 0.
    """
    if np is None or model is None:
        return
    pre = getattr(model, "preprocessor", None)
    if pre is not None and all(
        hasattr(pre, attr)
        for attr in (
            "_get_melspectrogram",
            "embedding_model_predict",
            "melspectrogram_buffer",
            "feature_buffer",
            "raw_data_buffer",
        )
    ):
        try:
            cached = getattr(pre, "_friday_silence_state", None)
            if cached is None:
                zeros_pcm = np.zeros(160 * (76 + 3), dtype=np.int16)
                mel_zero = np.asarray(
                    pre._get_melspectrogram(zeros_pcm), dtype=np.float32
                )
                if mel_zero.shape[0] > 76:
                    mel_zero = mel_zero[-76:]
                emb_zero = np.asarray(
                    pre.embedding_model_predict(mel_zero[None, :, :, None]),
                    dtype=np.float32,
                ).reshape(1, -1)
                cached = (mel_zero, emb_zero)
                setattr(pre, "_friday_silence_state", cached)
            mel_zero, emb_zero = cached
            if hasattr(pre.raw_data_buffer, "clear"):
                pre.raw_data_buffer.clear()
                pre.raw_data_buffer.extend([0] * 480)
            if hasattr(pre, "accumulated_samples"):
                pre.accumulated_samples = 0
            if hasattr(pre, "raw_data_remainder"):
                pre.raw_data_remainder = np.empty(0)
            pre.melspectrogram_buffer = mel_zero.copy()
            pre.feature_buffer = np.repeat(emb_zero, 16, axis=0).copy()
        except Exception:
            pass

    pred_buf = getattr(model, "prediction_buffer", None)
    models_map = getattr(model, "models", None)
    if isinstance(models_map, dict) and pred_buf is not None:
        try:
            for name in models_map:
                buf = pred_buf[name]
                while len(buf) < 5:
                    buf.append(0.0)
        except Exception:
            pass


class WakeWordService:
    """Streaming local wake-word detector with false-trigger protection."""

    def __init__(
        self,
        model_file: str = "hey_friday_v0.1.onnx",
        phrase: str = "Hey Friday",
        models_dir: Path | str | None = None,
    ):
        self._running = False
        self._thread = None
        self._on_detect = None
        self._model = None
        self._model_path = None
        self._model_file = model_file
        self._models_dir = Path(models_dir) if models_dir is not None else None
        self._phrase = phrase
        self._phrase_explicit = phrase != "Hey Friday"
        self._lock = threading.RLock()
        self._audio_buffer = []          # legacy attribute kept for compatibility
        self._residual = self._empty_samples()
        self._threshold = DETECT_THRESHOLD
        self._threshold_explicit = False
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
        self._sync_persisted_config()

    def _resolve_models_dir(self) -> Path:
        return self._models_dir if self._models_dir is not None else MODELS_DIR

    def _sync_persisted_config(self) -> None:
        """Adopt calibrated threshold and custom phrase from wake_word.json."""
        cfg_path = self._resolve_models_dir() / CONFIG_FILE_NAME
        if not cfg_path.is_file():
            return
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        with self._lock:
            if not self._threshold_explicit and "threshold" in data:
                try:
                    thr = float(data["threshold"])
                    if 0.1 <= thr <= 0.99:
                        self._threshold = thr
                except (TypeError, ValueError):
                    pass
            if not self._phrase_explicit:
                raw_phrase = " ".join(str(data.get("phrase") or "").split())
                if raw_phrase:
                    self._phrase = raw_phrase

    @staticmethod
    def _empty_samples():
        return np.empty(0, dtype=np.int16) if np is not None else None

    @property
    def available(self) -> bool:
        return _get_oww() is not None and self._model is not None

    # -- model loading -------------------------------------------------------

    @staticmethod
    def _model_init_signature(Model) -> tuple[set[str], bool]:
        """Inspect openwakeword's real __init__ across wrapped versions."""
        init = getattr(Model, "__init__", None)
        candidates = [getattr(init, "__wrapped__", None), init]
        for cell in getattr(init, "__closure__", None) or ():
            value = cell.cell_contents
            if callable(value):
                candidates.append(value)
        best: set[str] = set()
        best_var_kw = False
        for candidate in candidates:
            if not callable(candidate):
                continue
            try:
                sig = inspect.signature(candidate)
            except (TypeError, ValueError):
                continue
            names = set(sig.parameters)
            has_var_kw = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in sig.parameters.values()
            )
            if "wakeword_models" in names or "wakeword_model_paths" in names:
                return names, has_var_kw
            if not best and (names - {"self", "args", "kwargs"} or has_var_kw):
                best = names
                best_var_kw = has_var_kw
        return best, best_var_kw

    @classmethod
    def _model_init_params(cls, Model) -> set[str]:
        """Inspect openwakeword's real __init__ parameter names."""
        names, _ = cls._model_init_signature(Model)
        return names

    def _new_model(self, Model, candidate: str):
        """Build a Model with version-safe kwargs and local ONNX front-end."""
        params, has_var_kw = self._model_init_signature(Model)
        kwargs: dict = {}
        if "wakeword_models" in params:
            kwargs["wakeword_models"] = [candidate]
        elif "wakeword_model_paths" in params or not params:
            kwargs["wakeword_model_paths"] = [candidate]
        if "inference_framework" in params:
            kwargs["inference_framework"] = "onnx"
        if self._vad_threshold > 0 and "vad_threshold" in params:
            kwargs["vad_threshold"] = self._vad_threshold

        models_dir = self._resolve_models_dir()
        mel_path, emb_path = resolve_feature_models(models_dir, must_exist=False)
        have_local_features = mel_path.is_file() and emb_path.is_file()
        if have_local_features:
            if "melspec_model_path" in params or has_var_kw:
                kwargs["melspec_model_path"] = str(mel_path)
            if "embedding_model_path" in params or has_var_kw:
                kwargs["embedding_model_path"] = str(emb_path)

        ctx = (
            _oww_feature_models_context(mel_path, emb_path)
            if have_local_features
            else contextlib.nullcontext()
        )
        with ctx:
            try:
                model = Model(**kwargs)
            except Exception as exc:
                message = str(exc).lower()
                if "vad_threshold" in kwargs and any(
                    hint in message for hint in ("vad", "silero", "onnx", "model")
                ):
                    print(f"[WakeWord] VAD init failed ({exc}); retrying without VAD")
                    kwargs.pop("vad_threshold", None)
                    self._last_error = "VAD unavailable; loaded without vad_threshold"
                    model = Model(**kwargs)
                elif not have_local_features and any(
                    hint in message
                    for hint in ("melspectrogram", "embedding_model", "no_suchfile")
                ):
                    resolve_feature_models(models_dir, must_exist=True)
                    raise
                else:
                    raise

        _prime_oww_model(model)
        return model

    def load(self) -> bool:
        """Load the model without opening a microphone stream.

        The "Model missing" hint is printed at most once per model path so the
        startup path (service bootstrap + first engine probe) can no longer
        print the same warning twice back to back.
        """
        self._sync_persisted_config()
        if self._model is not None:
            return True
        Model = _get_oww()
        if Model is None:
            self._last_error = "openwakeword not installed"
            return False
        candidate = str(self._resolve_models_dir() / self._model_file)
        if not Path(candidate).exists():
            self._last_error = f"model not found: {candidate}"
            if candidate not in _WARNED_MISSING:
                _WARNED_MISSING.add(candidate)
                print(
                    "[WakeWord] Model missing. Say 'open wake word setup' (or "
                    "'set up the wake word') to record, train and activate it in "
                    "one step, sir."
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
                "Wake word model not found in models/. Say 'open wake word setup' "
                "(or 'set up the wake word') to record, train and activate it in "
                "one step, sir."
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
            self._threshold_explicit = True
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

    def _prediction_keys(self) -> set[str]:
        """Model output keys to read, tolerant of renamed exports."""
        stem = Path(self._model_file).stem
        keys = {stem, "hey_friday"} if stem.startswith("hey_friday") else {stem}
        keys.add("hey_friday")
        return keys

    def _score(self, frame) -> float:
        """Single-frame wake-word probability (0.0 on any backend error)."""
        if self._model is None or np is None:
            return 0.0
        try:
            prediction = self._model.predict(frame)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            return 0.0
        if not isinstance(prediction, dict):
            return 0.0
        return max((float(prediction.get(key) or 0) for key in self._prediction_keys()),
                   default=0.0)

    def score_frame(self, frame) -> float:
        """Score one 1280-sample frame for the interactive live test."""
        if np is None:
            return 0.0
        samples = np.asarray(frame, dtype=np.int16).reshape(-1)
        if samples.size < CHUNK:
            samples = np.pad(samples, (0, CHUNK - samples.size))
        score = self._score(samples[:CHUNK])
        with self._lock:
            self._last_score = score
        return score

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

        for frame in frames:
            rms = float(np.sqrt(np.mean(np.square(
                frame.astype(np.float64)
            ))))
            if rms < self._min_rms:
                pre = getattr(self._model, "preprocessor", None)
                if callable(pre):
                    try:
                        pre(frame)
                    except Exception:
                        pass
                with self._lock:
                    self._hits = 0
                continue

            score = self._score(frame)
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
            _prime_oww_model(self._model)
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
        phrase = _helper_phrase(self._phrase)      # disk read: keep off the lock
        with self._lock:
            state = "active" if self._running else "inactive"
            return {
                "state": "paused" if self._paused and self._running else state,
                "phrase": phrase,
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

    # -- diagnostics helpers -------------------------------------------------

    def describe(self) -> dict:
        """Compact status dict used by the CLI and the voice status action."""
        detail = self.stats()
        detail["model_file"] = self._model_file
        detail["model_present"] = (MODELS_DIR / self._model_file).exists()
        return detail


def _set_phrase(phrase) -> str:
    """Persist a custom wake phrase so recording, training and the UI agree."""
    text = " ".join(str(phrase or "").split())
    if not text:
        return ""
    try:
        text = _helper().normalise_phrase(text)
        _helper().save_config(phrase=text)
    except Exception:
        pass
    _service._phrase = text
    return text


_service = WakeWordService()


BUSY_RECORDING_MESSAGE = "Wake-word recording is already in progress, sir."

# Natural-language action words the model may emit -> canonical action.
_ACTION_ALIASES = {
    "guided_setup": "setup",
    "setup_wake_word": "setup",
    "create": "setup",
    "create_wake_word": "setup",
    "record_and_train": "setup",
    "calibrate": "test",
    "live_test": "test",
    "test_wake_word": "test",
    "clips": "list_clips",
    "list": "list_clips",
    "reset_wake_word": "reset",
    "clear_all": "reset",
    "help": "status",
}


def _helper():
    """Return the guided-workflow module (runtime import keeps tray alive)."""
    return importlib.import_module("actions.friday_wake")


def is_recording_active() -> bool:
    """True while the wake-word recorder or live test owns the microphone.

    ``main.py`` consults this before forwarding mic audio to Gemini Live, so a
    guided setup (five "Hey Friday" takes) can never leak into the live session,
    trigger a second ``record`` call, or get talked over mid-prompt.
    """
    try:
        return bool(_helper().is_recording_active())
    except Exception:
        return False


def recording_in_progress() -> tuple[bool, str]:
    """``(busy, activity)`` for status output and the setup guard."""
    try:
        return _helper().activity_in_progress()
    except Exception:
        return False, ""


def _helper_phrase(default: str = "Hey Friday") -> str:
    try:
        return _helper().current_phrase(default)
    except Exception:
        return default


def _threshold_value(params: dict, fallback: float) -> float:
    """Parse a threshold from tool params, clamped to the supported range."""
    raw = params.get("threshold", params.get("value"))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    if not 0.1 <= value <= 0.99:
        return fallback
    return value


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


def _int_param(params: dict, keys, default: int, low: int, high: int) -> int:
    """Read the first present integer param, clamped to [low, high]."""
    for key in keys:
        if params.get(key) is None:
            continue
        try:
            return max(low, min(int(params[key]), high))
        except (TypeError, ValueError):
            continue
    return default


def _float_param(params: dict, keys, default: float, low: float, high: float) -> float:
    """Read the first present float param, clamped to [low, high]."""
    for key in keys:
        if params.get(key) is None:
            continue
        try:
            return max(low, min(float(params[key]), high))
        except (TypeError, ValueError):
            continue
    return default


def _bool_param(params: dict, keys, default: bool = False) -> bool:
    for key in keys:
        if key in params and params[key] is not None:
            value = params[key]
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
    return default


def _phrase_param(params: dict) -> str:
    phrase = params.get("phrase", params.get("word", params.get("name")))
    return str(phrase or "").strip()


def wake_word(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Control the wake word listener (Hey Friday)."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()
    action = _ACTION_ALIASES.get(action, action)

    matcher = _load_matcher()
    try:
        helper = _helper()
    except Exception:
        helper = None

    phrase = _phrase_param(params)

    if action == "setup":
        if helper is None:
            return ("Guided wake-word setup needs actions/friday_wake.py, "
                    "which could not be imported, sir.")
        if phrase:
            _set_phrase(phrase)
        return helper.run_setup(
            phrase=phrase or _helper_phrase(),
            positives=_int_param(params, ("positives", "samples", "clips", "count"),
                                 5, 1, 30),
            negatives=_int_param(params, ("negatives", "backgrounds"),
                                 3, 0, 15),
            seconds=_float_param(params, ("seconds", "duration"),
                                 helper.RECORD_SECONDS, 0.5, 5.0),
            steps=_int_param(params, ("steps", "epochs"), 900, 50, 5000),
            player=player,
            engine=matcher,
            activate=True,
        )

    if action in ("record", "record_wake_word"):
        if matcher is None:
            return ("Custom wake-word recording is unavailable: "
                    "actions.friday_wake could not be imported.")
        clips = _int_param(params, ("clips", "positives", "samples", "count"),
                           5, 1, 30)
        negatives = _int_param(params, ("negatives", "backgrounds"), 3, 0, 15)
        if phrase:
            _set_phrase(phrase)
        if _bool_param(params, ("auto_train", "train", "then_train")):
            return matcher.setup(phrase=phrase or _helper_phrase(), positives=clips,
                                 negatives=negatives, player=player, activate=True)
        busy, _activity = recording_in_progress()
        if busy:
            return BUSY_RECORDING_MESSAGE
        result = matcher.record(n_clips=clips, negatives=negatives,
                                phrase=phrase or None, player=player)
        return result if isinstance(result, str) else str(result.get("message") or result)

    if action in ("train", "train_wake_word", "train_model"):
        if matcher is None:
            return ("Custom wake-word training is unavailable: "
                    "actions.friday_wake could not be imported.")
        steps = _int_param(params, ("steps", "epochs"), 900, 50, 5000)
        return matcher.train(steps=steps)

    if action == "test":
        if helper is None:
            return "Live wake-word testing needs actions/friday_wake.py, sir."
        return helper.live_test(
            seconds=_float_param(params, ("seconds", "duration"), 8.0, 1.0, 15.0),
            threshold=_threshold_value(params, None),
            phrase=phrase or _helper_phrase(),
            player=player,
        )

    if action in ("list_clips", "clips"):
        if helper is None:
            return "Wake-word clip listing needs actions/friday_wake.py, sir."
        return helper.list_clips()

    if action in ("reset", "clear", "forget"):
        if helper is None:
            return "Wake-word reset needs actions/friday_wake.py, sir."
        return helper.reset(delete_model=_bool_param(params, ("delete_model",),
                                                     default=True))

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
                and not getattr(engine, "_on_detect", None) \
                and _service._on_detect:
            engine.configure(on_detect=_service._on_detect)
        return engine.start()
    if action == "stop":
        return engine.stop()
    if action in ("status", "info"):
        state = "active" if engine._running else "inactive"
        detail = target().stats()
        trained = "trained" if getattr(engine, "available", False) else "not trained yet"
        return (
            f"Wake word is {state} ({detail['phrase']}, model {trained}). "
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
    if action == "diagnostics":
        return json.dumps(target().stats(), ensure_ascii=False, indent=2)

    return (
        f"Unknown wake word action: {action}, sir. Use status | setup | start | stop | "
        "record | train | test | list_clips | reset | pause | resume | set_threshold | "
        "devices | diagnostics."
    )


# ── standalone CLI wizard ---------------------------------------------------

_MENU = """\
Wake Word Studio — what would you like to do?
  1. Guided Setup (Record + Train + Test)
  2. Record More Clips
  3. Train Model
  4. Live Test & Calibrate
  5. Status / Clips
  6. Reset
  7. Quit
"""


def _cli_status(_args=None) -> int:
    helper = _helper()
    _engine_name, engine = _pick_engine()
    print(json.dumps(engine.stats(), ensure_ascii=False, indent=2))
    print(helper.list_clips())
    return 0


def _cli_setup(args) -> int:
    helper = _helper()
    transcript = helper.run_setup(
        phrase=getattr(args, "phrase", None),
        positives=max(1, int(args.positives)),
        negatives=max(0, int(args.negatives)),
        seconds=float(args.seconds),
        steps=int(args.steps),
        activate=True,
    )
    print()
    print(transcript)
    print()
    seconds = 8.0 if getattr(args, "test_seconds", None) is None else float(args.test_seconds)
    if seconds > 0:
        print(helper.live_test(seconds=seconds))
    return 0


def _cli_record(args) -> int:
    helper = _helper()
    result = helper.record_guided(
        positives=max(1, int(args.positives)),
        negatives=max(0, int(args.negatives)),
        seconds=float(args.seconds),
        phrase=getattr(args, "phrase", None),
    )
    print()
    print(result["message"])
    return 0 if result.get("positives_saved") else 1


def _cli_train(args) -> int:
    helper = _helper()
    print(helper.train_model(steps=int(args.steps),
                             phrase=getattr(args, "phrase", None)))
    return 0


def _cli_test(args) -> int:
    helper = _helper()
    seconds = max(1.0, min(float(args.seconds), 15.0))
    result = helper._live_test(seconds=seconds,
                               threshold=getattr(args, "threshold", None),
                               phrase=getattr(args, "phrase", None))
    print()
    print(f"{helper.confidence_bar(result['peak'])} {result['peak']:.2f}  "
          f"threshold {result['threshold']:.2f}  "
          f"{'TRIGGERED!' if result['triggered'] else 'not triggered'}")
    print(result["hint"])
    return 0 if result.get("ok") else 1


def _cli_reset(args) -> int:
    helper = _helper()
    print(helper.reset(delete_model=not getattr(args, "keep_model", False)))
    return 0


def _interactive_menu() -> int:
    while True:
        print()
        print(_MENU)
        try:
            choice = input("Choose 1-7: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice in ("7", "q", "quit", "exit", ""):
            return 0
        if choice == "1":
            phrase = input(f"Phrase [{_helper_phrase()}]: ").strip()
            print(_helper().run_setup(phrase=phrase or None))
            print(_helper().live_test(seconds=8))
        elif choice == "2":
            print(_helper().record_guided()["message"])
        elif choice == "3":
            print(_helper().train_model())
        elif choice == "4":
            print(_helper().live_test())
        elif choice == "5":
            _cli_status()
        elif choice == "6":
            confirm = input("Delete clips and the trained model? [y/N]: ").strip().lower()
            print(_helper().reset(delete_model=confirm in ("y", "yes")))
        else:
            print("Please choose a number between 1 and 7, sir.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m actions.wake_word",
        description="FRIDAY wake-word studio: guided record, train, test and reset.",
    )
    sub = parser.add_subparsers(dest="command")

    setup = sub.add_parser("setup", help="guided record -> auto-train -> live test")
    setup.add_argument("--phrase", default=None, help="custom wake phrase")
    setup.add_argument("--positives", type=int, default=5, help="positive takes (1-30)")
    setup.add_argument("--negatives", type=int, default=3, help="background clips (0-15)")
    setup.add_argument("--seconds", type=float, default=2.0, help="clip length in seconds")
    setup.add_argument("--steps", type=int, default=900, help="training steps")
    setup.add_argument("--test-seconds", type=float, default=8.0,
                       help="live confidence test length (0 skips the test)")
    setup.set_defaults(func=_cli_setup)

    record = sub.add_parser("record", help="record more clips")
    record.add_argument("--phrase", default=None, help="custom wake phrase")
    record.add_argument("--positives", type=int, default=5, help="positive takes (1-30)")
    record.add_argument("--negatives", type=int, default=3, help="background clips (0-15)")
    record.add_argument("--seconds", type=float, default=2.0, help="clip length in seconds")
    record.set_defaults(func=_cli_record)

    train = sub.add_parser("train", help="retrain from models/training/")
    train.add_argument("--steps", type=int, default=900, help="training steps")
    train.add_argument("--phrase", default=None, help="custom wake phrase")
    train.set_defaults(func=_cli_train)

    test = sub.add_parser("test", help="live confidence test with an ASCII meter")
    test.add_argument("--seconds", type=float, default=8.0, help="listen time (<=15)")
    test.add_argument("--threshold", type=float, default=None, help="override threshold")
    test.add_argument("--phrase", default=None, help="custom wake phrase")
    test.set_defaults(func=_cli_test)

    status = sub.add_parser("status", help="show engine status and saved clips")
    status.set_defaults(func=_cli_status)

    reset = sub.add_parser("reset", help="clear recorded clips / stale model")
    reset.add_argument("--keep-model", action="store_true",
                       help="keep the trained model, only delete clips")
    reset.set_defaults(func=_cli_reset)

    return parser


def main(argv=None) -> int:
    """CLI entry point; no arguments launches the interactive menu."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        return _interactive_menu()
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\nCancelled, sir.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
