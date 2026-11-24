# actions/screen_processor.py
# Friday — crisp screen/window/camera capture with multi-key Gemini vision.
#
# Optional native packages (mss, cv2, PIL) are guarded so this module imports
# cleanly on Windows and Linux. No config file is ever rewritten by camera
# auto-detection — the detected index is kept in memory.

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

try:
    import PIL.Image
    _PIL_OK = True
except ImportError:                                    # pragma: no cover - environment
    PIL = None
    _PIL_OK = False

try:
    import cv2
    _CV2_OK = True
except Exception:                                      # pragma: no cover - environment
    cv2 = None
    _CV2_OK = False

try:
    import mss
    import mss.tools
    _MSS_OK = True
except Exception:                                      # pragma: no cover - environment
    mss = None
    _MSS_OK = False

from api_keys import generate_with_retry
from config.models import MODEL_FLASH, MODEL_FLASH_LITE


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

#: Back-compat constants now describe the default "balanced" preset.
IMG_MAX_W, IMG_MAX_H, JPEG_Q = 1280, 720, 80
DETAIL_PRESETS = {
    "fast": (800, 450, 68),
    "balanced": (1280, 720, 80),
    "high": (1920, 1080, 88),
    "ocr": (1920, 1080, 88),
}

VISION_MODEL = os.environ.get("FRIDAY_VISION_MODEL") or MODEL_FLASH
_CAMERA_INDEX_CACHE: dict[str, int] = {}
_SHARED_CAMERA_CHECK = True


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _preset(name) -> tuple[int, int, int]:
    return DETAIL_PRESETS.get(str(name or "balanced").strip().lower(), DETAIL_PRESETS["balanced"])


def _is_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "quota" in text or "resource_exhausted" in text


def _generate(contents, model: str = VISION_MODEL) -> str:
    try:
        resp = generate_with_retry(model=model, contents=contents, api_version="v1beta")
        return (getattr(resp, "text", None) or "").strip()
    except Exception as exc:
        if model != MODEL_FLASH_LITE and _is_quota_error(exc):
            resp = generate_with_retry(model=MODEL_FLASH_LITE, contents=contents,
                                       api_version="v1beta")
            return (getattr(resp, "text", None) or "").strip()
        raise


def _get_camera_index(requested=None) -> int:
    """Resolve a camera index from the request or a small in-memory cache."""
    if requested is not None:
        try:
            index = int(requested)
            _CAMERA_INDEX_CACHE["last"] = index
            return index
        except (TypeError, ValueError):
            pass
    if "last" in _CAMERA_INDEX_CACHE:
        return _CAMERA_INDEX_CACHE["last"]
    # Never touch config/api_keys.json: a corrupt write there loses API keys.
    _CAMERA_INDEX_CACHE["last"] = 0
    return 0


