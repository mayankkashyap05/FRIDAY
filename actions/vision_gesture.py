# actions/vision_gesture.py
# Friday — Full-body computer vision service (FREE, local, no AI API)
#
# Uses Google MediaPipe Tasks (free, on-device, accurate) + OpenCV:
#   - HolisticLandmarker: full-body pose (33 pts) + hands (21 pts ×2) + face mesh
#   - FaceDetector: count people
#   - cv2.QRCodeDetector: read QR / barcodes
#
# Modes (continuous, background thread):
#   gesture, air_mouse, volume, motion, posture, objects, focus
#
# Optional dependencies (cv2, numpy, mediapipe, pyautogui and the vision/*
# helpers) are all guarded so this module imports cleanly on Windows/Linux.

from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path

try:
    import cv2
except ImportError:                                    # pragma: no cover - environment
    cv2 = None

try:
    import numpy as np
except ImportError:                                    # pragma: no cover - environment
    np = None

try:
    from vision.camera_manager import CameraConfig, CameraManager
    from vision.object_detector import DetectorConfig, ObjectDetector
    from vision.ocr_engine import CodeReader, OCREngine
    from vision.face_privacy import FacePrivacyProcessor
    from vision.segmentation import ForegroundSegmenter
    from vision.understanding import SceneCaptioner, VisualQuestionAnswering
    from vision.anomaly_monitor import AnomalyMonitor
    from vision.face_recognition import FaceProfileStore, FaceRecognitionEngine
    from vision.multimodal import MultimodalVision
    from vision.recording import VisionRecorder
    from vision.opencv_face_verifier import OpenCVFaceVerifier
    from vision.event_store import VisionEventStore
except Exception:                                      # pragma: no cover - environment
    CameraConfig = CameraManager = ObjectDetector = DetectorConfig = None
    OCREngine = CodeReader = FacePrivacyProcessor = ForegroundSegmenter = None
    SceneCaptioner = VisualQuestionAnswering = AnomalyMonitor = None
    FaceProfileStore = FaceRecognitionEngine = MultimodalVision = None
    VisionRecorder = OpenCVFaceVerifier = VisionEventStore = None


class _StubHelper:
    """Safe no-op replacement for an unavailable vision helper."""

    error = "optional vision dependency is unavailable"
    is_recording = False

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None

    def detect(self, *args, **kwargs):
        return []

    def extract(self, *args, **kwargs):
        return []

    def blur(self, frame, *args, **kwargs):
        return frame, []

    def caption(self, *args, **kwargs):
        return "Scene understanding is unavailable, sir."

    def answer(self, *args, **kwargs):
        return "Scene understanding is unavailable, sir."

    def ask(self, *args, **kwargs):
        return "Vision AI is unavailable, sir."

    def verify(self, *args, **kwargs):
        return (False, None, None)

    def load(self, *args, **kwargs):
        return False

    def read(self, *args, **kwargs):
        return []

    def recent(self, *args, **kwargs):
        return []


_MP = None  # None=not checked yet, False=unavailable, tuple=available


def _get_mp():
    """Lazily import mediapipe; returns (mp_python, mp_vision, MPImage, MPImageFormat) or None."""
    global _MP
    if _MP is None:
        try:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision
            from mediapipe import Image as MPImage
            from mediapipe import ImageFormat as MPImageFormat
            _MP = (mp_python, mp_vision, MPImage, MPImageFormat)
        except Exception:
            _MP = False
    return _MP or None


_PYAUTOGUI = None  # None=not checked yet, False=unavailable, module=available


def _get_pyautogui():
    """Lazily import pyautogui; returns the module or None."""
    global _PYAUTOGUI
    if _PYAUTOGUI is None:
        try:
            import pyautogui
            _PYAUTOGUI = pyautogui
        except Exception:
            _PYAUTOGUI = False
    return _PYAUTOGUI or None


BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"

MODES = {"gesture", "air_mouse", "volume", "motion", "posture", "objects", "focus"}

# ── Landmark indices ──────────────────────────────────────────────────────────
HAND_INDEX_TIP  = 8
HAND_INDEX_PIP  = 6
HAND_MIDDLE_TIP = 12
HAND_MIDDLE_PIP = 10
HAND_RING_TIP   = 16
HAND_RING_PIP   = 14
HAND_PINKY_TIP  = 20
HAND_PINKY_PIP  = 18
HAND_THUMB_TIP  = 4
HAND_THUMB_IP   = 3
HAND_THUMB_CMC  = 1
HAND_WRIST      = 0
HAND_PINKY_MCP  = 17
HAND_INDEX_MCP  = 5
HAND_MIDDLE_MCP = 9

POS_SHOULDER_L = 11
POS_SHOULDER_R = 12
POS_ELBOW_L    = 13
POS_ELBOW_R    = 14
POS_WRIST_L    = 15
POS_WRIST_R    = 16
POS_HIP_L      = 23
POS_HIP_R      = 24
POS_NOSE       = 0

