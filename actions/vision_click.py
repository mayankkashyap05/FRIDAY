# actions/vision_click.py
# Friday — click anywhere on screen by describing it (vision → coordinates)
#
# Gemini's native 0..1000 spatial grounding (box_2d / point) is mapped onto the
# selected monitor with Windows logical-vs-physical DPI scaling. Legacy pixel
# JSON and the original helper signatures remain supported.

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

from config.models import MODEL_FLASH

try:
    import mss
    _MSS = True
except ImportError:                                    # pragma: no cover - environment
    mss = None
    _MSS = False

try:
    from PIL import Image
    _PIL = True
except ImportError:                                    # pragma: no cover - environment
    Image = None
    _PIL = False

MAX_DIM = 1280
JPEG_QUALITY = 88


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = get_base_dir() / "config" / "api_keys.json"


def _get_api_key() -> str:
    """Legacy helper kept for compatibility; generation uses api_keys directly."""
    from api_keys import next_key
    return next_key()


def _mss_session():
    """Prefer the non-deprecated mss.MSS factory when available."""
    factory = getattr(mss, "MSS", None) or mss.mss
    return factory()


def _monitor_geometry(monitor=1) -> dict:
    """Return the physical geometry of the chosen mss monitor.

    monitor=0 is the virtual desktop spanning all displays; 1 is primary.
    """
    if _MSS:
        try:
            with _mss_session() as sct:
                monitors = sct.monitors
                if monitors:
                    index = 0 if int(monitor) == 0 else max(
                        1, min(int(monitor), len(monitors) - 1)
                    )
                    area = monitors[index]
                    return {
                        "left": int(area.get("left", 0)),
                        "top": int(area.get("top", 0)),
                        "width": int(area.get("width", 0)),
                        "height": int(area.get("height", 0)),
                    }
        except Exception:
            pass
    return {"left": 0, "top": 0, "width": 0, "height": 0}


def _screenshot_jpeg(monitor=1) -> tuple:
    """Capture one monitor and downscale it to a crisp <=1280px JPEG.

    Returns ``(bytes, orig_w, orig_h, img_w, img_h, left, top, width, height)``.
    """
    if not _MSS:
        raise RuntimeError("mss is not installed.")
    geometry = _monitor_geometry(monitor)
    try:
        with _mss_session() as sct:
            area = {
                "left": geometry["left"], "top": geometry["top"],
                "width": geometry["width"] or 1, "height": geometry["height"] or 1,
            }
            shot = sct.grab(area)
            orig_w, orig_h = shot.size
            png_bytes = mss.tools.to_png(shot.rgb, shot.size)
    except Exception as exc:
        raise RuntimeError(f"Screen capture failed: {exc}") from exc

    if _PIL:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        img.thumbnail([MAX_DIM, MAX_DIM], Image.BILINEAR)
        img_w, img_h = img.size
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        image_bytes = buf.getvalue()
    else:
        img_w, img_h = orig_w, orig_h
        image_bytes = png_bytes
    return (
        image_bytes, orig_w, orig_h, img_w, img_h,
        geometry["left"], geometry["top"], geometry["width"], geometry["height"],
    )


def _json_config():
    """GenerateContentConfig with a dict fallback for older google-genai."""
    try:
        from google.genai import types
        return types.GenerateContentConfig(response_mime_type="application/json")
    except Exception:
        return {"response_mime_type": "application/json"}


