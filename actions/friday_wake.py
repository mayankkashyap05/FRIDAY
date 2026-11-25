"""Friday — locally trained custom wake word ("Hey Friday").

Training is fully local and offline. Audio feature extraction uses the same
ONNX front-end as the runtime wake-word engine, so train and inference share
one feature space. All heavy libraries are imported lazily: this module must
import cleanly on a machine without torch / sounddevice / onnxruntime.

The guided workflow ("open wake word setup") is:

1. record a handful of positive takes of the phrase plus background negatives,
   each preceded by a short synthesized chime and validated against a peak
   quality gate (silent takes are retried instead of trained on);
2. expand those few takes with numpy-only augmentation (gain, time-shift,
   speed perturbation, SNR-controlled noise), add synthetic background
   windows, and train the small classifier on (16, 96) feature windows;
3. export the ONNX model atomically (``os.replace``) and reload the listener.

Every microphone-owning entry point is guarded by one non-blocking
single-flight lock so duplicate ``record`` / ``setup`` tool calls can never
open a second mic stream, and ``is_recording_active()`` lets ``main.py`` mute
its Gemini mic forwarding while the user repeats the wake phrase.
"""

from __future__ import annotations

import json
import os
import threading
import time
import wave
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"
TRAIN_DIR = MODELS_DIR / "training"
MODEL_FILE = "hey_friday_v0.1.onnx"
MODEL_PATH = MODELS_DIR / MODEL_FILE
CONFIG_FILE = MODELS_DIR / "wake_word.json"

RATE = 16000
CHANNEL = 1
FRAME = 1280                    # one openwakeword prediction frame (80 ms)
RECORD_SECONDS = 2.0
MIN_PEAK = 0.01                 # near-silent clip -> dataset warning
MIN_POSITIVE_PEAK = 0.03        # a positive take below this missed the phrase
CLIPPED_PEAK = 0.99
DEFAULT_PHRASE = "Hey Friday"
MAX_POSITIVE_RETRIES = 2
CHIME_HZ = 880.0
CHIME_SECONDS = 0.15
BAR_FULL = "█"
BAR_EMPTY = "░"

# Augmentation recipe — deliberately dependency-free (numpy only).
GAINS = (0.55, 0.8, 1.25, 1.5)
SHIFTS_MS = (-160, -80, 80, 160)
SPEED_RATES = (0.94, 1.06)
NOISE_VARIANTS = 2
NOISE_SNR_DB = (18.0, 25.0)
SYNTHETIC_LEVELS = (0.0, 0.004, 0.012, 0.03)
MAX_SYNTHETIC_NEGATIVES = 64

DETECT_THRESHOLD_FALLBACK = 0.6   # mirrors wake_word.DETECT_THRESHOLD

BUSY_RECORDING = "Wake-word recording is already in progress, sir."
BUSY_TRAINING = "Wake-word training is already in progress, sir."

# Indirection so tests can observe (and fail) the atomic replace.
_replace = os.replace

_mel = None
_emb = None
_features = None


# ── activity guard ──────────────────────────────────────────────────────────

_ACTIVITY_LOCK = threading.Lock()   # non-blocking single-flight guard
_ACTIVITY_NAME = ""                 # human name of the claimed activity
_MIC_ACTIVE = False                 # True while a recorder/test owns the mic


def _claim(name: str, mic: bool) -> bool:
    """Become the single wake-word activity, or fail immediately."""
    global _ACTIVITY_NAME, _MIC_ACTIVE
    if not _ACTIVITY_LOCK.acquire(blocking=False):
        return False
    _ACTIVITY_NAME = name
    _MIC_ACTIVE = bool(mic)
    return True


def _release() -> None:
    global _ACTIVITY_NAME, _MIC_ACTIVE
    _ACTIVITY_NAME = ""
    _MIC_ACTIVE = False
    try:
        _ACTIVITY_LOCK.release()
    except RuntimeError:                                  # pragma: no cover
        pass


def activity_in_progress() -> tuple[bool, str]:
    """Return ``(busy, activity name)`` without touching the lock."""
    return _ACTIVITY_LOCK.locked(), _ACTIVITY_NAME


def is_recording_active() -> bool:
    """True while the recorder (or a live confidence test) owns the mic.

    ``main.py`` polls this before forwarding mic audio to Gemini so the user
    can say "Hey Friday" as often as the setup needs without the live session
    hearing it, replying, or firing a duplicate ``record`` call.
    """
    return bool(_MIC_ACTIVE)


def busy_message(name: str = "recording") -> str:
    """The message returned when a second activity starts too early."""
    return BUSY_TRAINING if "train" in str(name or "").lower() else BUSY_RECORDING


# ── optional backends -------------------------------------------------------

def _sd():
    """Return sounddevice only when its PortAudio backend is usable."""
    try:
        import sounddevice as sd
        return sd
    except Exception:
        return None


def _numpy():
    try:
        import numpy as np
        return np
    except Exception:
        return None


# ── feature front-end (runtime parity) ──────────────────────────────────────

def _load_models() -> bool:
    """Load the ONNX mel/embedding front-end lazily (cached)."""
    global _mel, _emb
    if _mel is not None and _emb is not None:
        return True
    try:
        import onnxruntime as ort
        _mel = ort.InferenceSession(str(MODELS_DIR / "melspectrogram.onnx"))
        _emb = ort.InferenceSession(str(MODELS_DIR / "embedding_model.onnx"))
        return True
    except Exception as exc:
        print(f"[FridayWake] ⚠️ Feature models unavailable: {exc}")
        return False


def _embed(samples) -> "object | None":
    """Embed an int16 16 kHz array into (N, 96) per-window features."""
    import numpy as np
    if not _load_models():
        return None
    x = samples.reshape(1, -1).astype(np.float32)
    mel = np.squeeze(_mel.run(None, {"input": x})[0], axis=1)
    frames = mel[0]
    if len(frames) < 76:
        return None
    windows = np.stack([frames[i:i + 76] for i in range(0, len(frames) - 76, 10)])
    embedded = _emb.run(None, {"input_1": windows[..., None].astype(np.float32)})[0]
    return embedded.reshape(-1, 96)


def _get_features() -> "object":
    global _features
    if _features is None:
        from openwakeword.utils import AudioFeatures
        _features = AudioFeatures()
    return _features


def _windows(clip, stride: int = 4, limit: int = 1024):
    """Convert a clip to (16, 96) classifier windows using the runtime path."""
    import numpy as np
    features = _get_features()
    features.reset()
    features(clip.astype(np.int16))
    rows = np.array(features.feature_buffer, dtype=np.float32)
    if len(rows) < 16:
        return np.empty((0, 16, 96), dtype=np.float32)
    count = min(len(rows) - 15, limit * stride)
    return np.stack([rows[i:i + 16] for i in range(0, count, stride)]).astype(np.float32)


