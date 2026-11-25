"""Friday — locally trained custom wake word ("Hey Friday").

Training is fully local and offline. Audio feature extraction uses the same
ONNX front-end as the runtime wake-word engine, so train and inference share
one feature space. All heavy libraries are imported lazily: this module must
import cleanly on a machine without torch / sounddevice / onnxruntime.
"""

import time
import wave
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"
TRAIN_DIR = MODELS_DIR / "training"
MODEL_FILE = "hey_friday_v0.1.onnx"
MODEL_PATH = MODELS_DIR / MODEL_FILE

RATE = 16000
CHANNEL = 1
MIN_PEAK = 0.01  # warn about near-silent clips rather than training on them

_mel = None
_emb = None
_features = None


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


def record_dataset(n_pos: int = 20, n_neg: int = 8, seconds: float = 2.0,
                   prompt: str = "hey friday") -> str:
    """Record positive phrase takes and negative background clips."""
    if not _load_models():
        return "Feature models missing in models/, sir."
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        return f"Recording needs sounddevice/numpy: {exc}"
    n_pos = max(2, min(int(n_pos), 30))
    n_neg = max(2, min(int(n_neg), 15))
    pos_dir, neg_dir = TRAIN_DIR / "positive", TRAIN_DIR / "negative"
    pos_dir.mkdir(parents=True, exist_ok=True)
    neg_dir.mkdir(parents=True, exist_ok=True)

    def record_into(folder: Path, tag: str, line: str) -> Path:
        path = folder / f"{tag}_{int(time.time() * 1000)}.wav"
        print(f"\n[FridayWake] 🎙 {line}")
        time.sleep(1.0)
        audio = sd.rec(int(seconds * RATE), samplerate=RATE, channels=CHANNEL,
                       dtype="int16")
        sd.wait()
        data = np.asarray(audio).reshape(-1)
        peak = float(np.max(np.abs(data))) / 32768.0 if len(data) else 0.0
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(RATE)
            handle.writeframes(data.astype(np.int16).tobytes())
        print(f"[FridayWake] ✓ {path.name} (peak {peak:.2f})")
        return path

    try:
        for i in range(n_pos):
            record_into(pos_dir, f"pos_{i:02d}", f"Say '{prompt}' ({i + 1}/{n_pos})…")
        for i in range(n_neg):
            record_into(neg_dir, f"neg_{i:02d}",
                        "Stay quiet / make background noise (not the phrase)…")
    except Exception as exc:
        return f"Recording failed: {exc}"
    return (f"Recorded {n_pos} positive + {n_neg} negative clips to {TRAIN_DIR}. "
            "Now say 'train the wake word'.")


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


def _augment(windows, rng):
    """Noise + temporal-shift augmentation for positive training windows."""
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


def clear_dataset(root: Path = None) -> str:
    """Delete recorded training clips (never touches the exported model)."""
    root = root or TRAIN_DIR
    removed = 0
    for folder in (root / "positive", root / "negative"):
        if folder.is_dir():
            for path in folder.glob("*.wav"):
                try:
                    path.unlink()
                    removed += 1
                except Exception:
                    pass
    return f"Removed {removed} wake-word training clip(s)."