def _parse_json(text: str) -> dict | None:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _find_coordinates(image_bytes: bytes, target: str) -> tuple | dict:
    """Ask Gemini for native 0..1000 grounding, with legacy pixel fallback."""
    from api_keys import generate_with_retry

    try:
        from google.genai import types as gtypes
        image_part = gtypes.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    except Exception:
        image_part = {"inline_data": {"mime_type": "image/jpeg", "data": image_bytes}}

    prompt = (
        "You are Friday's screen-click module. Locate the UI element described "
        "by the user using Gemini's native spatial understanding. Coordinates "
        "MUST be normalized integers from 0 to 1000 in [y, x] order.\n"
        "Return ONLY JSON in this exact shape:\n"
        '{"found": true, "box_2d": [y_min, x_min, y_max, x_max], '
        '"point": [y, x], "label": "short element name", "confidence": 0.9}\n'
        "If the element is not visible, return "
        '{"found": false, "label": "NOT_FOUND"}.\n\n'
        f"User wants to click on: \"{target}\""
    )
    try:
        response = generate_with_retry(
            model=MODEL_FLASH,
            contents=[image_part, prompt],
            config=_json_config(),
        )
    except Exception as exc:
        raise RuntimeError(f"Vision call failed: {exc}") from exc

    text = (getattr(response, "text", None) or "").strip()
    data = _parse_json(text)
    if data is None:
        # Backward compatibility: {"x": 123, "y": 456} pixel responses.
        match = re.search(
            r'\{\s*"x"\s*:\s*(-?\d+)\s*,\s*"y"\s*:\s*(-?\d+)\s*\}',
            text, re.IGNORECASE,
        )
        if not match:
            raise RuntimeError(f"Could not locate the element: '{target}'.")
        return int(match.group(1)), int(match.group(2))
    return data


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve(result, orig_w: int, orig_h: int, img_w: int, img_h: int) -> dict | None:
    """Resolve a grounding result into coordinates plus metadata."""
    box = None
    label = ""
    if isinstance(result, (tuple, list)) and len(result) >= 2:
        px, py = _number(result[0]), _number(result[1])
        if px is None or py is None:
            return None
        if orig_w > 0 and img_w > 0:
            px = round(px * orig_w / img_w)
        if orig_h > 0 and img_h > 0:
            py = round(py * orig_h / img_h)
        return {"kind": "pixel", "x": px, "y": py, "label": label, "box": box}

    if not isinstance(result, dict):
        return None
    label = str(result.get("label") or "").strip()
    if result.get("found") is False or label.upper() == "NOT_FOUND":
        return None

    raw_box = result.get("box_2d") or result.get("box")
    if isinstance(raw_box, (list, tuple)) and len(raw_box) >= 4:
        values = [_number(v) for v in raw_box[:4]]
        if all(value is not None for value in values):
            box = values

    norm_y = norm_x = None
    point = result.get("point")
    if isinstance(point, (list, tuple)) and len(point) >= 2:
        py, px = _number(point[0]), _number(point[1])
        if py is not None and px is not None:
            inside = True
            if box:
                inside = (box[0] - 1 <= py <= box[2] + 1
                          and box[1] - 1 <= px <= box[3] + 1)
            if inside:
                norm_y, norm_x = py, px

    if norm_x is None and box:
        norm_y = (box[0] + box[2]) / 2.0
        norm_x = (box[1] + box[3]) / 2.0

    if norm_x is not None:
        if 0 <= norm_x <= 1000 and 0 <= norm_y <= 1000:
            return {"kind": "normalized", "x": norm_x, "y": norm_y,
                    "label": label, "box": box}

    # Last resort: legacy pixel fields inside the JSON object.
    px, py = _number(result.get("x")), _number(result.get("y"))
    if px is not None and py is not None:
        if orig_w > 0 and img_w > 0:
            px = round(px * orig_w / img_w)
        if orig_h > 0 and img_h > 0:
            py = round(py * orig_h / img_h)
        return {"kind": "pixel", "x": px, "y": py, "label": label, "box": box}
    return None


def _scale_factors(geometry: dict) -> tuple[float, float]:
    """Logical-to-physical ratio for pyautogui on DPI-scaled Windows displays."""
    if not _MSS:
        return 1.0, 1.0
    try:
        primary = _monitor_geometry(1)
        import pyautogui
        screen_w, screen_h = pyautogui.size()
        scale_x = float(screen_w) / float(primary["width"])
        scale_y = float(screen_h) / float(primary["height"])
        return (
            min(4.0, max(0.25, scale_x)),
            min(4.0, max(0.25, scale_y)),
        )
    except Exception:
        return 1.0, 1.0