def _to_jpeg(img_bytes: bytes, detail="balanced") -> bytes:
    max_w, max_h, quality = _preset(detail)
    if not _PIL_OK:
        return img_bytes
    img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img.thumbnail((max_w, max_h), PIL.Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _frame_to_jpeg(frame, detail="balanced") -> bytes:
    max_w, max_h, quality = _preset(detail)
    if _PIL_OK and _CV2_OK:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(rgb)
        img.thumbnail((max_w, max_h), PIL.Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()
    if _CV2_OK:
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes()
    raise RuntimeError("Neither PIL nor OpenCV is available to encode a frame.")


def _active_window_rect() -> dict | None:
    """Foreground window rectangle on Windows; None when it cannot be read."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return {"left": rect.left, "top": rect.top,
                "width": max(1, rect.right - rect.left),
                "height": max(1, rect.bottom - rect.top)}
    except Exception:
        return None


def _clamp_area(area: dict, monitor: dict) -> dict:
    left = max(int(monitor["left"]), int(area.get("left", monitor["left"])))
    top = max(int(monitor["top"]), int(area.get("top", monitor["top"])))
    right = min(int(monitor["left"] + monitor["width"]),
                int(area.get("left", monitor["left"]) + area.get("width", monitor["width"])))
    bottom = min(int(monitor["top"] + monitor["height"]),
                 int(area.get("top", monitor["top"]) + area.get("height", monitor["height"])))
    return {"left": left, "top": top, "width": max(1, right - left),
            "height": max(1, bottom - top)}


def _capture_screenshot(monitor=1, region=None, window=False, detail="balanced") -> bytes:
    if not _MSS_OK:
        raise RuntimeError("mss is not installed. Run: pip install mss")
    factory = getattr(mss, "MSS", None) or getattr(mss, "mss", None)
    if factory is None:
        raise RuntimeError("mss screen capture is unavailable")
    with factory() as sct:
        monitors = getattr(sct, "monitors", [])
        if not monitors:
            raise RuntimeError("No display monitors detected")
        try:
            index = int(monitor if monitor is not None else 1)
        except (TypeError, ValueError):
            index = 1
        index = min(max(0, index), len(monitors) - 1)
        base = monitors[index]
        area = dict(base)
        if _as_bool(window):
            rect = _active_window_rect()
            if rect:
                area = _clamp_area(rect, base)
        elif region is not None:
            try:
                x, y, width, height = (int(v) for v in region)
                if width > 0 and height > 0:
                    area = _clamp_area({"left": x, "top": y, "width": width,
                                        "height": height}, base)
            except (TypeError, ValueError):
                pass
        shot = sct.grab(area)
        if hasattr(mss.tools, "to_png"):
            raw = mss.tools.to_png(shot.rgb, shot.size)
        else:
            raw = shot.png
    mime = "image/jpeg" if _PIL_OK else "image/png"
    return (_to_jpeg(raw, detail), mime)


def _shared_camera_frame():
    """Use vision_gesture's live frame instead of opening a second camera."""
    if not _SHARED_CAMERA_CHECK:
        return None
    try:
        from actions import vision_gesture
        service = getattr(vision_gesture, "_service", None)
        if service is None or not getattr(service, "_running", False):
            return None
        camera = getattr(service, "_camera", None)
        if camera is None:
            return None
        packet = camera.latest()
        return getattr(packet, "frame", None) if packet is not None else None
    except Exception:
        return None


def _capture_camera(camera_index=None, detail="balanced") -> bytes:
    frame = _shared_camera_frame()
    if frame is not None:
        return _frame_to_jpeg(frame, detail)
    if not _CV2_OK:
        raise RuntimeError("OpenCV is not installed. Run: pip install opencv-python")
    index = _get_camera_index(camera_index)
    backend = getattr(cv2, "CAP_DSHOW", None) if os.name == "nt" else None
    cap = None
    try:
        cap = cv2.VideoCapture(index, backend) if backend is not None else cv2.VideoCapture(index)
        if not cap.isOpened():
            raise RuntimeError(f"Camera could not be opened: index {index}")
        for _ in range(5):
            cap.read()
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError("Could not capture camera frame.")
        return _frame_to_jpeg(frame, detail)
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def _analyze_image_text(
    image_bytes: bytes,
    mime_type: str,
    user_text: str,
    model: str = VISION_MODEL,
    concise: bool = True,
) -> str:
    """Send image + question to Gemini and return the text answer."""
    from google.genai import types
    style = ("Answer in max 2-3 short sentences" if concise else
             "Give a thorough, well-organised answer with every visible detail")
    prompt = (
        "You are Friday's vision module. Analyze the attached image and answer "
        f"the user's question. Be accurate, address the user as 'sir'. {style}.\n\n"
        f"User's question: {user_text}"
    )
    try:
        answer = _generate([
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ], model=model)
        return answer or "I looked at the image but couldn't make out an answer, sir."
    except Exception as e:
        return f"Vision analysis failed: {e}"


def screen_process(
    parameters: dict,
    response: str | None = None,
    player=None,
    session_memory=None,
) -> str:
    """Capture the screen, active window, a region or the camera and analyse it."""
    params = parameters if isinstance(parameters, dict) else {}
    user_text = str(params.get("text") or params.get("user_text") or "").strip()
    if not user_text:
        return "No question provided for screen analysis, sir."

    angle = str(params.get("angle") or "screen").strip().lower()
    detail = str(params.get("detail") or "balanced").strip().lower()
    if detail not in DETAIL_PRESETS:
        detail = "balanced"
    concise = _as_bool(params.get("concise", True))
    print(f"[ScreenProcess] angle={angle!r} detail={detail!r} text={user_text!r}")

    try:
        if angle == "camera":
            image_bytes = _capture_camera(params.get("camera_index"), detail)
            mime_type = "image/jpeg"
        else:
            image_bytes, mime_type = _capture_screenshot(
                monitor=params.get("monitor", 1),
                region=params.get("region"),
                window=angle in ("window", "active_window"),
                detail=detail,
            )
    except Exception as e:
        print(f"[ScreenProcess] Capture error: {e}")
        return f"Screen capture failed: {e}"

    save_path = params.get("save_path")
    if save_path:
        try:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            Path(save_path).write_bytes(image_bytes)
        except Exception as e:
            print(f"[ScreenProcess] Could not save capture: {e}")

    answer = _analyze_image_text(image_bytes, mime_type, user_text, concise=concise)
    if player:
        try:
            player.write_log(f"Friday: {answer}")
        except Exception:
            pass
    if session_memory:
        try:
            session_memory.set_last_search(query=f"screen: {user_text}", response=answer)
        except Exception:
            pass
    return answer


if __name__ == "__main__":
    print("[TEST] screen_processor.py — crisp image-only analysis")
    mode = input("screen / window / camera (default: screen): ").strip().lower() or "screen"
    question = input("Question (Enter for default): ").strip() or "What do you see? Be brief."
    t0 = time.perf_counter()
    print(screen_process({"angle": mode, "text": question}, player=None))
    print(f"Analyzed in {time.perf_counter() - t0:.3f}s")