# ── clip metadata / prompts ------------------------------------------------

def _clip_stats(path: Path) -> dict:
    """Return duration/peak metadata for a recorded WAV clip."""
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            data = handle.readframes(frames)
        import numpy as np
        samples = np.frombuffer(data, dtype=np.int16)
        peak = float(np.max(np.abs(samples))) / 32768.0 if len(samples) else 0.0
        return {"seconds": frames / rate if rate else 0.0, "peak": peak, "rate": rate}
    except Exception:
        return {"seconds": 0.0, "peak": 0.0, "rate": 0}


def _announce(message, player=None) -> None:
    """Print a prompt and mirror it into the FRIDAY UI log when available."""
    message = str(message)
    print(f"[FridayWake] {message}")
    write_log = getattr(player, "write_log", None)
    if callable(write_log):
        try:
            write_log(f"SYS: {message}")
        except Exception:
            pass


def classify_peak(peak: float, kind: str = "positive") -> str:
    """Return the per-clip quality badge: ``OK`` / ``TOO QUIET`` / ``CLIPPED``."""
    if peak >= CLIPPED_PEAK:
        return "CLIPPED"
    if peak < (MIN_POSITIVE_PEAK if kind == "positive" else MIN_PEAK):
        return "TOO QUIET"
    return "OK"


def confidence_bar(score: float, width: int = 10) -> str:
    """Render an ASCII confidence meter, e.g. ``[████████░░]``."""
    clamped = max(0.0, min(1.0, float(score)))
    filled = int(round(clamped * width))
    return "[" + BAR_FULL * filled + BAR_EMPTY * (width - filled) + "]"


# ── chime ───────────────────────────────────────────────────────────────────