def _to_screen(point: dict, geometry: dict, scale_x: float,
               scale_y: float) -> tuple[int, int]:
    left = geometry.get("left", 0) * scale_x
    top = geometry.get("top", 0) * scale_y
    width = geometry.get("width", 0) * scale_x
    height = geometry.get("height", 0) * scale_y
    if point["kind"] == "normalized":
        x = left + (point["x"] / 1000.0) * width
        y = top + (point["y"] / 1000.0) * height
    else:
        x = left + point["x"] * scale_x
        y = top + point["y"] * scale_y
    if width > 0:
        x = min(max(round(x), round(left)), round(left + width) - 1)
    if height > 0:
        y = min(max(round(y), round(top)), round(top + height) - 1)
    return int(x), int(y)


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _click_mode(params: dict) -> str:
    raw = str(
        params.get("click") or params.get("button") or params.get("action") or "left"
    ).strip().lower()
    aliases = {
        "single": "left", "1": "left", "double": "double", "2": "double",
        "twice": "double", "triple": "triple", "3": "triple",
        "context": "right", "move": "hover",
    }
    return aliases.get(raw, raw)


def vision_click_action(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Click a UI element on screen by describing it."""
    params = parameters if isinstance(parameters, dict) else {}
    target = str(params.get("target", params.get("what", ""))).strip()
    if not target:
        return ("Tell me what to click on the screen, sir — e.g. 'the Send "
                "button', 'the search box'.")

    raw_monitor = params.get("monitor", 1)
    try:
        monitor = 1 if raw_monitor is None else int(raw_monitor)
    except (TypeError, ValueError):
        monitor = 1
    monitor = max(0, monitor)
    only_coords = _as_bool(params.get("only_coords", False))
    mode = _click_mode(params)
    type_text = str(params.get("type_text") or params.get("text") or "").strip()
    press_enter = _as_bool(params.get("press_enter", False))

    try:
        # Default monitor uses the original zero-arg helper call so existing
        # integrations/mocks that patch _screenshot_jpeg() keep working.
        shot = _screenshot_jpeg() if monitor == 1 else _screenshot_jpeg(monitor)
        if not isinstance(shot, tuple):
            raise RuntimeError("Screenshot helper returned an invalid result")
        if len(shot) >= 9:
            (img, orig_w, orig_h, img_w, img_h,
             left, top, mon_w, mon_h) = shot[:9]
            geometry = {"left": left, "top": top, "width": mon_w, "height": mon_h}
        elif len(shot) == 5:
            img, orig_w, orig_h, img_w, img_h = shot
            geometry = {"left": 0, "top": 0, "width": orig_w, "height": orig_h}
        else:
            raise RuntimeError("Screenshot helper returned an invalid result")

        grounded = _find_coordinates(img, target)
        point = _resolve(grounded, orig_w, orig_h, img_w, img_h)
        if point is None:
            return (
                f"I couldn't find '{target}' on the screen, sir. "
                "Nothing was clicked."
            )
        scale_x, scale_y = _scale_factors(geometry)
        x, y = _to_screen(point, geometry, scale_x, scale_y)
    except Exception as exc:
        return f"Vision click failed: {exc}"

    label = point.get("label") or target
    if only_coords:
        detail = f"Found '{target}' at screen coordinates ({x}, {y}), sir."
        if label and label != target:
            detail += f" Label: {label}."
        if point.get("box"):
            detail += " Box: " + str([round(v, 1) for v in point["box"]]) + "."
        return detail

    try:
        import pyautogui
        if mode == "hover":
            pyautogui.moveTo(x, y)
        elif mode == "double":
            pyautogui.doubleClick(x, y)
        elif mode == "triple":
            if hasattr(pyautogui, "tripleClick"):
                pyautogui.tripleClick(x, y)
            else:
                for _ in range(3):
                    pyautogui.click(x, y)
        elif mode == "right":
            pyautogui.click(x, y, button="right")
        elif mode == "middle":
            pyautogui.click(x, y, button="middle")
        else:
            pyautogui.click(x, y)

        extra = ""
        if type_text:
            try:
                pyautogui.write(type_text, interval=0.01)
                extra += f" Typed: {type_text[:80]}."
            except Exception:
                extra += " Typing failed."
        if press_enter:
            try:
                pyautogui.press("enter")
                extra += " Pressed Enter."
            except Exception:
                extra += " Enter press failed."
        verb = "Moved to" if mode == "hover" else f"{mode.capitalize()}-clicked"
        return f"{verb} '{target}' at ({x}, {y}), sir.{extra}"
    except Exception as exc:
        return f"Found '{target}' at ({x}, {y}) but the click failed: {exc}"