def train_model(steps: int = 900, root: Path = None, export: Path = None) -> str:
    """Train on a clip-level train/validation split and export the best epoch."""
    import numpy as np
    root, export = root or TRAIN_DIR, export or MODEL_PATH
    pos_clips = _load_clips(root / "positive")
    neg_clips = _load_clips(root / "negative")
    if not pos_clips or not neg_clips:
        return ("Need positive & negative recordings first, sir. "
                "Record them with 'record the wake word'.")

    warnings = []
    for _, path in pos_clips + neg_clips:
        stats = _clip_stats(path)
        if stats["peak"] < MIN_PEAK:
            warnings.append(f"{path.name} is nearly silent (peak {stats['peak']:.3f})")
    if warnings:
        print("[FridayWake] Quality warnings: " + "; ".join(warnings[:5]))

    try:
        import torch
        import torch.nn as nn
    except ImportError:
        return "torch is not installed. Run: pip install torch"

    # Clip-level split: no windows from one recording appear in both sets.
    split = max(1, int(0.2 * min(len(pos_clips), len(neg_clips))))
    val_pos, train_pos = pos_clips[:split], pos_clips[split:]
    val_neg, train_neg = neg_clips[:split], neg_clips[split:]
    if not train_pos or not train_neg:
        return "Record at least 3 clips per class before training, sir."

    rng = np.random.default_rng(0)
    pos_windows = np.concatenate([_windows(clip) for clip, _ in train_pos])
    neg_windows = np.concatenate([_windows(clip) for clip, _ in train_neg])
    val_pos_windows = np.concatenate([_windows(clip) for clip, _ in val_pos])
    val_neg_windows = np.concatenate([_windows(clip) for clip, _ in val_neg])
    if len(pos_windows) == 0 or len(val_pos_windows) == 0:
        return "Clips are too short for feature extraction; record longer takes."

    if len(neg_windows) < 2 * len(pos_windows):
        reps = int(np.ceil((2 * len(pos_windows)) / max(len(neg_windows), 1)))
        neg_windows = np.concatenate([neg_windows] * reps)
    pos_windows = _augment(pos_windows, rng)

    X = np.concatenate([pos_windows, neg_windows])
    y = np.concatenate([np.ones(len(pos_windows)), np.zeros(len(neg_windows))])
    Xv = np.concatenate([val_pos_windows, val_neg_windows])
    yv = np.concatenate([np.ones(len(val_pos_windows)), np.zeros(len(val_neg_windows))])

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

    for step in range(1, max(1, int(steps)) + 1):
        index = torch.randperm(len(xt))[:64]
        optimizer.zero_grad()
        loss = loss_fn(model(xt[index]).squeeze(1), yt[index])
        loss.backward()
        optimizer.step()
        if step % 50 == 0 or step == 1:
            with torch.no_grad():
                predictions = (torch.sigmoid(model(xv)) > 0.5).float().squeeze(1)
                accuracy = (predictions == yvv).float().mean().item()
            print(f"[FridayWake] step {step}/{steps} loss {loss.item():.4f} val_acc {accuracy:.2f}")
            if accuracy > best_acc:
                best_acc, best_step = accuracy, step
                best_state = {k: v.clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    export_model = nn.Sequential(model, nn.Sigmoid())
    export_model.eval()
    export = Path(export)
    export.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        export_model, torch.randn(1, 16, 96), str(export), opset_version=13,
        input_names=["features"], output_names=[export.stem],
        dynamic_axes={"features": {0: "batch"}},
    )
    return (f"Model exported to {export.name} (best val acc {best_acc:.2f} "
            f"@ step {best_step}, clip-level split). "
            "Say 'activate wake word' to use it.")


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


class _FridayEngine:
    """Drop-in engine mirroring WakeWordService, plus dataset management."""

    def __init__(self):
        self._svc = None  # wake_word is imported only when the engine is used

    def _get_service(self):
        if self._svc is None:
            from actions.wake_word import WakeWordService
            self._svc = WakeWordService(model_file=MODEL_FILE, phrase="Hey Friday")
        return self._svc

    @property
    def available(self) -> bool:
        return MODEL_PATH.exists()

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
            return ("Friday wake needs a trained model first, sir. "
                    "Say: 'record the wake word', then 'train the wake word'.")
        return self._get_service().start()

    def stop(self) -> str:
        return self._get_service().stop() if self._svc else "Wake word engine is not running."

    def record(self, n_clips: int = 5, **kwargs) -> str:
        return record_dataset(n_pos=int(n_clips),
                              n_neg=max(2, int(n_clips) // 3))

    def train(self, **kwargs) -> str:
        return train_model(**kwargs)

    def dataset_info(self) -> str:
        return dataset_info()

    def clear_dataset(self) -> str:
        return clear_dataset()

    def evaluate(self) -> str:
        return evaluate()


_matcher = _FridayEngine()