def play_chime(sd=None) -> bool:
    """Play a soft 880 Hz / 150 ms sine cue; never raises, never blocks long."""
    np = _numpy()
    sd = sd if sd is not None else _sd()
    if np is None or sd is None:
        return False
    try:
        count = max(1, int(CHIME_SECONDS * RATE))
        t = np.arange(count, dtype=np.float32) / float(RATE)
        tone = np.sin(2.0 * np.pi * CHIME_HZ * t) * 0.22
        fade = min(count // 2, max(1, int(0.02 * RATE)))
        if fade and count > 2 * fade:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            tone[:fade] *= ramp
            tone[-fade:] *= ramp[::-1]
        sd.play(tone.astype(np.float32), RATE)
        sd.wait()
        return True
    except Exception:
        return False


# ── phrase / config persistence ─────────────────────────────────────────────

def load_config() -> dict:
    """Return the persisted wake-word config (empty dict when absent/broken)."""
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_config(**values) -> dict:
    """Merge ``values`` into the persisted config atomically; return the result."""
    config = load_config()
    config.update({key: value for key, value in values.items() if value is not None})
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_name(f".{CONFIG_FILE.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        _replace(str(tmp), str(CONFIG_FILE))
    except Exception as exc:
        print(f"[FridayWake] ⚠️ Could not save wake-word config: {exc}")
    return config


def normalise_phrase(phrase, default: str = DEFAULT_PHRASE) -> str:
    """Title-case a spoken phrase, preserving its word order ("hey friday")."""
    text = " ".join(str(phrase or "").split())
    if not text:
        return default
    return text if text != text.lower() else text.title()


def current_phrase(default: str = DEFAULT_PHRASE) -> str:
    """The phrase the user last set up (falls back to "Hey Friday")."""
    return normalise_phrase(load_config().get("phrase"), default)


# ── clip bookkeeping -------------------------------------------------------

def _next_clip_index(folder: Path, prefix: str) -> int:
    """First unused ``prefix_NN`` index so re-recording never overwrites."""
    highest = -1
    for path in folder.glob(f"{prefix}_*.wav"):
        stem = path.stem[len(prefix) + 1:].split("_")[0]
        if stem.isdigit():
            highest = max(highest, int(stem))
    return highest + 1


def _load_clips(folder: Path) -> list:
    """Load valid 16 kHz WAV clips plus their paths."""
    import numpy as np
    clips = []
    for path in sorted(folder.glob("*.wav")):
        try:
            with wave.open(str(path), "rb") as handle:
                data = np.frombuffer(handle.readframes(handle.getnframes()),
                                     dtype=np.int16)
                rate = handle.getframerate()
            if rate == RATE and len(data):
                clips.append((data.astype(np.float32), path))
        except Exception:
            continue
    return clips


def _write_clip(path: Path, samples) -> None:
    import numpy as np
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray(samples)
    if data.dtype.kind == "f":
        data = np.clip(data, -1.0, 1.0) * 32767.0
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(data.astype(np.int16).tobytes())


def dataset_info(root: Path = None) -> str:
    """Summarise clip counts, durations and quality warnings."""
    root = root or TRAIN_DIR
    pos, neg = _load_clips(root / "positive"), _load_clips(root / "negative")
    lines = [f"Wake-word dataset: {len(pos)} positive, {len(neg)} negative clip(s)."]
    low = []
    for _, path in pos + neg:
        stats = _clip_stats(path)
        if stats["peak"] < MIN_PEAK or stats["seconds"] < 1.0:
            low.append(path.name)
    if low:
        lines.append(f"Quality warning: {len(low)} low-volume/short clip(s): "
                     + ", ".join(low[:5]))
    if len(pos) < 5 or len(neg) < 5:
        lines.append("Quality warning: at least 5 clips per class are recommended.")
    return " ".join(lines)


def list_clips(root: Path = None) -> str:
    """Human-readable clip inventory with per-clip peaks and quality badges."""
    root = Path(root) if root is not None else TRAIN_DIR
    lines = [f"Wake-word clips in {root}:"]
    total = 0
    for label, folder in (("positive", root / "positive"), ("negative", root / "negative")):
        paths = sorted(folder.glob("*.wav")) if folder.is_dir() else []
        total += len(paths)
        lines.append(f"  {label}: {len(paths)} clip(s)")
        for path in paths:
            stats = _clip_stats(path)
            badge = classify_peak(stats["peak"], label)
            lines.append(f"    {path.name:<22} {stats['seconds']:.2f}s  "
                         f"peak {stats['peak']:.2f}  {badge}")
    if not total:
        lines.append("  (no clips recorded yet — say 'open wake word setup')")
    phrase = current_phrase()
    lines.append(f"Phrase: '{phrase}'. Model: "
                 f"{'present' if MODEL_PATH.exists() else 'not trained yet'}.")
    return "\n".join(lines)


def _clear_clips(root: Path) -> int:
    removed = 0
    for folder in (root / "positive", root / "negative"):
        if folder.is_dir():
            for path in folder.glob("*.wav"):
                try:
                    path.unlink()
                    removed += 1
                except Exception:
                    continue
    return removed


def clear_dataset(root: Path = None) -> str:
    """Delete recorded training clips (never touches the exported model)."""
    root = root or TRAIN_DIR
    removed = _clear_clips(root)
    return f"Removed {removed} wake-word training clip(s)."


# ── numpy-only preprocessing / augmentation ────────────────────────────────

def variant_count() -> int:
    """How many audio variants ``augment_clip`` produces per recorded take."""
    return 1 + len(GAINS) + len(SHIFTS_MS) + len(SPEED_RATES) + NOISE_VARIANTS


def centre_clip(clip):
    """Trim leading/trailing silence and centre the speech burst."""
    import numpy as np
    samples = np.asarray(clip, dtype=np.int16).reshape(-1)
    if samples.size == 0:
        return samples
    threshold = max(1.0, MIN_POSITIVE_PEAK * 32768.0 * 0.6)
    loud = np.flatnonzero(np.abs(samples.astype(np.int32)) >= threshold)
    if loud.size == 0:
        return samples
    burst = samples[int(loud[0]):int(loud[-1]) + 1]
    if burst.size >= samples.size:
        return samples
    lead = (samples.size - burst.size) // 2
    centred = np.zeros(samples.size, dtype=np.int16)
    centred[lead:lead + burst.size] = burst
    return centred


def shift_clip(clip, offset: int):
    """Shift a clip by ``offset`` samples (zero fill, no wraparound)."""
    import numpy as np
    samples = np.asarray(clip, dtype=np.int16).reshape(-1)
    if offset == 0 or samples.size == 0:
        return samples.copy()
    out = np.zeros_like(samples)
    if offset > 0:
        if offset < samples.size:
            out[offset:] = samples[:samples.size - offset]
    else:
        count = -offset
        if count < samples.size:
            out[:samples.size - count] = samples[count:]
    return out


def gain_clip(clip, factor: float):
    """Scale amplitude by ``factor`` and hard-clip to the int16 range."""
    import numpy as np
    scaled = np.asarray(clip, dtype=np.float32) * float(factor)
    return np.clip(scaled, -32768.0, 32767.0).astype(np.int16)


def speed_clip(clip, rate: float):
    """Resample by ``rate`` via linear interpolation (0.94x / 1.06x style)."""
    import numpy as np
    samples = np.asarray(clip, dtype=np.float32).reshape(-1)
    if samples.size < 2 or rate <= 0 or abs(rate - 1.0) < 1e-6:
        return samples.astype(np.int16)
    target = max(1, int(round(samples.size / float(rate))))
    x_old = np.linspace(0.0, 1.0, num=samples.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=target, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.int16)


def mix_noise(clip, noise, snr_db: float):
    """Add ``noise`` at the requested SNR (dB) to ``clip``."""
    import numpy as np
    signal = np.asarray(clip, dtype=np.float32).reshape(-1)
    bed = np.asarray(noise, dtype=np.float32).reshape(-1)
    if signal.size == 0:
        return signal.astype(np.int16)
    if bed.size < signal.size:
        bed = np.resize(bed, signal.size)
    bed = bed[:signal.size]
    signal_rms = float(np.sqrt(np.mean(np.square(signal)))) or 1.0
    bed_rms = float(np.sqrt(np.mean(np.square(bed)))) or 1.0
    target = signal_rms / (10.0 ** (float(snr_db) / 20.0))
    return np.clip(signal + bed * (target / bed_rms), -32768.0, 32767.0).astype(np.int16)


def synthetic_negative(seconds: float = RECORD_SECONDS, level: float = 0.0,
                       rng=None):
    """Synthesise a background window: digital silence or a soft room tone."""
    import numpy as np
    rng = rng if rng is not None else np.random.default_rng(0)
    count = max(RATE, int(float(seconds) * RATE))
    if level <= 0:
        return np.zeros(count, dtype=np.int16)
    noise = rng.normal(0.0, 1.0, count)
    smoothed = np.convolve(noise, np.ones(48, dtype=np.float32) / 48.0, mode="same")
    return np.clip(smoothed * (float(level) * 32768.0 * 3.0),
                   -32767.0, 32767.0).astype(np.int16)


def augment_clip(clip, rng=None, noise_beds=None) -> list:
    """Expand one positive take into gain/shift/speed/noise variants."""
    import numpy as np
    rng = rng if rng is not None else np.random.default_rng(0)
    base = centre_clip(clip)
    variants = [base]
    for factor in GAINS:
        variants.append(gain_clip(base, factor))
    for millis in SHIFTS_MS:
        variants.append(shift_clip(base, int(RATE * millis / 1000.0)))
    for rate in SPEED_RATES:
        variants.append(speed_clip(base, rate))

    beds = [np.asarray(bed, dtype=np.float32).reshape(-1) for bed in (noise_beds or [])]
    for _ in range(NOISE_VARIANTS):
        if beds:
            bed = beds[int(rng.integers(0, len(beds)))]
        else:
            bed = rng.normal(0.0, 1600.0, base.size).astype(np.float32)
        variants.append(mix_noise(base, bed, float(rng.uniform(*NOISE_SNR_DB))))
    return variants


def _augment(windows, rng):
    """Window-level noise + temporal-shift augmentation (kept for parity)."""
    import numpy as np
    noise = rng.normal(0.0, 0.02, windows.shape).astype(np.float32)
    shifted = np.empty_like(windows)
    for index, shift in enumerate(rng.integers(-2, 3, size=len(windows))):
        if shift == 0:
            shifted[index] = windows[index]
        elif shift > 0:
            shifted[index, :-shift] = windows[index, shift:]
            shifted[index, -shift:] = windows[index, 0]
        else:
            shifted[index, -shift:] = windows[index, :shift]
            shifted[index, :-shift] = windows[index, -1]
    return np.concatenate([windows, windows + noise, shifted])


def build_training_data(pos_clips: list, neg_clips: list, rng=None,
                        windows_fn=None) -> dict:
    """Build classifier windows from a handful of raw clips (pure numpy).

    Positives are expanded with :func:`augment_clip`, negatives are topped up
    with synthetic background/silence windows so training never collapses just
    because the user recorded only a couple of background samples.
    """
    import numpy as np
    rng = rng if rng is not None else np.random.default_rng(0)
    windows_fn = windows_fn or _windows
    noise_beds = [clip for clip, _path in neg_clips] or None

    failures: list[str] = []

    def collect(clips, augment: bool) -> tuple[list, int]:
        chunks, variants = [], 0
        for clip, _path in clips:
            takes = augment_clip(clip, rng, noise_beds) if augment else [centre_clip(clip)]
            for take in takes:
                variants += 1
                try:
                    got = windows_fn(take)
                except Exception as exc:
                    if not failures:
                        failures.append(str(exc))
                    continue
                if got is not None and len(got):
                    chunks.append(np.asarray(got, dtype=np.float32))
        return chunks, variants

    # One clip per class is held out when possible; with a single recording the
    # same take is scored un-augmented (still a useful separation check).
    if len(pos_clips) >= 2:
        val_pos, train_pos = pos_clips[:1], pos_clips[1:]
    else:
        val_pos, train_pos = list(pos_clips), list(pos_clips)
    if len(neg_clips) >= 2:
        val_neg, train_neg = neg_clips[:1], neg_clips[1:]
    else:
        val_neg, train_neg = list(neg_clips), list(neg_clips)

    pos_chunks, pos_variants = collect(train_pos, augment=True)
    val_pos_chunks, _ = collect(val_pos, augment=False)
    neg_chunks, _ = collect(train_neg, augment=False)
    val_neg_chunks, _ = collect(val_neg, augment=False)

    if not pos_chunks or not val_pos_chunks:
        if failures:
            raise ValueError(f"the audio feature front-end failed ({failures[0][:160]})")
        raise ValueError("positive clips are too short for feature extraction")

    pos_windows = np.concatenate(pos_chunks)
    pos_windows = _augment(pos_windows, rng)
    neg_windows = (np.concatenate(neg_chunks) if neg_chunks
                   else np.empty((0, 16, 96), dtype=np.float32))

    synthetic = 0
    while len(neg_windows) < 2 * len(pos_windows) and synthetic < MAX_SYNTHETIC_NEGATIVES:
        level = SYNTHETIC_LEVELS[synthetic % len(SYNTHETIC_LEVELS)]
        try:
            got = windows_fn(synthetic_negative(RECORD_SECONDS, level, rng))
        except Exception:
            got = None
        if got is not None and len(got):
            neg_windows = np.concatenate([neg_windows, np.asarray(got, dtype=np.float32)])
        synthetic += 1
    if len(neg_windows) == 0:
        raise ValueError("no negative windows could be produced")

    val_neg = (np.concatenate(val_neg_chunks) if val_neg_chunks
               else np.empty((0, 16, 96), dtype=np.float32))
    extra = 0
    while len(val_neg) < 2 and extra < 4:
        try:
            got = windows_fn(synthetic_negative(RECORD_SECONDS, 0.012, rng))
        except Exception:
            got = None
        if got is not None and len(got):
            val_neg = np.concatenate([val_neg, np.asarray(got, dtype=np.float32)])
        extra += 1
    if len(val_neg) == 0:
        raise ValueError("no validation negatives could be produced")

    val_pos = np.concatenate(val_pos_chunks)
    return {
        "X": np.concatenate([pos_windows, neg_windows]),
        "y": np.concatenate([np.ones(len(pos_windows), dtype=np.float32),
                             np.zeros(len(neg_windows), dtype=np.float32)]),
        "Xv": np.concatenate([val_pos, val_neg]),
        "yv": np.concatenate([np.ones(len(val_pos), dtype=np.float32),
                              np.zeros(len(val_neg), dtype=np.float32)]),
        "stats": {
            "pos_clips": len(pos_clips),
            "pos_train_clips": len(train_pos),
            "pos_variants": pos_variants,
            "pos_windows": int(len(pos_windows)),
            "neg_clips": len(neg_clips),
            "neg_synthetic": synthetic,
            "neg_windows": int(len(neg_windows)),
            "val_pos_windows": int(len(val_pos)),
            "val_neg_windows": int(len(val_neg)),
        },
    }


# ── recording ───────────────────────────────────────────────────────────────

def record_clip(path: Path, kind: str = "positive", seconds: float = RECORD_SECONDS,
                prompt: str = "", player=None, sd=None,
                max_retries: int = MAX_POSITIVE_RETRIES) -> dict:
    """Record one validated clip.

    Positive takes quieter than :data:`MIN_POSITIVE_PEAK` are retried up to
    ``max_retries`` times instead of saving silence; a final failure returns
    ``saved=False`` with guidance rather than raising.
    """
    np = _numpy()
    sd = sd if sd is not None else _sd()
    path = Path(path)
    result = {"kind": kind, "name": path.name, "path": str(path), "peak": 0.0,
              "quality": "NO DEVICE", "attempts": 0, "saved": False, "message": ""}
    if np is None:
        result["message"] = "numpy is required to record wake-word clips."
        return result
    if sd is None:
        result["message"] = ("No microphone backend available — install "
                             "sounddevice and PortAudio first, sir.")
        return result

    attempts = 1 + (max(1, int(max_retries)) if kind == "positive" else 0)
    for attempt in range(1, attempts + 1):
        result["attempts"] = attempt
        label = prompt or ("positive sample" if kind == "positive" else "background sample")
        if attempt == 1:
            _announce(f"🎙 {label}", player)
        else:
            _announce(f"🎙 retry {attempt - 1}/{attempts - 1} — {label}", player)
        play_chime(sd=sd)
        time.sleep(0.05)
        try:
            audio = sd.rec(int(float(seconds) * RATE), samplerate=RATE,
                           channels=CHANNEL, dtype="int16")
            sd.wait()
        except Exception as exc:
            result.update(quality="ERROR", message=f"Recording failed: {exc}")
            _announce(f"⚠️ Recording failed: {exc}", player)
            return result

        samples = np.asarray(audio).reshape(-1)
        if samples.dtype.kind == "f":
            samples = np.clip(samples, -1.0, 1.0) * 32767.0
        samples = samples.astype(np.int16)
        peak = (float(np.max(np.abs(samples))) / 32768.0) if samples.size else 0.0
        quality = classify_peak(peak, kind)
        result.update(peak=peak, quality=quality)

        if kind == "positive" and peak < MIN_POSITIVE_PEAK:
            # Never train (or even keep) a silent take: retry, then explain.
            if attempt < attempts:
                _announce(f"👂 TOO QUIET (peak {peak:.2f}) — nothing captured, "
                          f"please speak again.", player)
                continue
            result["message"] = (
                f"Discarded {path.name}: every take was TOO QUIET (peak "
                f"{peak:.2f}). Check Windows Settings → Privacy → Microphone, pick "
                f"the right input device ('list wake word devices'), then try again.")
            _announce(f"⚠️ {result['message']}", player)
            return result

        try:
            _write_clip(path, samples)
        except Exception as exc:
            result.update(saved=False, message=f"Could not save the clip: {exc}")
            _announce(f"⚠️ Could not save {path.name}: {exc}", player)
            return result

        result["saved"] = True
        warning = " — mic is clipping, lower the input gain" if quality == "CLIPPED" else ""
        result["message"] = f"{path.name} peak {peak:.2f} {quality}{warning}"
        _announce(f"✓ {result['message']}", player)
        return result

    result["message"] = ("Discarded — every take was TOO QUIET. Check the "
                         "microphone privacy setting and input device, then try again.")
    _announce(f"⚠️ {result['message']}", player)
    return result


def _record_guided(positives: int, negatives: int, phrase: str,
                   seconds: float, player, sd, max_retries: int,
                   resume_listener: bool, engine) -> dict:
    """Internal guided recording pass (caller owns the activity lock)."""
    phrase = normalise_phrase(phrase, current_phrase())
    try:
        positives = max(1, min(int(positives), 30))
    except (TypeError, ValueError):
        positives = 5
    try:
        negatives = max(0, min(int(negatives), 15))
    except (TypeError, ValueError):
        negatives = 3
    try:
        seconds = max(0.5, min(float(seconds), 5.0))
    except (TypeError, ValueError):
        seconds = RECORD_SECONDS

    save_config(phrase=phrase)
    pos_dir, neg_dir = TRAIN_DIR / "positive", TRAIN_DIR / "negative"
    pos_dir.mkdir(parents=True, exist_ok=True)
    neg_dir.mkdir(parents=True, exist_ok=True)

    result = {"ok": False, "phrase": phrase, "positives_requested": positives,
              "positives_saved": 0, "negatives_requested": negatives,
              "negatives_saved": 0, "skipped": 0, "errors": 0, "clips": [],
              "message": ""}

    sd = sd if sd is not None else _sd()
    if sd is None:
        result["message"] = ("No microphone backend available — install "
                             "sounddevice and PortAudio first, sir.")
        return result

    resume_after = False
    if resume_listener and engine is not None:
        try:
            if getattr(engine, "_running", False):
                engine.stop()
                resume_after = True
        except Exception:
            resume_after = False

    _announce(f"Guided recording for '{phrase}': {positives} positive + "
              f"{negatives} background clip(s).", player)
    try:
        index = _next_clip_index(pos_dir, "pos")
        for offset in range(positives):
            prompt = (f"Say '{phrase}' ({offset + 1}/{positives}) — "
                      f"speak naturally after the beep…")
            clip = record_clip(pos_dir / f"pos_{index + offset:02d}.wav",
                               kind="positive", seconds=seconds, prompt=prompt,
                               player=player, sd=sd, max_retries=max_retries)
            clip["kind"] = "positive"
            result["clips"].append(clip)
            if clip["saved"]:
                result["positives_saved"] += 1
            else:
                result["skipped"] += 1
                result["errors"] += clip["quality"] == "ERROR"

        index = _next_clip_index(neg_dir, "neg")
        for offset in range(negatives):
            prompt = (f"Background sample ({offset + 1}/{negatives}) — stay quiet or "
                      f"say normal words (NOT '{phrase}')…")
            clip = record_clip(neg_dir / f"neg_{index + offset:02d}.wav",
                               kind="negative", seconds=seconds, prompt=prompt,
                               player=player, sd=sd, max_retries=0)
            clip["kind"] = "negative"
            result["clips"].append(clip)
            if clip["saved"]:
                result["negatives_saved"] += 1
            else:
                result["skipped"] += 1
                result["errors"] += clip["quality"] == "ERROR"
    finally:
        if resume_after and engine is not None:
            try:
                engine.start()
            except Exception:
                pass

    result["ok"] = result["positives_saved"] > 0
    badges = "\n".join(
        f"  {clip['name']:<22} peak {clip['peak']:.2f}  {clip['quality']}"
        for clip in result["clips"]
    )
    if not result["ok"]:
        if result["errors"]:
            first_error = next((clip["message"] for clip in result["clips"]
                                if clip["quality"] == "ERROR"), "unknown error")
            result["message"] = (
                f"Recorded 0/{positives} usable positive clips — the microphone "
                f"backend failed.\n{badges}\n{first_error}\nFix the microphone "
                f"(see 'list wake word devices'), then say 'open wake word setup' again.")
        else:
            result["message"] = (
                f"Recorded 0/{positives} usable positive clips — every take was "
                f"TOO QUIET.\n{badges}\nCheck Windows Settings → Privacy → "
                f"Microphone, pick another input device ('list wake word devices'), "
                f"then say 'open wake word setup' again.")
        return result

    hint = ("Say 'train the wake word' (or use action='setup') to train and activate it."
            if result["negatives_saved"] or negatives == 0 else
            "No usable background clips were saved; training will synthesise them.")
    result["message"] = (
        f"Recorded {result['positives_saved']}/{positives} positive and "
        f"{result['negatives_saved']}/{negatives} negative clip(s) for '{phrase}'.\n"
        f"{badges}\n{hint}")
    _announce(result["message"], player)
    return result


def record_dataset(n_pos: int = 5, n_neg: int = 3, seconds: float = RECORD_SECONDS,
                   prompt: str = "hey friday", phrase: str = None,
                   player=None, sd=None, max_retries: int = MAX_POSITIVE_RETRIES,
                   resume_listener: bool = True, engine=None) -> dict:
    """Guided positive + negative recording (single-flight, returned as a dict)."""
    if not _claim("recording", mic=True):
        return {"ok": False, "busy": True, "message": busy_message("recording"),
                "clips": [], "positives_saved": 0, "negatives_saved": 0}
    # NOTE: the busy result is what ``_FridayEngine.record`` returns verbatim, so
    # duplicate back-to-back tool calls simply report "already in progress".
    try:
        return _record_guided(n_pos, n_neg, phrase or prompt, seconds, player, sd,
                              max_retries, resume_listener, engine)
    finally:
        _release()


def record_guided(positives: int = 5, negatives: int = 3, phrase: str = None,
                  seconds: float = RECORD_SECONDS, player=None, sd=None,
                  max_retries: int = MAX_POSITIVE_RETRIES, engine=None) -> dict:
    """Public guided recording entry point (see :func:`record_dataset`)."""
    return record_dataset(positives, negatives, seconds, phrase=phrase,
                          player=player, sd=sd, max_retries=max_retries,
                          engine=engine)


def _save_clip_list(path: Path) -> list:
    """Clips currently saved under ``path`` (used to diff a setup run)."""
    return sorted(path.glob("*.wav")) if path.is_dir() else []


# ── training ───────────────────────────────────────────────────────────────

def _train_torch(X, y, Xv, yv, steps: int = 900):
    """Train the small MLP on (16, 96) windows; returns (export_model, metrics)."""
    import numpy as np
    import torch
    import torch.nn as nn

    torch.manual_seed(0)
    model = nn.Sequential(
        nn.Flatten(),
        nn.Linear(16 * 96, 160), nn.ReLU(), nn.Dropout(0.2),
        nn.Linear(160, 160), nn.ReLU(), nn.Dropout(0.2),
        nn.Linear(160, 1),
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = torch.nn.BCEWithLogitsLoss()
    xt, yt = torch.from_numpy(X.astype(np.float32)), torch.from_numpy(y.astype(np.float32))
    xv, yvv = torch.from_numpy(Xv.astype(np.float32)), torch.from_numpy(yv.astype(np.float32))
    best_acc, best_step, best_state = -1.0, 0, None
    steps = max(1, int(steps))

    for step in range(1, steps + 1):
        index = torch.randperm(len(xt))[:64]
        optimizer.zero_grad()
        loss = loss_fn(model(xt[index]).squeeze(1), yt[index])
        loss.backward()
        optimizer.step()
        if step % 50 == 0 or step == 1:
            with torch.no_grad():
                predictions = (torch.sigmoid(model(xv)) > 0.5).float().squeeze(1)
                accuracy = (predictions == yvv).float().mean().item()
            print(f"[FridayWake] step {step}/{steps} loss {loss.item():.4f} "
                  f"val_acc {accuracy:.2f}")
            if accuracy > best_acc:
                best_acc, best_step = accuracy, step
                best_state = {k: v.clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        scores = torch.sigmoid(model(xv).squeeze(1)).numpy()
    pos_scores = scores[yv > 0.5]
    neg_scores = scores[yv <= 0.5]
    pos_mean = float(np.mean(pos_scores)) if len(pos_scores) else 0.0
    neg_mean = float(np.mean(neg_scores)) if len(neg_scores) else 0.0
    separation = pos_mean - neg_mean
    suggested = float(np.clip((pos_mean + neg_mean) / 2.0, 0.3, 0.9))
    metrics = {
        "accuracy": float(max(best_acc, 0.0)),
        "best_step": int(best_step),
        "steps": steps,
        "pos_mean": pos_mean,
        "neg_mean": neg_mean,
        "separation": separation,
        "suggested_threshold": round(suggested, 2),
    }
    export_model = nn.Sequential(model, nn.Sigmoid())
    export_model.eval()
    return export_model, metrics


def _export_onnx(export_model, path, output_name: str = "hey_friday"):
    """Export the trained classifier to ONNX (path may be a temp file)."""
    import torch
    torch.onnx.export(
        export_model, torch.randn(1, 16, 96), str(path), opset_version=13,
        input_names=["features"], output_names=[output_name],
        dynamic_axes={"features": {0: "batch"}},
    )


def _training_message(phrase: str, stats: dict, metrics: dict, export: Path,
                      warnings: list) -> str:
    sep = metrics.get("separation", 0.0)
    accuracy = metrics.get("accuracy", 0.0)
    lines = [
        f"Training complete for '{phrase}'.",
        (f"Data: {stats['pos_clips']} real positive clip(s) → "
         f"{stats['pos_variants']} augmented variants ({stats['pos_windows']} windows); "
         f"{stats['neg_clips']} real + {stats['neg_synthetic']} synthetic negative clip(s)."),
        (f"Held-out accuracy {accuracy:.0%} — positive score "
         f"{metrics.get('pos_mean', 0.0):.2f} vs negative "
         f"{metrics.get('neg_mean', 0.0):.2f} (separation {sep:+.2f})."),
        f"Suggested threshold {metrics.get('suggested_threshold', 0.6):.2f}.",
        f"Model saved atomically to {export.name}.",
    ]
    if accuracy < 0.7 or sep < 0.3:
        lines.append("Training quality is low — record 3-5 more takes, keep "
                     "background clips free of the phrase, then retrain.")
    if warnings:
        lines.append("Quality warnings: " + "; ".join(warnings[:3]))
    lines.append("Say 'start the wake word' (or use action='setup') to activate it.")
    return "\n".join(lines)


def _train_model(steps: int = 900, root: Path = None, export: Path = None,
                 phrase: str = None) -> tuple:
    """Internal trainer: returns ``(ok, message)``, never raises."""
    root = Path(root) if root is not None else TRAIN_DIR
    export = Path(export) if export is not None else MODEL_PATH
    phrase = normalise_phrase(phrase, current_phrase())

    pos_clips = _load_clips(root / "positive")
    neg_clips = _load_clips(root / "negative")
    if not pos_clips:
        return False, ("No positive clips found in models/training/positive yet, sir. "
                       "Record them with 'open wake word setup' (or "
                       "`python -m actions.wake_word record`).")

    warnings = []
    for _, path in pos_clips + neg_clips:
        stats = _clip_stats(path)
        if stats["peak"] < MIN_PEAK:
            warnings.append(f"{path.name} is nearly silent (peak {stats['peak']:.3f})")
    if warnings:
        print("[FridayWake] Quality warnings: " + "; ".join(warnings[:5]))

    try:
        data = build_training_data(pos_clips, neg_clips)
    except ValueError as exc:
        return False, (f"Could not build a training set: {exc}. Check that "
                       f"models/melspectrogram.onnx and models/embedding_model.onnx "
                       f"exist and that `pip install openwakeword onnxruntime` "
                       f"succeeded, then record fresh takes.")
    except Exception as exc:
        return False, f"Feature extraction failed: {exc}"

    print(f"[FridayWake] Training on {len(data['X'])} windows "
          f"({data['stats']['pos_variants']} augmented positive takes)…")
    try:
        export_model, metrics = _train_torch(data["X"], data["y"],
                                             data["Xv"], data["yv"], steps)
    except ImportError as exc:
        return False, f"torch is not installed. Run: pip install torch ({exc})"
    except Exception as exc:
        return False, f"Training failed: {exc}"

    export.parent.mkdir(parents=True, exist_ok=True)
    tmp = export.with_name(f".{export.name}.tmp-{os.getpid()}-{int(time.time() * 1000)}")
    try:
        _export_onnx(export_model, tmp, output_name=export.stem)
        if not Path(tmp).exists():
            raise RuntimeError("ONNX export produced no file")
        _replace(str(tmp), str(export))
    except Exception as exc:
        try:
            Path(tmp).unlink()
        except OSError:
            pass
        return False, ("Model export failed — the existing model (if any) was left "
                       f"untouched: {exc}")

    save_config(phrase=phrase, threshold=metrics.get("suggested_threshold"))
    return True, _training_message(phrase, data["stats"], metrics, export, warnings)


def train_model(steps: int = 900, root: Path = None, export: Path = None,
                phrase: str = None) -> str:
    """Train (and atomically export) the classifier from ``models/training``."""
    if not _claim("training", mic=False):
        return busy_message("training")
    try:
        try:
            steps = max(50, min(int(steps), 5000))
        except (TypeError, ValueError):
            steps = 900
        return _train_model(steps=steps, root=root, export=export, phrase=phrase)[1]
    finally:
        _release()


def evaluate(root: Path = None, export: Path = None) -> str:
    """Evaluate the exported model on held-out clips (no retraining)."""
    import numpy as np
    import onnxruntime as ort
    root, export = root or TRAIN_DIR, export or MODEL_PATH
    if not export.exists():
        return "No trained wake-word model found, sir."
    pos, neg = _load_clips(root / "positive"), _load_clips(root / "negative")
    if not pos or not neg:
        return "No evaluation clips found, sir."
    try:
        session = ort.InferenceSession(str(export))
        input_name = session.get_inputs()[0].name
        correct = total = 0
        for clips, expected in ((pos, 1), (neg, 0)):
            for clip, _ in clips:
                windows = _windows(clip)
                if len(windows) == 0:
                    continue
                scores = session.run(None, {input_name: windows.astype(np.float32)})[0]
                predicted = 1 if float(np.mean(scores)) >= 0.5 else 0
                correct += int(predicted == expected)
                total += 1
        if not total:
            return "No evaluable clips found, sir."
        return f"Wake-word evaluation accuracy: {correct / total:.0%} on {total} clip(s)."
    except Exception as exc:
        return f"Wake-word evaluation failed: {exc}"


# ── live test & calibration ────────────────────────────────────────────────

def _live_test(seconds: float = 8.0, threshold=None, phrase: str = None,
               player=None, sd=None) -> dict:
    """Score the live microphone against the trained model (internal)."""
    np = _numpy()
    try:
        seconds = max(1.0, min(float(seconds), 15.0))
    except (TypeError, ValueError):
        seconds = 8.0
    phrase = normalise_phrase(phrase, current_phrase())
    result = {"ok": False, "peak": 0.0, "threshold": 0.6, "triggered": False,
              "hint": "", "message": ""}

    if not MODEL_PATH.exists():
        result["message"] = ("No trained wake-word model yet, sir. Run 'open wake word "
                             "setup' (or `python -m actions.wake_word setup`) first.")
        result["hint"] = result["message"]
        return result
    if np is None:
        result["message"] = "numpy is required for the live wake-word test."
        return result

    from actions.wake_word import WakeWordService
    service = WakeWordService(model_file=MODEL_FILE, phrase=phrase)
    if not service.load():
        result["message"] = (f"Could not load the wake-word model: "
                             f"{service._last_error or 'unknown error'}")
        result["hint"] = result["message"]
        return result

    try:
        limit = float(threshold) if threshold is not None else float(service._threshold)
    except (TypeError, ValueError):
        limit = float(service._threshold)
    if not 0.1 <= limit <= 0.99:
        limit = float(service._threshold)
    result["threshold"] = limit

    sd = sd if sd is not None else _sd()
    if sd is None:
        result["message"] = "No microphone backend available for the live test, sir."
        result["hint"] = result["message"]
        return result

    _announce(f"🎧 Live test: say '{phrase}' now ({seconds:.0f}s)…", player)
    play_chime(sd=sd)
    try:
        audio = sd.rec(int(seconds * RATE), samplerate=RATE, channels=CHANNEL,
                       dtype="int16")
        sd.wait()
    except Exception as exc:
        result["message"] = f"Live test recording failed: {exc}"
        result["hint"] = result["message"]
        return result

    samples = np.asarray(audio).reshape(-1)
    if samples.dtype.kind == "f":
        samples = np.clip(samples, -1.0, 1.0) * 32767.0
    samples = samples.astype(np.int16)

    peak = 0.0
    triggered = False
    frames = 0
    for start in range(0, max(0, len(samples) - FRAME + 1), FRAME):
        score = service.score_frame(samples[start:start + FRAME])
        frames += 1
        peak = max(peak, score)
        hit = score >= limit
        triggered = triggered or hit
        print(f"  {confidence_bar(score)} {score:.2f} "
              f"{'TRIGGERED!' if hit else '          '}", end="\r", flush=True)
    print()

    if frames == 0:
        result["message"] = "The live test captured no usable audio, sir."
        result["hint"] = result["message"]
        return result

    if triggered:
        hint = (f"Peak {peak:.2f} vs threshold {limit:.2f} — the wake word triggered. "
                f"Say '{phrase}' to wake me once the listener is active.")
    elif peak >= max(0.2, limit - 0.2):
        suggestion = round(max(0.3, peak - 0.03), 2)
        hint = (f"Peak {peak:.2f} vs threshold {limit:.2f} — try lowering threshold "
                f"to {suggestion:.2f} (say 'set wake word threshold to {suggestion:.2f}') "
                f"or recording 3 more samples.")
    else:
        hint = (f"Peak {peak:.2f} vs threshold {limit:.2f} — the model barely heard "
                f"'{phrase}'. Record 3 more takes ('record the wake word') and retrain.")
    result.update(ok=True, peak=round(peak, 3), triggered=triggered, hint=hint,
                  message=f"Live wake-word test on {frames} frames. {hint}")
    _announce(result["message"], player)
    return result


def live_test(seconds: float = 8.0, threshold=None, phrase: str = None,
              player=None, sd=None) -> str:
    """Listen for ``seconds`` seconds and report peak score vs threshold."""
    if not _claim("testing", mic=True):
        return busy_message("testing")
    try:
        return _live_test(seconds=seconds, threshold=threshold, phrase=phrase,
                          player=player, sd=sd)["message"]
    finally:
        _release()


# ── guided setup / reset ───────────────────────────────────────────────────

def _run_setup(phrase, positives, negatives, seconds, player, sd, steps,
               activate, engine, max_retries) -> str:
    """Internal one-step record → train → activate (caller owns the lock)."""
    engine = engine if engine is not None else _matcher
    _ = activate  # the guided flow always (re)loads and starts the listener
    phrase = normalise_phrase(phrase, current_phrase())

    recorded = _record_guided(positives, negatives, phrase, seconds, player, sd,
                              max_retries, resume_listener=False, engine=engine)
    if not recorded["positives_saved"]:
        return recorded["message"]

    print("[FridayWake] Training the classifier from the new clips…")
    ok, train_message = _train_model(steps=steps, phrase=phrase)
    parts = [recorded["message"], train_message]
    if not ok:
        parts.append("The listener was not restarted because training did not finish.")
        return "\n".join(parts)

    try:
        if hasattr(engine, "reload"):
            activation = engine.reload(start=True)
        else:
            activation = engine.start()
    except Exception as exc:
        activation = f"Wake word could not be activated automatically: {exc}"
    parts.append(activation)
    return "\n".join(parts)


def run_setup(phrase: str = None, positives: int = 5, negatives: int = 3,
              seconds: float = RECORD_SECONDS, player=None, sd=None,
              steps: int = 900, activate: bool = True, engine=None,
              max_retries: int = MAX_POSITIVE_RETRIES) -> str:
    """One-step guided setup: record → validate → train → activate."""
    return run_setup_result(phrase=phrase, positives=positives,
                            negatives=negatives, seconds=seconds, player=player,
                            sd=sd, steps=steps, activate=activate, engine=engine,
                            max_retries=max_retries)["message"]


def run_setup_result(phrase: str = None, positives: int = 5, negatives: int = 3,
                     seconds: float = RECORD_SECONDS, player=None, sd=None,
                     steps: int = 900, activate: bool = True, engine=None,
                     max_retries: int = MAX_POSITIVE_RETRIES) -> dict:
    """``run_setup`` that also returns the recorded clip inventory."""
    if not _claim("setup", mic=True):
        return {"ok": False, "busy": True, "message": busy_message("recording"),
                "clips": [], "positives_saved": 0, "negatives_saved": 0}
    try:
        try:
            steps = max(50, min(int(steps), 5000))
        except (TypeError, ValueError):
            steps = 900
        message = _run_setup(phrase, positives, negatives, seconds, player, sd,
                             steps, activate, engine, max_retries)
        return {"ok": MODEL_PATH.exists(), "busy": False, "message": message,
                "clips": [],
                "positives_saved": len(_save_clip_list(TRAIN_DIR / "positive")),
                "negatives_saved": len(_save_clip_list(TRAIN_DIR / "negative"))}
    finally:
        _release()


def reset(root: Path = None, export: Path = None, delete_model: bool = True,
          keep_model: bool = None, engine=None) -> str:
    """Clear recorded clips (and optionally the stale trained model)."""
    busy, name = activity_in_progress()
    if busy:
        return f"Wake-word {name or 'recording'} is still running, sir — wait for it to finish."
    if keep_model is not None:
        delete_model = not bool(keep_model)
    root = Path(root) if root is not None else TRAIN_DIR
    export = Path(export) if export is not None else MODEL_PATH
    engine = engine if engine is not None else _matcher

    parts = [f"Removed {_clear_clips(root)} wake-word training clip(s)."]
    if delete_model and Path(export).exists():
        try:
            Path(export).unlink()
            parts.append(f"Deleted the stale trained model {Path(export).name}.")
        except OSError as exc:
            parts.append(f"Could not delete {Path(export).name}: {exc}")

    service = getattr(engine, "_svc", None)
    if service is not None:
        try:
            service.stop()
            service._model = None
            service._model_path = None
        except Exception:
            pass
    parts.append("Say 'open wake word setup' to record and train a fresh wake word.")
    return " ".join(parts)


# ── engine wrapper ──────────────────────────────────────────────────────────

class _FridayEngine:
    """Drop-in engine mirroring WakeWordService, plus dataset management."""

    def __init__(self):
        self._svc = None  # wake_word is imported only when the engine is used

    def _get_service(self):
        if self._svc is None:
            from actions.wake_word import WakeWordService
            self._svc = WakeWordService(model_file=MODEL_FILE,
                                        phrase=current_phrase())
        return self._svc

    @property
    def available(self) -> bool:
        return MODEL_PATH.exists()

    @property
    def phrase(self) -> str:
        return current_phrase()

    @property
    def _running(self) -> bool:
        return bool(self._svc and self._svc._running)

    @property
    def _on_detect(self):
        return self._svc._on_detect if self._svc else None

    def configure(self, on_detect=None):
        self._get_service().configure(on_detect)

    def start(self) -> str:
        if not self.available:
            return ("Friday wake needs a trained model first, sir. Say 'open wake word "
                    "setup' to record, train and activate it in one step (or run "
                    "`python -m actions.wake_word setup`).")
        return self._get_service().start()

    def stop(self) -> str:
        return self._get_service().stop() if self._svc else "Wake word engine is not running."

    def reload(self, start: bool = True) -> str:
        """Drop the in-memory classifier and load the freshly trained model."""
        if not self.available:
            return ("The wake-word model is not on disk, so the listener stays off, sir. "
                    "Run 'open wake word setup' again.")
        service = self._get_service()
        was_running = bool(getattr(service, "_running", False))
        stop = getattr(service, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass
        for attr in ("_model", "_model_path"):
            try:
                setattr(service, attr, None)
            except Exception:
                pass
        try:
            service._phrase = self.phrase
        except Exception:
            pass
        if start or was_running:
            start_fn = getattr(service, "start", None)
            if callable(start_fn):
                try:
                    return start_fn()
                except Exception as exc:
                    return f"Wake-word model reloaded but the listener failed to start: {exc}"
            return "Wake-word model reloaded, sir."
        load = getattr(service, "load", None)
        if callable(load):
            try:
                if load():
                    return ("Wake-word model reloaded, sir. Say 'start the wake word' "
                            "to listen.")
            except Exception:
                pass
        return (f"Wake-word model failed to load: "
                f"{getattr(service, '_last_error', '') or 'unknown error'}")

    def record(self, n_clips: int = 5, negatives: int = 3, phrase: str = None,
               seconds: float = RECORD_SECONDS, player=None, auto_train: bool = False,
               **kwargs) -> str:
        if auto_train:
            return self.setup(phrase=phrase, positives=n_clips, negatives=negatives,
                              seconds=seconds, player=player)
        result = record_dataset(n_pos=int(n_clips), n_neg=int(negatives),
                                seconds=seconds, phrase=phrase, player=player,
                                engine=self)
        return result["message"]

    def setup(self, phrase: str = None, positives: int = 5, negatives: int = 3,
              seconds: float = RECORD_SECONDS, player=None, steps: int = 900,
              activate: bool = True, **kwargs) -> str:
        return run_setup(phrase=phrase, positives=positives, negatives=negatives,
                         seconds=seconds, player=player, steps=steps,
                         activate=activate, engine=self)

    def train(self, steps: int = 900, **kwargs) -> str:
        return train_model(steps=steps, phrase=self.phrase)

    def live_test(self, seconds: float = 8.0, threshold=None, phrase: str = None,
                  player=None, **kwargs) -> str:
        return live_test(seconds=seconds, threshold=threshold,
                         phrase=phrase or self.phrase, player=player)

    def list_clips(self) -> str:
        return list_clips()

    def reset(self, delete_model: bool = True, keep_model: bool = None) -> str:
        return reset(delete_model=delete_model, keep_model=keep_model, engine=self)

    def stats(self) -> dict:
        """Engine status in the same shape as ``WakeWordService.stats()``.

        Works before the underlying service exists (nothing trained/started
        yet), which is exactly when `status` is most useful.
        """
        phrase = self.phrase
        model_path = str(MODEL_PATH) if MODEL_PATH.exists() else None
        detail = {
            "state": "inactive",
            "phrase": phrase,
            "model_path": model_path,
            "threshold": DETECT_THRESHOLD_FALLBACK,
            "patience": None,
            "cooldown": None,
            "detections": 0,
            "last_score": 0.0,
            "last_error": "",
            "backend": "none",
            "available": self.available,
        }
        if self._svc is not None:
            try:
                detail.update(self._svc.stats())
            except Exception as exc:
                detail["last_error"] = str(exc)
        return detail

    def dataset_info(self) -> str:
        return dataset_info()

    def clear_dataset(self) -> str:
        return clear_dataset()

    def evaluate(self) -> str:
        return evaluate()


_matcher = _FridayEngine()