PINCH_ENGAGE = 0.35
PINCH_RELEASE = 0.48
DRAG_AFTER_S = 0.45
ACTIVE_MARGIN = 0.12
GESTURE_FRAMES = 3
MOTION_SENSITIVITY = 0.025
FOCUS_GRACE_S = 10.0


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def _dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def _palm_scale(hand):
    """Wrist → middle-finger MCP distance, stable across camera depth."""
    if not hand or len(hand) <= 9:
        return 1e-6
    return max(_dist(hand[HAND_WRIST], hand[HAND_MIDDLE_MCP]), 1e-6)


def _map_active_region(value, margin: float = ACTIVE_MARGIN) -> float:
    """Map a camera-space coordinate onto the full screen, corner to corner."""
    try:
        value = float(value)
        margin = float(margin)
    except (TypeError, ValueError):
        return 0.0
    margin = _clamp(margin, 0.0, 0.45)
    if margin <= 0 or margin >= 0.5:
        return _clamp(value)
    return _clamp((value - margin) / (1.0 - 2.0 * margin))


class _OneEuroFilter:
    """Casiez et al. 1€ filter: steady for slow motion, lag-free for fast motion."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.0,
                 d_cutoff: float = 1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._last_time = None
        self._last_value = None
        self._last_derivative = 0.0

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * max(cutoff, 1e-6))
        dt = max(dt, 1e-6)
        return 1.0 / (1.0 + tau / dt)

    @staticmethod
    def _lowpass(value: float, alpha: float, previous: float) -> float:
        return alpha * value + (1.0 - alpha) * previous

    def __call__(self, value: float, timestamp: float | None = None) -> float:
        now = float(timestamp) if timestamp is not None else time.monotonic()
        value = float(value)
        if self._last_time is None:
            self._last_time = now
            self._last_value = value
            self._last_derivative = 0.0
            return value
        dt = max(now - self._last_time, 1e-6)
        derivative = (value - self._last_value) / dt
        ed = self._lowpass(
            derivative, self._alpha(self.d_cutoff, dt), self._last_derivative
        )
        cutoff = self.min_cutoff + self.beta * abs(ed)
        smoothed = self._lowpass(
            value, self._alpha(cutoff, dt), self._last_value
        )
        self._last_time = now
        self._last_value = smoothed
        self._last_derivative = ed
        return smoothed


def _extended_fingers(hand, handedness=None) -> list[bool]:
    """Return [thumb, index, middle, ring, pinky] extension flags."""
    if not hand or len(hand) <= 20:
        return [False] * 5
    wrist = hand[HAND_WRIST]
    pinky_mcp = hand[HAND_PINKY_MCP]

    flags = []
    for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
        extended = _dist(hand[tip], wrist) > _dist(hand[pip], wrist) * 1.04
        if not extended and abs(_dist(hand[tip], wrist) -
                                _dist(hand[pip], wrist)) < 0.01:
            extended = hand[tip].y < hand[pip].y
        flags.append(bool(extended))

    tip = hand[HAND_THUMB_TIP]
    ip = hand[HAND_THUMB_IP]
    cmc = hand[HAND_THUMB_CMC]
    side = str(handedness or "").strip().lower()
    if side.startswith("right"):
        direction = tip.x > ip.x
    elif side.startswith("left"):
        direction = tip.x < ip.x
    else:
        direction = _dist(tip, pinky_mcp) > _dist(ip, pinky_mcp)
    stretch = (_dist(tip, pinky_mcp) > _dist(ip, pinky_mcp) * 1.02
               or _dist(tip, wrist) > _dist(cmc, wrist) * 1.08)
    thumb = bool(direction and stretch)
    if not thumb and _dist(tip, wrist) > _dist(ip, wrist) * 1.25:
        thumb = True
    return [thumb] + flags


def _finger_count(hand, handedness=None) -> int:
    """Counts extended fingers for a MediaPipe hand landmark list."""
    return sum(1 for flag in _extended_fingers(hand, handedness) if flag)


def _gesture_name(fingers: int, pinch: bool, point: bool,
                  hand=None, handedness=None) -> str:
    if hand is not None:
        return _classify_gesture(hand, handedness)
    if pinch and fingers <= 1:
        return "pinch / grab"
    if fingers == 0:
        return "fist"
    if fingers == 5:
        return "open hand"
    if fingers == 1 and point:
        return "pointing"
    if fingers == 1:
        return "one finger (index)"
    if fingers == 2 and not point:
        return "peace sign"
    if fingers == 2:
        return "two fingers"
    if fingers == 3:
        return "three fingers"
    if fingers == 4:
        return "four fingers"
    return f"{fingers} fingers"


def _classify_gesture(hand, handedness=None) -> str:
    """Classify a hand using palm-scale-normalised landmark geometry."""
    flags = _extended_fingers(hand, handedness)
    thumb, index, middle, ring, pinky = flags
    fingers = sum(flags)
    scale = _palm_scale(hand)
    if not hand or len(hand) <= 12:
        return "fist"
    pinch = _dist(hand[HAND_THUMB_TIP], hand[HAND_INDEX_TIP]) / scale < PINCH_ENGAGE
    middle_pinch = _dist(hand[HAND_THUMB_TIP], hand[HAND_MIDDLE_TIP]) / scale < PINCH_ENGAGE

    # A closed fist also puts thumb and index close together; require the
    # thumb (or index) to be extended before calling it a pinch.
    pinch = pinch and (thumb or index)
    if pinch and middle and ring and pinky and not index:
        return "ok sign"
    if pinch:
        return "pinch / grab"
    if middle_pinch and not index and not middle:
        return "pinch / grab"
    if fingers == 0:
        return "fist"
    if fingers == 5:
        return "open hand"
    if thumb and not (index or middle or ring or pinky):
        if hand[HAND_THUMB_TIP].y < hand[HAND_WRIST].y - scale * 0.4:
            return "thumbs up"
        if hand[HAND_THUMB_TIP].y > hand[HAND_WRIST].y + scale * 0.4:
            return "thumbs down"
        return "thumbs up"
    if index and not (thumb or middle or ring or pinky):
        return "pointing"
    if fingers == 3:
        return "three fingers"
    if index and middle and not (ring or pinky):
        return "peace sign"
    if fingers == 4:
        return "four fingers"
    return _gesture_name(fingers, False, index)


class VisionService:
    """Background-thread camera service with switchable modes."""

    def __init__(self):
        self._lock = threading.RLock()
        self._running = False
        self._mode = "gesture"
        self._thread = None
        self._cap = None
        self._camera = None
        self._player = None
        self._speak = None
        self._ts = 0
        self._last_mp_ts_ms = 0
        self._last_tell = 0
        self._holistic = None
        self._faces = None
        self._prev_gray = None
        self._ready = threading.Event()
        self._motion_sensitivity = MOTION_SENSITIVITY
        self._focus_grace = FOCUS_GRACE_S
        self._away_since = None
        self._focus_away = False
        self._gesture_candidate = None
        self._gesture_frames = 0
        self._last_gesture = ""
        self._pinch_active = False
        self._pinch_since = 0.0
        self._drag_active = False
        self._middle_pinch_active = False
        self._volume_ema = None
        self._last_volume = None
        self._filters = {
            "x": _OneEuroFilter(min_cutoff=1.2, beta=0.08, d_cutoff=1.0),
            "y": _OneEuroFilter(min_cutoff=1.2, beta=0.08, d_cutoff=1.0),
        }

        def build(factory, *args, **kwargs):
            if factory is None:
                return _StubHelper()
            try:
                return factory(*args, **kwargs)
            except Exception:
                return _StubHelper()

        self._object_detector = build(
            ObjectDetector, DetectorConfig(model_path=MODELS_DIR / "yolo11n.pt")
        ) if ObjectDetector is not None and DetectorConfig is not None else _StubHelper()
        self._ocr_engine = build(OCREngine)
        self._code_reader = build(CodeReader)
        self._privacy_processor = build(FacePrivacyProcessor)
        self._segmenter = build(ForegroundSegmenter)
        self._captioner = build(SceneCaptioner)
        self._vqa = build(VisualQuestionAnswering)
        self._anomaly_monitor = build(AnomalyMonitor)
        self._event_store = build(
            VisionEventStore, BASE_DIR / "data" / "vision" / "events.db"
        ) if VisionEventStore is not None else _StubHelper()
        self._face_profiles = build(
            FaceProfileStore, BASE_DIR / "data" / "vision" / "face_profiles.json"
        ) if FaceProfileStore is not None else _StubHelper()
        self._face_recognition = build(
            FaceRecognitionEngine, self._face_profiles
        ) if FaceRecognitionEngine is not None else _StubHelper()
        self._opencv_identity = build(
            OpenCVFaceVerifier,
            MODELS_DIR / "face_detection_yunet_2023mar.onnx",
            MODELS_DIR / "face_recognition_sface_2021dec.onnx",
            self._face_profiles,
        ) if OpenCVFaceVerifier is not None else _StubHelper()
        self._multimodal = build(MultimodalVision)
        self._recorder = build(VisionRecorder)

    # ── Setup / control ───────────────────────────────────────────────────────
    def configure(self, player=None, speak=None):
        self._player = player
        self._speak = speak

    def _camera_index(self) -> int:
        try:
            cfg = json.loads((BASE_DIR / "config" / "api_keys.json").read_text(
                encoding="utf-8"))
        except Exception:
            return 0
        value = cfg.get("camera_index", cfg.get("camera", {}).get("index", 0))
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _open_camera(self, index=None):
        if cv2 is None:
            return None
        index = self._camera_index() if index is None else int(index)
        capture = None
        if os.name == "nt" and hasattr(cv2, "CAP_DSHOW"):
            try:
                capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
                if capture is not None and not capture.isOpened():
                    capture.release()
                    capture = None
            except Exception:
                capture = None
        if capture is None:
            try:
                capture = cv2.VideoCapture(index)
            except Exception:
                return None
        if capture is None:
            return None
        try:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        except Exception:
            pass
        return capture

    def _reset_tracking(self):
        """Clear all temporal state when a mode changes or the loop restarts."""
        self._prev_gray = None
        self._gesture_candidate = None
        self._gesture_frames = 0
        self._pinch_active = False
        self._pinch_since = 0.0
        self._drag_active = False
        self._middle_pinch_active = False
        self._volume_ema = None
        self._last_volume = None
        self._away_since = None
        self._focus_away = False
        self._filters["x"] = _OneEuroFilter(1.2, 0.08, 1.0)
        self._filters["y"] = _OneEuroFilter(1.2, 0.08, 1.0)

    def set_mode(self, mode: str) -> str:
        mode = str(mode or "").strip().lower()
        if mode not in MODES:
            return (f"Unknown vision mode: {mode}. Use: " + ", ".join(sorted(MODES)))
        with self._lock:
            changed = self._mode != mode
            self._mode = mode
        if changed:
            self._reset_tracking()
        return f"Vision mode switched to {mode}."

    def start(self, mode: str = "gesture") -> str:
        if _get_mp() is None:
            return "MediaPipe is not installed. Run: pip install mediapipe"
        mode = str(mode or "gesture").strip().lower()
        if mode not in MODES:
            return "Unknown vision mode: " + ", ".join(sorted(MODES))
        with self._lock:
            already = self._running
            if self._mode != mode:
                self._mode = mode
                self._reset_tracking()
        if not already:
            self._ready.clear()
            self._reset_tracking()
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="VisionGestureThread"
            )
            self._thread.start()
            if not self._ready.wait(timeout=20):
                return "Camera failed to open, sir."
        return f"Vision mode active: {mode}. Say 'stop vision' when done."

    def stop(self) -> str:
        # The loop thread owns the camera object and releases it on exit.
        with self._lock:
            self._running = False
        return "Vision stopped, sir."

    @property
    def is_running(self) -> bool:
        return self._running

    def _build_tools(self):
        mp = _get_mp()
        if mp is None:
            return False
        mp_python, mp_vision, MPImage, MPImageFormat = mp
        self._mp_image_factory = MPImage
        self._mp_image_format = MPImageFormat
        base = mp_python.BaseOptions(
            model_asset_path=str(MODELS_DIR / "holistic_landmarker.task")
        )
        self._holistic = mp_vision.HolisticLandmarker.create_from_options(
            mp_vision.HolisticLandmarkerOptions(
                base_options=base,
                running_mode=mp_vision.RunningMode.VIDEO,
                min_pose_detection_confidence=0.5,
                min_hand_landmarks_confidence=0.5,
                min_face_landmarks_confidence=0.5,
            )
        )
        self._faces = mp_vision.FaceDetector.create_from_options(
            mp_vision.FaceDetectorOptions(
                base_options=mp_python.BaseOptions(
                    model_asset_path=str(MODELS_DIR / "face_detector.tflite")
                ),
                running_mode=mp_vision.RunningMode.VIDEO,
            )
        )
        return True

    def _close_tools(self):
        for name in ("_holistic", "_faces"):
            tool = getattr(self, name, None)
            if tool is not None:
                try:
                    if hasattr(tool, "close"):
                        tool.close()
                except Exception:
                    pass
                setattr(self, name, None)

    # ── Main loop ─────────────────────────────────────────────────────────────
    def _loop(self):
        self._reset_tracking()
        camera_config = CameraConfig(index=self._camera_index(), width=640, height=480)
        self._camera = CameraManager(camera_config)
        try:
            if not self._camera.start():
                print("[VisionGesture] ❌ Camera failed to open")
                return
            if not self._build_tools():
                print("[VisionGesture] ❌ MediaPipe unavailable")
                return

            with self._lock:
                self._running = True
            self._ready.set()
            print("[VisionGesture] ✅ Camera running")

            while self._running:
                packet = self._camera.latest()
                if packet is None or packet.sequence <= self._ts:
                    time.sleep(0.02)
                    continue
                self._ts = packet.sequence
                if self._player and getattr(self._player, "vision_preview_open", False):
                    safe_frame, _ = self._privacy_processor.blur(packet.frame)
                    try:
                        self._player.set_vision_frame(safe_frame)
                    except Exception:
                        pass
                if self._player and getattr(self._player, "arcade_open", False):
                    try:
                        self._player.set_arcade_frame(packet.frame)
                    except Exception:
                        pass
                if self._recorder.is_recording:
                    safe_frame, _ = self._privacy_processor.blur(packet.frame)
                    self._recorder.write(safe_frame)
                with self._lock:
                    mode = self._mode
                try:
                    self._process(packet.frame, mode, self._ts)
                except Exception as exc:
                    print(f"[VisionGesture] ⚠️ {exc}")
                time.sleep(0.02)
        except Exception as exc:
            print(f"[VisionGesture] ❌ {exc}")
        finally:
            with self._lock:
                self._running = False
            if self._camera is not None:
                try:
                    self._camera.stop()
                except Exception:
                    pass
                self._camera = None
            self._close_tools()
            self._prev_gray = None
            self._ready.set()
            print("[VisionGesture] 🔴 Camera stopped")

    def _next_mp_ts_ms(self) -> int:
        """MediaPipe VIDEO mode requires strictly increasing timestamps."""
        self._last_mp_ts_ms = max(
            self._last_mp_ts_ms + 1, int(time.monotonic() * 1000)
        )
        return self._last_mp_ts_ms

    def _process(self, frame, mode: str, ts: int):
        if cv2 is None or self._holistic is None:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = self._mp_image_factory(
            image_format=self._mp_image_format.SRGB, data=rgb
        )
        ts_ms = self._next_mp_ts_ms()
        res = self._holistic.detect_for_video(mp_image, ts_ms)
        pose = res.pose_landmarks if res.pose_landmarks else None
        hands = []
        if res.right_hand_landmarks:
            hands.append((res.right_hand_landmarks, "Right"))
        if res.left_hand_landmarks:
            hands.append((res.left_hand_landmarks, "Left"))
        hand_landmarks = [hand for hand, _ in hands]

        if mode == "gesture":
            self._handle_gesture(hand_landmarks,
                                 hands[0][1] if hands else None)
        elif mode == "air_mouse":
            self._handle_air_mouse(hand_landmarks)
        elif mode == "volume":
            self._handle_volume(hand_landmarks)
        elif mode == "posture":
            self._handle_posture(pose)
        elif mode == "motion":
            self._handle_motion(frame)
        elif mode == "objects":
            self._handle_objects(frame, ts)
        elif mode == "focus":
            self._handle_focus(pose, hand_landmarks)

    # ── Mode handlers ─────────────────────────────────────────────────────────
    def _tell(self, text: str, cooldown: float = 1.5):
        now = time.time()
        if now - self._last_tell < cooldown:
            return
        self._last_tell = now
        if self._speak:
            try:
                self._speak(text)
            except Exception:
                pass
        if self._player:
            try:
                self._player.write_log(f"Vision: {text}")
                self._player.set_vision_signal(text)
            except Exception:
                pass
        print(f"[VisionGesture] 💬 {text}")

    def _handle_gesture(self, hands, handedness=None):
        if not hands:
            return
        hand = hands[0]
        name = _classify_gesture(hand, handedness)
        if name == self._gesture_candidate:
            self._gesture_frames += 1
        else:
            self._gesture_candidate = name
            self._gesture_frames = 1
        if self._gesture_frames >= GESTURE_FRAMES and name != self._last_gesture:
            self._last_gesture = name
            self._tell(f"Gesture detected: {name}.", cooldown=0.0)
        if self._player and hasattr(self._player, "set_arcade_signal"):
            try:
                self._player.set_arcade_signal(name, hand[HAND_INDEX_TIP].x)
            except Exception:
                pass

    def _handle_air_mouse(self, hands):
        pyautogui = _get_pyautogui()
        if not hands or pyautogui is None:
            return
        hand = hands[0]
        idx = hand[HAND_INDEX_TIP]
        scr_w, scr_h = pyautogui.size()
        now = time.monotonic()
        x_norm = _map_active_region(1.0 - idx.x, ACTIVE_MARGIN)
        y_norm = _map_active_region(idx.y, ACTIVE_MARGIN)
        x = int(_clamp(self._filters["x"](x_norm, now), 0.0, 0.9999) * scr_w)
        y = int(_clamp(self._filters["y"](y_norm, now), 0.0, 0.9999) * scr_h)
        try:
            pyautogui.moveTo(x, y, duration=0)
        except Exception:
            pass

        scale = _palm_scale(hand)
        thumb = hand[HAND_THUMB_TIP]
        middle = hand[HAND_MIDDLE_TIP]
        pinch = _dist(thumb, idx) / scale

        if pinch < PINCH_ENGAGE:
            if not self._pinch_active:
                self._pinch_active = True
                self._pinch_since = now
                try:
                    pyautogui.click()
                except Exception:
                    pass
                self._tell("Click.", cooldown=0.6)
            elif now - self._pinch_since >= DRAG_AFTER_S and not self._drag_active:
                self._drag_active = True
                try:
                    pyautogui.mouseDown()
                except Exception:
                    pass
                self._tell("Drag.", cooldown=0.6)
        elif pinch > PINCH_RELEASE and self._pinch_active:
            if self._drag_active:
                try:
                    pyautogui.mouseUp()
                except Exception:
                    pass
            self._pinch_active = False
            self._drag_active = False

        middle_pinch = _dist(thumb, middle) / scale
        if middle_pinch < PINCH_ENGAGE and not self._middle_pinch_active:
            self._middle_pinch_active = True
            try:
                pyautogui.click(button="right")
            except Exception:
                pass
            self._tell("Right click.", cooldown=0.6)
        elif middle_pinch > PINCH_RELEASE and self._middle_pinch_active:
            self._middle_pinch_active = False

    def _handle_volume(self, hands):
        if not hands:
            return
        hand = hands[0]
        scale = _palm_scale(hand)
        ratio = _dist(hand[HAND_THUMB_TIP], hand[HAND_INDEX_TIP]) / scale
        target = int(_clamp(1.0 - ratio / 1.5, 0.0, 1.0) * 100)
        if self._volume_ema is None:
            self._volume_ema = float(target)
        else:
            self._volume_ema = 0.7 * self._volume_ema + 0.3 * target
        pct = int(round(self._volume_ema))
        if self._last_volume is not None and abs(pct - self._last_volume) < 3:
            return
        self._last_volume = pct
        try:
            from actions.computer_settings import volume_set
            volume_set(pct)
        except Exception:
            pass
        self._tell(f"Volume {pct}%.", cooldown=3.0)

    def _handle_posture(self, pose):
        if not pose:
            if self._player and hasattr(self._player, "set_arcade_pose"):
                self._player.set_arcade_pose(False, 0.0)
            self._tell("No body detected in frame.", cooldown=5.0)
            return
        nose = pose[POS_NOSE]
        so = (pose[POS_SHOULDER_L], pose[POS_SHOULDER_R])
        sh_mid = ((so[0].x + so[1].x) / 2, (so[0].y + so[1].y) / 2)
        wrist_top = min(pose[POS_WRIST_L].y, pose[POS_WRIST_R].y)
        shoulder_y = min(so[0].y, so[1].y)

        if wrist_top < shoulder_y - 0.1:
            if self._player and hasattr(self._player, "set_arcade_pose"):
                self._player.set_arcade_pose(True, 0.0)
            self._tell("Arms are raised.", cooldown=4.0)
        elif abs(nose.x - sh_mid[0]) > 0.12:
            if self._player and hasattr(self._player, "set_arcade_pose"):
                self._player.set_arcade_pose(False, abs(nose.x - sh_mid[0]))
            self._tell("Leaning to the side.", cooldown=4.0)
        else:
            if self._player and hasattr(self._player, "set_arcade_pose"):
                self._player.set_arcade_pose(False, 0.0)
            self._tell("Body detected, neutral posture.", cooldown=8.0)

    def _handle_motion(self, frame, sensitivity=None):
        if cv2 is None or np is None:
            return
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        if self._prev_gray is None:
            self._prev_gray = gray
            return
        diff = cv2.absdiff(self._prev_gray, gray)
        self._prev_gray = gray
        thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)[1]
        motion_pct = float(np.mean(thresh > 0))
        threshold = self._motion_sensitivity if sensitivity is None else float(sensitivity)
        if motion_pct > threshold:
            self._tell(
                f"Motion detected, sir. Coverage {motion_pct * 100:.1f}%.",
                cooldown=3.0,
            )

    def _handle_focus(self, pose, hands):
        present = bool(pose or hands)
        now = time.monotonic()
        if present:
            if self._focus_away:
                self._tell("Welcome back, sir.", cooldown=0.0)
            self._away_since = None
            self._focus_away = False
            return
        if self._away_since is None:
            self._away_since = now
        elif not self._focus_away and now - self._away_since >= self._focus_grace:
            self._focus_away = True
            self._tell("You left the frame, sir. Is everything alright?", cooldown=0.0)

    def _handle_objects(self, frame, ts):
        events = self._object_detector.detect(frame, timestamp=time.time())
        if events:
            anomalies = self._anomaly_monitor.observe(events)
            self._event_store.add_many(events + anomalies)
            counts = {}
            for event in events:
                counts[event.label] = counts.get(event.label, 0) + 1
            summary = ", ".join(f"{count} {label}" for label, count in counts.items())
            self._tell(f"Objects detected: {summary}.", cooldown=2.0)
            if anomalies:
                self._tell("Anomaly detected: scene changed.", cooldown=4.0)
            if self._player and hasattr(self._player, "set_vision_detections"):
                self._player.set_vision_detections(events)
                self._player.set_vision_signal(self._captioner.caption(events))
        elif self._object_detector.error and self._player and hasattr(
            self._player, "set_vision_signal"
        ):
            self._player.set_vision_signal("OBJECT DETECTION: " + self._object_detector.error)

    # ── One-shot actions ──────────────────────────────────────────────────────
    def _capture_frame(self):
        frames = self._capture_frames(1)
        return frames[-1] if frames else None

    def _capture_frames(self, count: int = 1):
        count = max(1, int(count or 1))
        if self._running and self._camera is not None:
            frames = []
            seen = set()
            deadline = time.monotonic() + 1.0
            while len(frames) < count and time.monotonic() < deadline:
                packet = self._camera.latest()
                if packet is not None and packet.sequence not in seen:
                    seen.add(packet.sequence)
                    frames.append(packet.frame)
                time.sleep(0.02)
            if not frames:
                packet = self._camera.latest()
                if packet is not None:
                    frames = [packet.frame] * count
            return frames

        cap = self._open_camera()
        if cap is None:
            return []
        frames = []
        try:
            for _ in range(5):
                cap.read()
            for _ in range(count):
                ok, frame = cap.read()
                if ok:
                    frames.append(frame)
                time.sleep(0.04)
        finally:
            try:
                cap.release()
            except Exception:
                pass
        return frames

    def face_count(self) -> str:
        mp = _get_mp()
        if mp is None:
            return "MediaPipe is not installed, sir."
        mp_python, mp_vision, MPImage, MPImageFormat = mp
        try:
            det = mp_vision.FaceDetector.create_from_options(
                mp_vision.FaceDetectorOptions(
                    base_options=mp_python.BaseOptions(
                        model_asset_path=str(MODELS_DIR / "face_detector.tflite")
                    ),
                    running_mode=mp_vision.RunningMode.IMAGE,
                )
            )
            n_faces = 0
            for frame in self._capture_frames(8):
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                res = det.detect(MPImage(image_format=MPImageFormat.SRGB, data=rgb))
                if res.detections:
                    n_faces = max(n_faces, len(res.detections))
            return f"I see {n_faces} person(s) in front of the camera, sir."
        except Exception as exc:
            return f"Face detection failed: {exc}"

    def read_qr(self) -> str:
        try:
            if cv2 is None:
                return "QR reading failed: OpenCV is not installed."
            detector = cv2.QRCodeDetector()
            for frame in self._capture_frames(15):
                data, *_ = detector.detectAndDecode(frame)
                if data:
                    return f"QR code read: {data[:150]}"
            return "I couldn't find a QR code, sir. Make sure it's fully visible."
        except Exception as exc:
            return f"QR reading failed: {exc}"

    def read_text(self) -> str:
        """Capture one frame and return readable text, if OCR is available."""
        frame = self._capture_frame()
        if frame is None:
            return "Could not capture camera frame, sir."
        events = self._ocr_engine.extract(frame)
        if not events:
            return self._ocr_engine.error or "I couldn't find readable text, sir."
        text = " ".join(event.label for event in events)
        return f"I can read: {text[:1000]}"

    def remove_background_snapshot(self) -> str:
        """Capture a frame, remove its background, and save a PNG artifact."""
        frame = self._capture_frame()
        if frame is None:
            return "Could not capture camera frame, sir."
        try:
            output = self._segmenter.remove_background(frame, transparent=True)
            if output is None:
                return self._segmenter.error or "Background removal failed, sir."
            out_dir = BASE_DIR / "data" / "vision"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"foreground_{int(time.time())}.png"
            cv2.imwrite(str(path), output)
            return f"Background removed. Saved transparent image to {path}."
        except Exception as exc:
            return f"Background removal failed: {exc}"

    def describe_scene(self, question: str = "") -> str:
        """Capture one frame and answer from local detector/OCR evidence."""
        frame = self._capture_frame()
        if frame is None:
            return "Could not capture camera frame, sir."
        try:
            events = self._object_detector.detect(frame, timestamp=time.time())
            events.extend(self._ocr_engine.extract(frame))
            events.extend(self._privacy_processor.detect(frame))
            return self._vqa.answer(question, events) if question \
                else self._captioner.caption(events)
        except Exception as exc:
            return f"Scene understanding failed: {exc}"

    def register_face(self, name: str) -> str:
        if not str(name).strip():
            return "Please provide a profile name, for example: register my face as Doniyor."
        frame = self._capture_frame()
        if frame is None:
            return "Could not capture camera frame, sir."
        if self._opencv_identity.load():
            if self._opencv_identity.enroll(name, frame):
                return f"Face profile registered for {name} using local OpenCV verification."
            return self._opencv_identity.error or "Face registration failed, sir."
        embedding = self._face_recognition.embedding_from_frame(frame)
        if embedding is None:
            return self._face_recognition.error or "Face registration is unavailable, sir."
        self._face_profiles.add(name, embedding)
        return f"Face profile registered for {name}."

    def identify_face(self) -> str:
        if self._opencv_identity.load():
            best = (False, None, None)
            for frame in self._capture_frames(5):
                result = self._opencv_identity.verify(frame)
                if result[2] is not None and (best[2] is None or result[2] > best[2]):
                    best = result
                if result[0]:
                    return f"Identity verified: {result[1]} (match {result[2]:.2f}), sir."
            if best[1] is not None:
                return f"Identity verification failed: best match {best[1]} scored {best[2]:.2f}, sir."
            return "Identity verification failed: face not recognized, sir."
        frame = self._capture_frame()
        if frame is None:
            return "Could not capture camera frame, sir."
        embedding = self._face_recognition.embedding_from_frame(frame)
        if embedding is None:
            return self._face_recognition.error or "Face recognition is unavailable, sir."
        name = self._face_recognition.identify(embedding)
        return f"I recognize {name}, sir." if name else "Unknown face, sir."

    def ask_multimodal(self, question: str = "Describe this scene.") -> str:
        frame = self._capture_frame()
        if frame is None:
            return "Could not capture camera frame, sir."
        return self._multimodal.ask(frame, question)

    def start_recording(self) -> str:
        if not self._running or not self._camera:
            return "Start a vision camera mode before recording, sir."
        packet = self._camera.latest()
        if packet is None:
            return "Camera has not produced a frame yet, sir."
        height, width = packet.frame.shape[:2]
        path = self._recorder.start(VisionRecorder.default_path(BASE_DIR), (width, height))
        return f"Privacy-safe recording started: {path}"

    def stop_recording(self) -> str:
        path = self._recorder.stop()
        return f"Recording saved: {path}" if path else "No recording is active, sir."

    def snapshot(self, question: str = "") -> str:
        mp = _get_mp()
        if mp is None:
            return "MediaPipe is not installed, sir."
        mp_python, mp_vision, MPImage, MPImageFormat = mp
        try:
            det = mp_vision.HolisticLandmarker.create_from_options(
                mp_vision.HolisticLandmarkerOptions(
                    base_options=mp_python.BaseOptions(
                        model_asset_path=str(MODELS_DIR / "holistic_landmarker.task")
                    ),
                    running_mode=mp_vision.RunningMode.IMAGE,
                    min_pose_detection_confidence=0.5,
                )
            )
            frame = self._capture_frame()
            if frame is None:
                return "Could not capture camera frame, sir."
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = det.detect(MPImage(image_format=MPImageFormat.SRGB, data=rgb))
            pose = res.pose_landmarks if res.pose_landmarks else None
            hands = []
            if res.right_hand_landmarks:
                hands.append(res.right_hand_landmarks)
            if res.left_hand_landmarks:
                hands.append(res.left_hand_landmarks)
            desc = ["a person is in frame" if pose else "no person detected"]
            if hands:
                fingers = sum(_finger_count(hand) for hand in hands)
                desc.append(f"{len(hands)} hand(s), {fingers} fingers visible")
            return "On camera: " + ", ".join(desc) + "."
        except Exception as exc:
            return f"Snapshot failed: {exc}"

    def status(self) -> str:
        with self._lock:
            running = self._running
            mode = self._mode
        camera_health = "unavailable"
        if self._camera is not None:
            camera_health = "error" if getattr(self._camera, "error", None) else "healthy"
        if running and self._camera is not None and self._camera.latest() is None:
            camera_health = "starting"
        recording = bool(getattr(self._recorder, "is_recording", False))
        return (
            f"Vision service is {'active' if running else 'stopped'} "
            f"(mode: {mode}, recording: {'yes' if recording else 'no'}, "
            f"last gesture: {self._last_gesture or 'none'}, camera: {camera_health})."
        )


_service = VisionService()


def vision_gesture(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    """Control the full-body vision service."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action", "status")).lower().strip()
    mode = str(params.get("mode", _service._mode)).lower().strip()

    _service.configure(player=player, speak=speak)

    if action == "start":
        return _service.start(mode)
    if action == "stop":
        return _service.stop()
    if action in ("mode", "switch", "set_mode"):
        return _service.set_mode(mode)
    if action in ("record_start", "start_recording"):
        return _service.start_recording()
    if action in ("record_stop", "stop_recording"):
        return _service.stop_recording()
    if action == "face_count":
        return _service.face_count()
    if action == "qr":
        return _service.read_qr()
    if action in ("ocr", "read_text", "text"):
        return _service.read_text()
    if action in ("background_remove", "remove_background", "cutout"):
        return _service.remove_background_snapshot()
    if action in ("describe", "caption", "scene"):
        return _service.describe_scene()
    if action in ("vqa", "ask_scene", "visual_question"):
        return _service.describe_scene(str(params.get("text", "")))
    if action in ("register_face", "enroll_face"):
        name = params.get("name") or params.get("text") or params.get("person") or ""
        return _service.register_face(str(name))
    if action in ("identify_face", "recognize_face", "verify_identity"):
        return _service.identify_face()
    if action in ("multimodal", "vision_ai"):
        return _service.ask_multimodal(str(params.get("text", "Describe this scene.")))
    if action == "snapshot":
        return _service.snapshot(params.get("text", ""))
    if action in ("status", "info"):
        if "sensitivity" in params:
            try:
                _service._motion_sensitivity = _clamp(float(params["sensitivity"]), 0.001, 1.0)
            except (TypeError, ValueError):
                pass
        if "grace" in params:
            try:
                _service._focus_grace = max(0.0, float(params["grace"]))
            except (TypeError, ValueError):
                pass
        return _service.status()
    return (
        f"Unknown vision action: {action}, sir. Use start | stop | mode | "
        "face_count | qr | ocr | snapshot | status"
    )
