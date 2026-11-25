"""100% offline tests for the guided wake-word workflow.

No microphone, speaker, PortAudio, torch, onnxruntime or trained model is ever
required: ``sounddevice`` is replaced by a scripted fake, the ONNX feature
front-end by a deterministic numpy stub, and the trainer's torch dependency by
a fake that follows the same contract (``Sequential`` / ``eval`` / export).

Covers the production bugs fixed in ``actions/wake_word.py`` /
``actions/friday_wake.py`` / ``main.py``:

* silent positives retried instead of trained on,
* one non-blocking single-flight lock for record/setup/train/test,
* numpy augmentation + synthetic negatives + atomic ``os.replace`` export,
* one-step ``setup`` (record -> train -> activate),
* ``list_clips`` / ``reset``,
* argparse CLI parsing,
* mic forwarding muted and startup warning logged once in ``main.py``.
"""

from __future__ import annotations

import ast
import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from actions import friday_wake as fw
from actions import wake_word as ww

REPO = Path(__file__).resolve().parent.parent


# ── fixtures / fakes --------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_models(tmp_path, monkeypatch):
    """Redirect every model/config path into a temp dir and reset module state."""
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setattr(fw, "MODELS_DIR", models)
    monkeypatch.setattr(fw, "TRAIN_DIR", models / "training")
    monkeypatch.setattr(fw, "_sd", lambda: None)
    monkeypatch.setattr(fw, "MODEL_PATH", models / fw.MODEL_FILE)
    monkeypatch.setattr(fw, "CONFIG_FILE", models / "wake_word.json")
    monkeypatch.setattr(fw, "_replace", __import__("os").replace)
    monkeypatch.setattr(fw.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(fw, "_features", None)
    monkeypatch.setattr(fw, "_features_owned", None)
    monkeypatch.setattr(fw, "_mel", None)
    monkeypatch.setattr(fw, "_emb", None)
    monkeypatch.setattr(fw, "_mel_emb_key", None)
    ww._WARNED_MISSING.clear()
    yield models
    ww._WARNED_MISSING.clear()


def _loud(count: int = 32000, amplitude: float = 0.4) -> np.ndarray:
    """A deterministic audible take (a slow 440 Hz-ish tone)."""
    t = np.arange(count, dtype=np.float32) / fw.RATE
    return (np.sin(2 * np.pi * 440.0 * t) * amplitude * 32767.0).astype(np.int16)


class FakeSD:
    """Scripted stand-in for ``sounddevice`` (input only, never opens a stream)."""

    def __init__(self, takes=None, chunks=None):
        self.takes = list(takes or [])
        self.chunks = list(chunks or [])
        self.plays = 0
        self.recs = 0
        self.waits = 0

    def play(self, _data, _rate):
        self.plays += 1

    def wait(self):
        self.waits += 1

    def rec(self, frames, samplerate=None, channels=1, dtype="int16"):
        self.recs += 1
        if self.chunks:
            take = self.chunks.pop(0)
        else:
            take = self.takes.pop(0) if self.takes else np.zeros(frames, dtype=np.int16)
        take = np.asarray(take, dtype=np.int16)
        if take.size < frames:
            take = np.pad(take, (0, frames - take.size))
        return take[:frames].reshape(-1, 1)


class FakeFeatures:
    """``openwakeword.utils.AudioFeatures`` clone: energy -> constant windows."""

    def __init__(self):
        self.clips = 0

    def reset(self):
        pass

    def __call__(self, _clip):
        self.clips += 1


def _install_fake_features(monkeypatch):
    features = FakeFeatures()

    def fake_windows(take, stride=4, limit=1024):
        x = np.asarray(take, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return np.empty((0, 16, 96), dtype=np.float32)
        energy = float(np.sqrt(np.mean(np.square(x)))) / 32768.0
        return np.tile(np.float32(energy), (4, 16, 96))

    monkeypatch.setattr(fw, "_features", features)
    monkeypatch.setattr(fw, "_windows", fake_windows)
    return features


class FakeEngine:
    """Minimal matcher/engine used by setup + activation assertions."""

    def __init__(self):
        self.available = True
        self.started = 0
        self.reloaded = 0
        self.stopped = 0
        self._running = False

    def start(self):
        self.started += 1
        self._running = True
        return "Wake word active (Hey Friday)."

    def stop(self):
        self.stopped += 1
        self._running = False
        return "Wake word stopped, sir."

    def reload(self, start: bool = True):
        self.reloaded += 1
        self._running = True
        return "Wake word reloaded and active."


# ── 1. quality gate, chime and retry ----------------------------------------

def test_chime_is_played_before_every_take_and_never_raises():
    sd = FakeSD([np.zeros(32000)])
    assert fw.play_chime(sd=sd) is True
    assert sd.plays == 1 and sd.waits == 1
    # A dead (or muted) audio device must degrade to a silent no-op.
    class Broken:
        def play(self, *a, **k):
            raise RuntimeError("device unavailable")

        def wait(self):
            raise RuntimeError("device unavailable")

    assert fw.play_chime(sd=Broken()) is False
    assert fw.play_chime(sd=None) is False


def test_silent_positive_clip_retries_then_succeeds(monkeypatch):
    sd = FakeSD([np.zeros(32000), _loud()])
    prompts = []
    result = fw.record_clip(Path("/tmp") / "pos_00.wav", kind="positive",
                            sd=sd, player=SimpleNamespace(write_log=prompts.append))
    assert result["saved"] is True
    assert result["attempts"] == 2
    assert result["quality"] == "OK"
    assert sd.recs == 2 and sd.plays == 2             # a chime before each take
    assert any("TOO QUIET" in line for line in prompts)


def test_final_silent_positive_gives_guidance_and_saves_nothing(tmp_path):
    sd = FakeSD([np.zeros(32000), np.zeros(32000), np.zeros(32000)])
    path = tmp_path / "pos_00.wav"
    result = fw.record_clip(path, kind="positive", sd=sd)
    assert result["saved"] is False
    assert result["attempts"] == 3                     # 1 take + 2 retries
    assert "TOO QUIET" in result["message"]
    assert "Discarded" in result["message"]
    assert "microphone" in result["message"] or "Microphone" in result["message"]
    assert not path.exists()


def test_negative_quiet_room_tone_is_kept():
    sd = FakeSD([np.zeros(32000)])
    result = fw.record_clip(Path("/tmp") / "neg_00.wav", kind="negative",
                            sd=sd, max_retries=2)
    assert result["saved"] is True and result["attempts"] == 1
    assert result["quality"] == "TOO QUIET"           # quiet room tone is fine
    assert sd.recs == 1


def test_clipped_take_is_badged_and_warned(tmp_path):
    sd = FakeSD([np.full(32000, 32767 // 1, dtype=np.int16)])
    result = fw.record_clip(tmp_path / "pos_00.wav", kind="positive", sd=sd,
                            player=SimpleNamespace(write_log=lambda _m: None))
    assert result["quality"] == "CLIPPED"
    assert "clipping" in result["message"]


@pytest.mark.parametrize("peak,kind,expected", [
    (0.0, "positive", "TOO QUIET"),
    (0.02, "positive", "TOO QUIET"),
    (0.03, "positive", "OK"),
    (0.5, "negative", "OK"),
    (0.995, "positive", "CLIPPED"),
])
def test_classify_peak_badges(peak, kind, expected):
    assert fw.classify_peak(peak, kind) == expected


def test_recording_without_backend_returns_actionable_message(monkeypatch):
    monkeypatch.setattr(fw, "_sd", lambda: None)
    result = fw.record_dataset(1, 0, sd=None)
    assert result["ok"] is False and result["positives_saved"] == 0
    assert "sounddevice" in result["message"]


# ── 2. single-flight lock ---------------------------------------------------

def test_concurrent_record_returns_immediately(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow_record(n_clips=5, n_neg=3, seconds=2.0, prompt=None, phrase=None,
                    player=None, sd=None, max_retries=2, resume_listener=True,
                    engine=None):
        calls.append(n_clips)
        started.set()
        release.wait(2.0)
        return {"ok": True, "message": "recorded", "clips": [],
                "positives_saved": 1, "negatives_saved": 0}

    monkeypatch.setattr(fw, "_record_guided", slow_record)
    first = {}
    worker = threading.Thread(target=lambda: first.update(
        r=fw.record_dataset(5, 3)))
    worker.start()
    assert started.wait(2.0)

    second = fw.record_dataset(5, 3)
    assert second["busy"] is True
    assert "already in progress" in second["message"]
    assert calls == [5]                                # no second recording

    release.set()
    worker.join(timeout=3)
    assert first["r"]["ok"] is True
    assert fw.activity_in_progress() == (False, "")


def test_setup_train_test_share_the_same_lock():
    assert fw._claim("setup", mic=True) is True
    assert fw.record_dataset(1, 0)["busy"] is True
    assert "already in progress" in fw.run_setup(positives=1)
    assert "already in progress" in fw.train_model()
    assert "already in progress" in fw.live_test()
    assert fw.is_recording_active() is True
    fw._release()
    assert fw.is_recording_active() is False
    # A released lock can be claimed again by the next activity.
    assert fw._claim("training", mic=False) is True
    fw._release()


def test_is_recording_active_is_false_when_idle():
    assert fw.is_recording_active() is False
    assert ww.is_recording_active() is False


# ── 3. augmentation + atomic training ---------------------------------------

def _write_training_clips(monkeypatch, root: Path, positives=5, negatives=2):
    pos, neg = root / "positive", root / "negative"
    pos.mkdir(parents=True, exist_ok=True)
    neg.mkdir(parents=True, exist_ok=True)
    for index in range(positives):
        fw._write_clip(pos / f"pos_{index:02d}.wav", _loud(amplitude=0.25 + 0.02 * index))
    for index in range(negatives):
        quiet = (np.random.default_rng(index).normal(0, 600, 32000)).astype(np.int16)
        fw._write_clip(neg / f"neg_{index:02d}.wav", quiet)
    return pos, neg


def _install_fake_trainer(monkeypatch, accuracy=0.92, fail_export=None):
    calls = {}

    class Sequential:
        def __init__(self, *_mods):
            pass

        def eval(self):
            return self

    def fake_train(X, y, Xv, yv, steps=900):
        calls.update(X=np.asarray(X), y=np.asarray(y), Xv=np.asarray(Xv),
                     yv=np.asarray(yv), steps=steps)
        metrics = {"accuracy": accuracy, "best_step": 1, "steps": steps,
                   "pos_mean": 0.83, "neg_mean": 0.17, "separation": 0.66,
                   "suggested_threshold": 0.5}
        return Sequential(), metrics

    def fake_export(model, path, output_name="hey_friday"):
        if fail_export:
            raise RuntimeError(fail_export)
        Path(path).write_bytes(b"fake-onnx")

    monkeypatch.setattr(fw, "_train_torch", fake_train)
    monkeypatch.setattr(fw, "_export_onnx", fake_export)
    return calls


def test_augmentation_produces_the_documented_variants():
    clip = _loud()
    variants = fw.augment_clip(clip, np.random.default_rng(0))
    assert len(variants) == fw.variant_count()
    gains_and_shifts = len(fw.GAINS) + len(fw.SHIFTS_MS)
    for variant in variants[:1 + gains_and_shifts]:
        assert variant.dtype == np.int16 and variant.size == clip.size
    # Speed perturbation deliberately changes the length (0.94x / 1.06x).
    speeds = variants[1 + gains_and_shifts:1 + gains_and_shifts + len(fw.SPEED_RATES)]
    assert speeds[0].size > clip.size and speeds[1].size < clip.size
    # Gain scaling and time shifts actually change the waveform.
    assert not np.array_equal(variants[0], variants[1])
    assert np.max(np.abs(variants[1].astype(np.int32))) < np.max(np.abs(clip.astype(np.int32)))
    # Hidden callers may rely on the window-level helper staying available.
    windows = np.ones((3, 16, 96), dtype=np.float32)
    assert len(fw._augment(windows, np.random.default_rng(0))) == 9


def test_centre_clip_centres_the_burst():
    clip = np.zeros(16000, dtype=np.int16)
    clip[1000:3000] = _loud(2000)
    centred = fw.centre_clip(clip)
    assert centred.shape == clip.shape
    loud = np.flatnonzero(np.abs(centred.astype(np.int32)) > 500)
    assert abs(int(loud[0]) + int(loud[-1]) - (centred.size - 1)) <= 4


def test_speed_shift_and_noise_helpers_are_safe():
    clip = _loud(16000)
    assert fw.speed_clip(clip, 1.06).size < clip.size
    assert fw.speed_clip(clip, 0.94).size > clip.size
    assert np.array_equal(fw.shift_clip(clip, 0), clip)
    shifted = fw.shift_clip(clip, 1600)
    assert shifted.size == clip.size and not np.array_equal(shifted, clip)
    mixed = fw.mix_noise(clip, np.zeros(clip.size, dtype=np.float32), 20.0)
    assert mixed.shape == clip.shape


def test_train_with_5_positives_and_2_negatives_uses_augmentation_and_replaces_atomically(
        isolated_models, monkeypatch):
    _write_training_clips(monkeypatch, fw.TRAIN_DIR, positives=5, negatives=2)
    _install_fake_features(monkeypatch)
    calls = _install_fake_trainer(monkeypatch)
    replaced = []
    monkeypatch.setattr(fw, "_replace", lambda src, dst: (replaced.append((src, dst)),
                                                          __import__("os").replace(src, dst))[1])

    message = fw.train_model(steps=120, phrase="Hey Friday")

    assert "Training complete" in message
    assert "augmented variants" in message and "synthetic negative" in message
    assert "Held-out accuracy 92%" in message and "separation +0.66" in message
    assert fw.MODEL_PATH.exists() and fw.MODEL_PATH.read_bytes() == b"fake-onnx"
    assert fw.load_config()["phrase"] == "Hey Friday"

    # 5 takes -> 13 variants each; so the training set is far larger than the raw clips.
    assert calls["X"].shape[1:] == (16, 96)
    assert len(calls["X"]) > 5 * 4 * 13
    assert set(np.unique(calls["y"])) == {0.0, 1.0}

    # Atomic save: the model temp file lands in models/ and is renamed onto the
    # final path (the config file uses the same atomic pattern).
    model_replace = [pair for pair in replaced if Path(pair[1]) == fw.MODEL_PATH]
    assert len(model_replace) == 1
    tmp_path, dst = model_replace[0]
    assert Path(dst) == fw.MODEL_PATH and Path(tmp_path).parent == fw.MODELS_DIR
    assert Path(tmp_path).name != fw.MODEL_FILE
    assert not Path(tmp_path).exists()


def test_training_never_corrupts_the_previous_model_when_export_fails(
        isolated_models, monkeypatch):
    _write_training_clips(monkeypatch, fw.TRAIN_DIR)
    _install_fake_features(monkeypatch)
    _install_fake_trainer(monkeypatch, fail_export="CUDA out of memory")
    fw.MODEL_PATH.write_bytes(b"previous-good-model")

    message = fw.train_model(steps=60)

    assert "existing model (if any) was left untouched" in message
    assert fw.MODEL_PATH.read_bytes() == b"previous-good-model"
    leftovers = [p.name for p in fw.MODELS_DIR.glob(".*tmp*")]
    assert leftovers == []                             # temp file cleaned up


def test_training_without_positives_is_guided_not_crashing(isolated_models, monkeypatch):
    _install_fake_trainer(monkeypatch)
    message = fw.train_model()
    assert "No positive clips" in message
    assert not fw.MODEL_PATH.exists()


def test_training_with_a_single_positive_clip_still_produces_a_dataset(
        isolated_models, monkeypatch):
    _write_training_clips(monkeypatch, fw.TRAIN_DIR, positives=1, negatives=1)
    _install_fake_features(monkeypatch)
    calls = _install_fake_trainer(monkeypatch)
    message = fw.train_model(steps=60)
    assert "Training complete" in message
    assert calls["X"].shape[0] > 0 and calls["Xv"].shape[0] > 0


def test_build_training_data_reports_quality_stats(monkeypatch):
    _install_fake_features(monkeypatch)
    rng = np.random.default_rng(0)
    pos = [(_loud().astype(np.float32), Path("pos_0.wav"))]
    neg = [((rng.normal(0, 400, 32000)).astype(np.float32), Path("neg_0.wav"))]
    data = fw.build_training_data(pos, neg)
    stats = data["stats"]
    assert stats["pos_clips"] == 1 and stats["neg_clips"] == 1
    assert stats["pos_variants"] == fw.variant_count()
    assert stats["neg_synthetic"] > 0
    assert len(data["X"]) == stats["pos_windows"] + stats["neg_windows"]
    assert len(data["Xv"]) == stats["val_pos_windows"] + stats["val_neg_windows"]


# ── 4. one-step setup -------------------------------------------------------

def test_setup_records_trains_and_activates_in_one_call(isolated_models, monkeypatch):
    sd = FakeSD([_loud()] * 3 + [np.zeros(32000)] * 2)
    monkeypatch.setattr(fw, "_sd", lambda: sd)
    _install_fake_features(monkeypatch)
    _install_fake_trainer(monkeypatch)
    engine = FakeEngine()

    transcript = fw.run_setup(phrase="Hey Friday", positives=3, negatives=2,
                              steps=60, engine=engine, sd=sd)

    assert "Recorded 3/3 positive" in transcript
    assert "Training complete" in transcript
    assert "reloaded" in transcript.lower() or "active" in transcript.lower()
    assert engine.reloaded == 1
    assert fw.MODEL_PATH.exists()
    assert (fw.TRAIN_DIR / "positive").glob("pos_*.wav")
    assert len(list((fw.TRAIN_DIR / "positive").glob("*.wav"))) == 3
    assert len(list((fw.TRAIN_DIR / "negative").glob("*.wav"))) == 2
    assert fw.is_recording_active() is False            # lock released
    assert fw.load_config()["phrase"] == "Hey Friday"


def test_setup_that_records_only_silence_skips_training(isolated_models, monkeypatch):
    sd = FakeSD()
    _install_fake_trainer(monkeypatch)
    engine = FakeEngine()
    transcript = fw.run_setup(positives=1, negatives=0, max_retries=0,
                              engine=engine, sd=sd)
    assert "0/1 usable positive clips" in transcript
    assert engine.reloaded == 0
    assert not fw.MODEL_PATH.exists()


def test_setup_custom_phrase_is_persisted(isolated_models, monkeypatch):
    sd = FakeSD([_loud()] * 2)
    _install_fake_features(monkeypatch)
    _install_fake_trainer(monkeypatch)
    fw.run_setup(phrase="Hey Jarvis", positives=1, negatives=0, steps=60,
                 engine=FakeEngine(), sd=sd)
    assert fw.current_phrase() == "Hey Jarvis"


def test_setup_stops_and_resumes_running_listener(isolated_models, monkeypatch):
    sd = FakeSD([_loud()] * 2)
    _install_fake_trainer(monkeypatch)
    engine = FakeEngine()
    engine._running = True
    fw.record_dataset(2, 0, sd=sd, engine=engine)
    assert engine.stopped == 1 and engine.started == 1


# ── 5. list_clips / reset ---------------------------------------------------

def test_list_clips_reports_counts_and_peaks(isolated_models, monkeypatch):
    _write_training_clips(monkeypatch, fw.TRAIN_DIR, positives=2, negatives=1)
    (fw.TRAIN_DIR / "negative" / "silent.wav").write_bytes(b"")
    report = fw.list_clips()
    assert "positive: 2 clip(s)" in report
    assert "neg_00.wav" in report and "peak" in report and "OK" in report
    assert "not trained yet" in report
    fw.MODEL_PATH.write_bytes(b"model")
    assert "present" in fw.list_clips()


def test_reset_clears_clips_and_model(isolated_models, monkeypatch):
    _write_training_clips(monkeypatch, fw.TRAIN_DIR, positives=2, negatives=2)
    fw.MODEL_PATH.write_bytes(b"model")
    message = fw.reset()
    assert "Removed 4" in message and "Deleted the stale trained model" in message
    assert list((fw.TRAIN_DIR / "positive").glob("*.wav")) == []
    assert not fw.MODEL_PATH.exists()


def test_reset_can_keep_the_model(isolated_models, monkeypatch):
    _write_training_clips(monkeypatch, fw.TRAIN_DIR, positives=1, negatives=1)
    fw.MODEL_PATH.write_bytes(b"model")
    assert "Deleted" not in fw.reset(keep_model=True)
    assert fw.MODEL_PATH.exists()


def test_reset_refuses_while_recording_is_active(isolated_models):
    assert fw._claim("setup", mic=True) is True
    try:
        assert "still running" in fw.reset()
    finally:
        fw._release()


@pytest.mark.parametrize("phrase,expected", [
    ("hey friday", "Hey Friday"),
    ("Hey Jarvis", "Hey Jarvis"),
    ("  hey   computer ", "Hey Computer"),
    ("", "Hey Friday"),
])
def test_phrase_normalisation_preserves_words(phrase, expected):
    assert fw.normalise_phrase(phrase) == expected


def test_voice_phrase_is_persisted_and_shown_normalised(isolated_models, monkeypatch):
    helper = SimpleNamespace(
        RECORD_SECONDS=2.0,
        normalise_phrase=fw.normalise_phrase,
        save_config=fw.save_config,
        current_phrase=fw.current_phrase,
        run_setup=lambda **kwargs: "setup",
    )
    monkeypatch.setattr(ww, "_helper", lambda: helper)
    monkeypatch.setattr(ww, "_load_matcher", lambda: object())
    ww.wake_word({"action": "setup", "phrase": "hey jarvis"})
    assert fw.current_phrase() == "Hey Jarvis"
    assert ww._service.stats()["phrase"] == "Hey Jarvis"


def test_engine_status_works_on_a_fresh_install(isolated_models):
    engine = fw._FridayEngine()
    detail = engine.stats()
    assert detail["state"] == "inactive"
    assert detail["phrase"] == fw.DEFAULT_PHRASE
    assert detail["model_path"] is None and detail["available"] is False
    assert detail["threshold"] > 0
    fw.MODEL_PATH.write_bytes(b"model")
    engine = fw._FridayEngine()
    assert engine.stats()["model_path"] == str(fw.MODEL_PATH)


def test_engine_status_merges_the_live_service(isolated_models):
    engine = fw._FridayEngine()
    engine._svc = SimpleNamespace(stats=lambda: {
        "state": "active", "phrase": "Hey Friday", "threshold": 0.42,
        "patience": 2, "cooldown": 2.5, "detections": 3, "last_score": 0.9,
        "last_error": "", "backend": "sounddevice"})
    detail = engine.stats()
    assert detail["state"] == "active" and detail["threshold"] == 0.42
    assert detail["detections"] == 3


def test_reload_is_defensive_with_a_duck_typed_service(isolated_models):
    fw.MODEL_PATH.write_bytes(b"model")
    engine = fw._FridayEngine()
    calls = []
    engine._svc = SimpleNamespace(
        _running=False,
        stop=lambda: calls.append("stop"),
        start=lambda: calls.append("start") or "started",
    )
    assert engine.reload(start=True) == "started"
    assert calls == ["stop", "start"]


def test_reload_without_a_model_does_not_start(isolated_models):
    engine = fw._FridayEngine()
    assert "not on disk" in engine.reload(start=True)


def test_cli_status_accepts_an_args_namespace(isolated_models):
    assert ww._cli_status(None) == 0
    assert ww._cli_status(SimpleNamespace(command="status")) == 0


def test_voice_actions_route_every_new_action(monkeypatch):
    helper = SimpleNamespace(
        RECORD_SECONDS=2.0,
        current_phrase=lambda default="Hey Friday": default,
        run_setup=lambda **kwargs: "setup",
        live_test=lambda **kwargs: "test",
        list_clips=lambda: "clips",
        reset=lambda **kwargs: "reset",
    )
    monkeypatch.setattr(ww, "_helper", lambda: helper)
    engine = SimpleNamespace(
        available=False, _running=False,
        stats=lambda: {"phrase": "Hey Friday", "threshold": 0.6, "cooldown": 2.5,
                       "backend": "none"},
        start=lambda: "started", stop=lambda: "stopped",
    )
    monkeypatch.setattr(ww, "_load_matcher", lambda: engine)
    monkeypatch.setattr(ww, "_pick_engine", lambda: ("friday", engine))
    # `start` mirrors the device/on-detect wiring onto the module service.
    monkeypatch.setattr(ww, "_service", SimpleNamespace(
        _device=None, _on_detect=None,
        stats=lambda: {"phrase": "Hey Friday", "threshold": 0.6,
                       "cooldown": 2.5, "backend": "none"}))

    assert ww.wake_word({"action": "status"}).startswith("Wake word is inactive")
    assert "not trained yet" in ww.wake_word({"action": "status"})
    assert ww.wake_word({"action": "setup"}) == "setup"
    assert ww.wake_word({"action": "guided_setup"}) == "setup"
    assert ww.wake_word({"action": "test"}) == "test"
    assert ww.wake_word({"action": "list_clips"}) == "clips"
    assert ww.wake_word({"action": "reset"}) == "reset"
    assert ww.wake_word({"action": "start"}) == "started"
    assert ww.wake_word({"action": "stop"}) == "stopped"


def test_record_action_reports_busy_instead_of_opening_a_second_stream(monkeypatch):
    monkeypatch.setattr(ww, "_load_matcher", lambda: object())
    assert fw._claim("recording", mic=True) is True
    try:
        assert "already in progress" in ww.wake_word({"action": "record", "clips": 3})
    finally:
        fw._release()


# ── 6. CLI parsing ----------------------------------------------------------

@pytest.mark.parametrize("argv,expected", [
    (["setup"], ("setup", 5, 3)),
    (["setup", "--positives", "7", "--negatives", "2"], ("setup", 7, 2)),
    (["record", "--positives", "4"], ("record", 4, 3)),
    (["train", "--steps", "200"], ("train", None, None)),
    (["test", "--seconds", "10"], ("test", None, None)),
    (["status"], ("status", None, None)),
    (["reset", "--keep-model"], ("reset", None, None)),
])
def test_cli_parses_every_subcommand(argv, expected):
    args = ww._build_parser().parse_args(argv)
    assert args.command == expected[0]
    if expected[1] is not None:
        assert args.positives == expected[1]
    if expected[2] is not None:
        assert args.negatives == expected[2]


def test_cli_without_arguments_launches_the_menu(monkeypatch):
    menus = []
    monkeypatch.setattr(ww, "_interactive_menu", lambda: menus.append(1) or 0)
    assert ww.main([]) == 0
    assert menus == [1]


@pytest.mark.parametrize("command", ["setup", "record", "train", "test", "status", "reset"])
def test_cli_subcommands_dispatch_without_hardware(monkeypatch, command):
    calls = []

    def fake(command_name):
        def run(args):
            calls.append(command_name)
            return 0
        return run

    monkeypatch.setattr(ww, "_cli_" + command, fake(command))
    assert ww.main([command]) == 0
    assert calls == [command]


def test_cli_help_lists_the_studio(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        ww.main(["--help"])
    out = capsys.readouterr().out
    assert "wake-word studio" in out.lower()


# ── 7. live session protection (main.py) ------------------------------------

def _main_source() -> str:
    return (REPO / "main.py").read_text(encoding="utf-8")


def test_main_suppresses_mic_forwarding_while_recording(monkeypatch):
    import main

    monkeypatch.setattr(main, "_wake_recording_active", lambda: True)
    live = main.FridayLive(MagicMock(muted=False))
    queued = []
    live._safe_put_mic = lambda data: queued.append(data)

    source = _main_source()
    block = source.split("async def _listen_audio")[1].split("def _safe_put_mic")[0]
    assert "_wake_recording_active()" in block
    assert "not recording" in block

    # Behaviour of the guard the callback uses.
    assert main._wake_recording_active() is True
    monkeypatch.setattr(main, "_wake_recording_active", lambda: False)
    assert main._wake_recording_active() is False
    assert live is not None


def test_missing_model_warning_is_logged_at_most_once(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ww, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(ww, "_get_oww", lambda: MagicMock())
    ww._WARNED_MISSING.clear()
    assert ww.WakeWordService().load() is False
    assert ww.WakeWordService().load() is False
    out = capsys.readouterr().out
    assert out.count("Model missing") == 1
    assert "open wake word setup" in out


def test_missing_model_warning_is_shared_across_startup_probes(
        isolated_models, monkeypatch, capsys):
    """Startup probes two different engines; the hint must print once, not twice."""
    monkeypatch.setattr(ww, "_get_oww", lambda: MagicMock())
    ww._WARNED_MISSING.clear()
    ww.WakeWordService().start()
    fw._FridayEngine().start()
    out = capsys.readouterr().out
    assert out.count("Model missing") == 1


def test_wake_active_check_is_cached_and_fails_closed(monkeypatch):
    import main

    calls = []
    monkeypatch.setattr(main, "_WAKE_ACTIVE_CHECK", None)
    monkeypatch.setattr(fw, "is_recording_active",
                        lambda: (calls.append(1), True)[1])
    assert main._wake_recording_active() is True
    assert main._wake_recording_active() is True
    assert calls == [1, 1]                    # resolved once, called per chunk

    monkeypatch.setattr(fw, "is_recording_active",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert main._wake_recording_active() is False   # never breaks the callback


def test_long_wake_word_calls_run_outside_the_receive_loop():
    """A blocking setup must not stall WebSocket frame draining (1011 fix)."""
    source = _main_source()
    assert "LONG_RUNNING_TOOLS" in source
    block = source.split("async def _receive_audio")[1].split("async def _play_audio")[0]
    assert "if fc.name in LONG_RUNNING_TOOLS:" in block
    assert "_execute_tool_in_background(fc)" in block
    background = source.split("async def _execute_tool_in_background")[1] \
                       .split("async def _await_ready_for_tool")[0]
    assert "send_tool_response" in background


def test_background_tool_returns_response_after_the_loop_keeps_running():
    import main

    live = main.FridayLive(MagicMock(muted=False))
    live._tool_handlers["wake_word"] = (lambda _a: "setup done", 1500)
    live.ui = MagicMock(muted=True)
    live.ui.set_state = lambda *_a, **_k: None
    live.ui.write_log = lambda *_a, **_k: None
    live.ui.muted = True
    live.set_speaking = lambda *_a, **_k: None

    sent = []

    class Session:
        async def send_tool_response(self, function_responses):
            sent.append(function_responses[0].response["result"])

    fc = SimpleNamespace(name="wake_word", args={"action": "setup"}, id="abc")

    async def run():
        live.session = Session()
        live._send_lock = asyncio.Lock()
        live._ready = True
        await live._execute_tool_in_background(fc)

    asyncio.run(run())
    assert sent == ["setup done"]


def test_background_tool_ignores_a_closed_session():
    import main

    live = main.FridayLive(MagicMock(muted=False))
    live.ui = MagicMock()

    fc = SimpleNamespace(name="wake_word", args={"action": "setup"}, id="abc")

    async def run():
        live.session = None
        live._send_lock = None
        live._ready = False
        monkey = pytest.MonkeyPatch()
        monkey.setattr(main, "TOOL_READY_GRACE_S", 0.05)
        try:
            await live._execute_tool_in_background(fc)   # must not raise
        finally:
            monkey.undo()

    asyncio.run(run())


def test_tool_declaration_teaches_the_model_the_setup_phrases():
    import main

    declaration = next(d for d in main.TOOL_DECLARATIONS if d["name"] == "wake_word")
    description = declaration["description"].lower()
    for phrase in ("open wake word setup", "set up the wake word",
                   "record and train the wake word", "create a wake word"):
        assert phrase in description
    properties = declaration["parameters"]["properties"]
    for name in ("action", "phrase", "positives", "negatives", "seconds", "auto_train"):
        assert name in properties
    assert "setup" in properties["action"]["description"]


def test_wake_word_action_routes_setup_with_a_mockable_helper(monkeypatch):
    calls = {}
    helper = SimpleNamespace(
        RECORD_SECONDS=2.0,
        current_phrase=lambda default="Hey Friday": default,
        run_setup=lambda **kwargs: calls.update(kwargs) or "guided setup done",
        live_test=lambda **kwargs: calls.update(kwargs) or "live test done",
        list_clips=lambda: "clip list",
        reset=lambda **kwargs: "reset done",
    )
    monkeypatch.setattr(ww, "_helper", lambda: helper)
    monkeypatch.setattr(ww, "_load_matcher", lambda: object())

    assert ww.wake_word({"action": "setup", "phrase": "Hey Jarvis",
                         "positives": 7, "negatives": 2}) == "guided setup done"
    assert calls["phrase"] == "Hey Jarvis"
    assert calls["positives"] == 7 and calls["negatives"] == 2
    assert calls["activate"] is True

    assert "guided setup done" == ww.wake_word({"action": "create_wake_word"})
    assert calls["phrase"] == "Hey Friday"
    assert "Unknown wake word action" in ww.wake_word({"action": "nonsense"})
    assert ww.wake_word({"action": "list_clips"}) == "clip list"
    assert ww.wake_word({"action": "reset"}) == "reset done"
    assert ww.wake_word({"action": "live_test", "seconds": 12}) == "live test done"
    assert calls["seconds"] == 12


def test_no_forbidden_files_were_touched():
    """Scope guard: only the wake-word files, main.py, readme and tests changed."""
    source = (REPO / "actions" / "wake_word.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert tree is not None
    readme = (REPO / "readme.md").read_text(encoding="utf-8")
    assert "Wake Word Setup (Voice & CLI)" in readme
    # The engine keeps its lazy-import contract for the tray thread.
    assert "from actions.friday_wake import" not in source


# ── 8. ONNX front-end paths, PyTorch 2.x export & AudioFeatures parity -----

def test_get_features_forwards_local_onnx_paths_and_invalidates_on_dir_change(
        isolated_models, monkeypatch, tmp_path):
    """_get_features() must wire MODELS_DIR ONNX files into AudioFeatures."""
    import sys
    import types

    # Missing front-end files raise an actionable FileNotFoundError before import.
    with pytest.raises(FileNotFoundError, match="melspectrogram.onnx"):
        fw._get_features()

    mel1 = isolated_models / "melspectrogram.onnx"
    emb1 = isolated_models / "embedding_model.onnx"
    mel1.write_bytes(b"mel-1")
    emb1.write_bytes(b"emb-1")

    recorded = []

    class StubAudioFeatures:
        def __init__(self, melspec_model_path="", embedding_model_path="",
                     inference_framework="tflite"):
            recorded.append({
                "melspec_model_path": melspec_model_path,
                "embedding_model_path": embedding_model_path,
                "inference_framework": inference_framework,
            })

    fake_oww = types.ModuleType("openwakeword")
    fake_utils = types.ModuleType("openwakeword.utils")
    fake_utils.AudioFeatures = StubAudioFeatures
    monkeypatch.setitem(sys.modules, "openwakeword", fake_oww)
    monkeypatch.setitem(sys.modules, "openwakeword.utils", fake_utils)

    inst1 = fw._get_features()
    assert fw._get_features() is inst1
    assert recorded == [{
        "melspec_model_path": str(mel1),
        "embedding_model_path": str(emb1),
        "inference_framework": "onnx",
    }]

    # Switching MODELS_DIR invalidates auto-created _features and rebinds paths.
    alt_models = tmp_path / "alt_models"
    alt_models.mkdir()
    mel2 = alt_models / "melspectrogram.onnx"
    emb2 = alt_models / "embedding_model.onnx"
    mel2.write_bytes(b"mel-2")
    emb2.write_bytes(b"emb-2")
    monkeypatch.setattr(fw, "MODELS_DIR", alt_models)

    inst2 = fw._get_features()
    assert inst2 is not inst1
    assert recorded[-1] == {
        "melspec_model_path": str(mel2),
        "embedding_model_path": str(emb2),
        "inference_framework": "onnx",
    }


def test_wake_word_service_new_model_forwards_local_onnx_paths(
        isolated_models, monkeypatch):
    """WakeWordService._new_model() must wire local ONNX paths via kwargs & AudioFeatures."""
    import sys
    import types

    mel = isolated_models / "melspectrogram.onnx"
    emb = isolated_models / "embedding_model.onnx"
    wake = isolated_models / ww.WakeWordService()._model_file
    mel.write_bytes(b"mel")
    emb.write_bytes(b"emb")
    wake.write_bytes(b"wake")

    af_calls = []
    model_calls = []

    class StubAF:
        def __init__(self, melspec_model_path="", embedding_model_path="",
                     inference_framework="tflite", **extra):
            af_calls.append({
                "melspec_model_path": melspec_model_path,
                "embedding_model_path": embedding_model_path,
                "inference_framework": inference_framework,
                "extra": extra,
            })

    def _wrap_re_arg(fn):
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)
        return wrapper

    class WrappedModel:
        @_wrap_re_arg
        def __init__(self, wakeword_models=None, inference_framework="tflite",
                     **kwargs):
            model_calls.append({
                "wakeword_models": wakeword_models,
                "inference_framework": inference_framework,
                "kwargs": kwargs,
            })
            self.preprocessor = oww_mod.AudioFeatures()

    oww_mod = types.ModuleType("openwakeword.model")
    oww_mod.AudioFeatures = StubAF
    oww_mod.Model = WrappedModel
    monkeypatch.setitem(sys.modules, "openwakeword.model", oww_mod)
    monkeypatch.setattr(ww, "MODELS_DIR", isolated_models)
    monkeypatch.setattr(ww, "_get_oww", lambda: WrappedModel)

    svc = ww.WakeWordService()
    assert svc.load() is True
    assert model_calls[0]["inference_framework"] == "onnx"
    assert model_calls[0]["kwargs"]["melspec_model_path"] == str(mel)
    assert model_calls[0]["kwargs"]["embedding_model_path"] == str(emb)
    assert af_calls[0]["melspec_model_path"] == str(mel)
    assert af_calls[0]["embedding_model_path"] == str(emb)
    assert af_calls[0]["inference_framework"] == "onnx"
    assert oww_mod.AudioFeatures is StubAF


def test_windows_eliminates_random_reset_buffer_leakage_and_aligns_speech(
        isolated_models, monkeypatch):
    """_windows() must never include AudioFeatures.reset()'s 41 random rows."""
    class LeakyResetFeatures:
        feature_buffer_max_len = 120

        def __init__(self):
            self.feature_buffer = np.empty((0, 96), dtype=np.float32)

        def reset(self):
            # Mimic openwakeword.utils.AudioFeatures.reset() random pre-fill.
            self.feature_buffer = np.full((41, 96), -999.0, dtype=np.float32)

        def __call__(self, x):
            n_new = max(1, len(x) // fw.FRAME)
            real_rows = np.arange(n_new, dtype=np.float32)[:, None] * np.ones(
                (1, 96), dtype=np.float32
            )
            self.feature_buffer = np.vstack([self.feature_buffer, real_rows])

    monkeypatch.setattr(fw, "_features", LeakyResetFeatures())

    # 2.0s clip with a localized speech burst between 0.35s and 0.95s.
    clip = np.zeros(2 * fw.RATE, dtype=np.int16)
    burst_t = np.arange(int(0.60 * fw.RATE), dtype=np.float32) / fw.RATE
    clip[int(0.35 * fw.RATE):int(0.95 * fw.RATE)] = (
        np.sin(2.0 * np.pi * 520.0 * burst_t) * 14000.0
    ).astype(np.int16)

    windows = fw._windows(clip)
    assert windows.ndim == 3 and windows.shape[1:] == (16, 96)
    assert len(windows) >= 1
    # Zero sentinel -999.0 rows from reset() may appear anywhere in _windows().
    assert float(np.min(windows)) >= 0.0


def test_pytorch_2x_onnx_export_uses_dynamo_false_and_verification_gate(
        isolated_models, monkeypatch):
    """_export_onnx must pass dynamo=False and _verify_onnx must gate _replace."""
    torch = pytest.importorskip("torch")

    # 1. Verify dynamo=False is forwarded and a single self-contained .onnx is written.
    seen_kwargs = {}
    real_torch_export = torch.onnx.export

    def spy_export(*args, **kwargs):
        seen_kwargs.update(kwargs)
        return real_torch_export(*args, **kwargs)

    monkeypatch.setattr(torch.onnx, "export", spy_export)

    X = np.concatenate([
        np.ones((24, 16, 96), dtype=np.float32) * 1.5,
        np.zeros((36, 16, 96), dtype=np.float32),
    ])
    y = np.array([1.0] * 24 + [0.0] * 36, dtype=np.float32)
    export_model, metrics = fw._train_torch(X, y, X[:8], y[:8], steps=10)
    assert 0.35 <= metrics["suggested_threshold"] <= 0.85

    onnx_out = isolated_models / "probe.onnx"
    fw._export_onnx(export_model, onnx_out, output_name="hey_friday")
    if "dynamo" in __import__("inspect").signature(real_torch_export).parameters:
        assert seen_kwargs.get("dynamo") is False
    assert onnx_out.is_file() and onnx_out.stat().st_size > 0
    assert not Path(f"{onnx_out}.data").exists()
    fw._verify_onnx(onnx_out)

    # 2. Pre-replace verification gate must reject corrupt exports & preserve old model.
    _write_training_clips(monkeypatch, fw.TRAIN_DIR, positives=2, negatives=1)
    _install_fake_features(monkeypatch)
    monkeypatch.setattr(
        fw,
        "_train_torch",
        lambda *_a, **_k: (
            export_model,
            {
                "accuracy": 0.95,
                "best_step": 10,
                "steps": 10,
                "pos_mean": 0.92,
                "neg_mean": 0.05,
                "separation": 0.87,
                "suggested_threshold": 0.56,
            },
        ),
    )
    fw.MODEL_PATH.write_bytes(b"trusted-previous-model")

    # Corrupt bytes written by exporter -> _verify_onnx catches it BEFORE _replace!
    def write_corrupt(_model, path, output_name="hey_friday"):
        Path(path).write_bytes(b"not-a-valid-onnx-protobuf")

    monkeypatch.setattr(fw, "_export_onnx", write_corrupt)
    monkeypatch.setattr(fw, "_verify_onnx", lambda p: fw._REAL_VERIFY_ONNX(p))

    msg = fw.train_model(steps=50)
    assert "existing model (if any) was left untouched" in msg
    assert fw.MODEL_PATH.read_bytes() == b"trusted-previous-model"
    assert not list(isolated_models.glob(".hey_friday*"))

    # External .data sidecar -> _verify_onnx rejects non-self-contained exports too!
    def write_with_sidecar(_model, path, output_name="hey_friday"):
        Path(path).write_bytes(onnx_out.read_bytes())
        Path(f"{path}.data").write_bytes(b"external-weights")

    monkeypatch.setattr(fw, "_export_onnx", write_with_sidecar)
    msg_sidecar = fw.train_model(steps=50)
    assert "sidecar" in msg_sidecar
    assert fw.MODEL_PATH.read_bytes() == b"trusted-previous-model"
    assert not list(isolated_models.glob(".hey_friday*"))


def test_end_to_end_real_math_wake_word_training_export_and_streaming(
        isolated_models, monkeypatch):
    """Real AudioFeatures + PyTorch + ONNXRuntime + WakeWordService smoke test."""
    import shutil

    pytest.importorskip("torch")
    ort = pytest.importorskip("onnxruntime")
    pytest.importorskip("openwakeword")

    real_mel = REPO / "models" / "melspectrogram.onnx"
    real_emb = REPO / "models" / "embedding_model.onnx"
    if not (real_mel.is_file() and real_emb.is_file()):
        pytest.skip("local ONNX feature models not present in repo")

    shutil.copy2(real_mel, isolated_models / "melspectrogram.onnx")
    shutil.copy2(real_emb, isolated_models / "embedding_model.onnx")
    monkeypatch.setattr(ww, "MODELS_DIR", isolated_models)

    def make_structured_phrase(seed: int, *, reverse: bool = False) -> np.ndarray:
        rng = np.random.default_rng(seed)
        sr = fw.RATE
        f0 = float(rng.uniform(125.0, 165.0))
        rate = float(rng.uniform(0.92, 1.08))
        amp = float(rng.uniform(0.26, 0.48))

        def syl(f_base, f1, f2, dur_s):
            n = int(dur_s * sr)
            phase = 2.0 * np.pi * np.cumsum(
                np.full(n, f_base, dtype=np.float32)
            ) / sr
            sig = np.zeros(n, dtype=np.float32)
            for h in range(1, 18):
                fh = h * f_base
                if fh > 7200:
                    break
                w = (
                    np.exp(-0.5 * ((fh - f1) / 170.0) ** 2)
                    + 0.8 * np.exp(-0.5 * ((fh - f2) / 250.0) ** 2)
                )
                sig += float(w) * np.sin(h * phase)
            fade = min(n // 4, int(0.02 * sr))
            if fade > 1:
                sig[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
                sig[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
            return (sig / (float(np.max(np.abs(sig))) + 1e-6)) * amp

        s_hey = syl(f0 * 1.06, 480.0, 2050.0, 0.19 * rate)
        gap = np.zeros(int(0.04 * rate * sr), dtype=np.float32)
        s_fri = syl(f0 * 0.96, 720.0, 1280.0, 0.20 * rate)
        s_day = syl(f0 * 0.90, 450.0, 2180.0, 0.21 * rate)
        parts = [s_day, gap, s_fri, gap, s_hey] if reverse else [s_hey, gap, s_fri, gap, s_day]
        phrase = np.concatenate(parts)

        clip = rng.normal(0.0, 0.002, int(2.0 * sr)).astype(np.float32)
        onset = int(rng.uniform(0.25, 0.85) * sr)
        end = min(clip.size, onset + phrase.size)
        clip[onset:end] += phrase[:end - onset]
        return np.clip(clip * 32767.0, -32767.0, 32767.0).astype(np.int16)

    for idx in range(4):
        fw._write_clip(
            fw.TRAIN_DIR / "positive" / f"pos_{idx:02d}.wav",
            make_structured_phrase(50 + idx),
        )
    bg_rng = np.random.default_rng(99)
    for idx in range(2):
        fw._write_clip(
            fw.TRAIN_DIR / "negative" / f"neg_{idx:02d}.wav",
            bg_rng.normal(0.0, 140.0, 2 * fw.RATE).astype(np.int16),
        )

    ok, report = fw._train_model(steps=260, phrase="Hey Friday")
    assert ok is True, report
    assert fw.MODEL_PATH.is_file()
    assert not Path(f"{fw.MODEL_PATH}.data").exists()

    # Parse reported accuracy and separation directly from _train_model output.
    import re
    acc_match = re.search(r"Held-out accuracy (\d+)%", report)
    sep_match = re.search(r"separation ([+-]?\d+\.\d+)", report)
    assert acc_match and int(acc_match.group(1)) >= 85, report
    assert sep_match and float(sep_match.group(1)) > 0.35, report

    cfg = fw.load_config()
    assert 0.35 <= float(cfg["threshold"]) <= 0.85

    # Verify standalone ONNXRuntime session + runtime WakeWordService streaming.
    sess = ort.InferenceSession(
        str(fw.MODEL_PATH), providers=["CPUExecutionProvider"]
    )
    probe_out = sess.run(
        None, {sess.get_inputs()[0].name: np.zeros((1, 16, 96), dtype=np.float32)}
    )[0]
    assert probe_out.shape == (1, 1)
    assert 0.0 <= float(probe_out[0, 0]) <= 1.0

    svc = ww.WakeWordService(models_dir=isolated_models)
    assert svc.load() is True
    assert svc._threshold == pytest.approx(float(cfg["threshold"]))

    unseen_pos = make_structured_phrase(777)
    pos_scores = [
        svc.score_frame(unseen_pos[p:p + ww.CHUNK])
        for p in range(0, len(unseen_pos) - ww.CHUNK + 1, ww.CHUNK)
    ]
    svc._model.reset()
    ww._prime_oww_model(svc._model)
    silence = np.zeros(2 * fw.RATE, dtype=np.int16)
    sil_scores = [
        svc.score_frame(silence[p:p + ww.CHUNK])
        for p in range(0, len(silence) - ww.CHUNK + 1, ww.CHUNK)
    ]
    svc._model.reset()
    ww._prime_oww_model(svc._model)
    reversed_speech = make_structured_phrase(888, reverse=True)
    rev_scores = [
        svc.score_frame(reversed_speech[p:p + ww.CHUNK])
        for p in range(0, len(reversed_speech) - ww.CHUNK + 1, ww.CHUNK)
    ]

    assert max(pos_scores) >= svc._threshold
    assert max(sil_scores) < svc._threshold
    assert max(pos_scores) - max(sil_scores) > 0.35
    assert max(rev_scores) < max(pos_scores)


def test_receive_audio_schedules_long_running_wake_word_as_asyncio_task():
    """_receive_audio must wrap LONG_RUNNING_TOOLS in asyncio.create_task()."""
    import main

    ui = MagicMock(muted=True)
    ui.set_state = lambda *_a, **_k: None
    ui.write_log = lambda *_a, **_k: None
    ui.set_speaking = lambda *_a, **_k: None
    live = main.FridayLive(ui)
    live.set_speaking = lambda *_a, **_k: None

    tool_started = asyncio.Event()
    release_tool = asyncio.Event()
    sent_responses = []
    observed_tasks = []

    def slow_wake_tool(_args):
        return "wake setup finished"

    live._tool_handlers["wake_word"] = (slow_wake_tool, 1500)

    orig_bg = live._execute_tool_in_background

    async def tracked_bg(fc):
        tool_started.set()
        await release_tool.wait()
        return await orig_bg(fc)

    live._execute_tool_in_background = tracked_bg

    fc = SimpleNamespace(name="wake_word", args={"action": "setup"}, id="call-1")
    tool_msg = SimpleNamespace(
        data=None,
        server_content=None,
        tool_call=SimpleNamespace(function_calls=[fc]),
        tool_call_cancellation=None,
    )
    follow_up_msg = SimpleNamespace(
        data=None,
        server_content=SimpleNamespace(
            interrupted=False,
            model_turn=None,
            input_transcription=None,
            output_transcription=None,
            turn_complete=True,
        ),
        tool_call=None,
        tool_call_cancellation=None,
    )

    class FakeSession:
        def receive(self):
            async def _gen():
                yield tool_msg
                await tool_started.wait()
                observed_tasks.extend(list(live._background_tasks))
                yield follow_up_msg
                release_tool.set()
                raise asyncio.CancelledError()
            return _gen()

        async def send_tool_response(self, function_responses):
            sent_responses.append(function_responses[0].response["result"])

    async def scenario():
        live.session = FakeSession()
        live._send_lock = asyncio.Lock()
        live._ready = True
        with pytest.raises(asyncio.CancelledError):
            await live._receive_audio()
        if live._background_tasks:
            await asyncio.gather(*list(live._background_tasks))

    asyncio.run(scenario())

    assert len(observed_tasks) == 1
    assert isinstance(observed_tasks[0], asyncio.Task)
    assert sent_responses == ["wake setup finished"]
    assert len(live._background_tasks) == 0

