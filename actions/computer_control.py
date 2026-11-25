# actions/computer_control.py
# Friday — Computer Control
#
# Atomic computer control functions using PyAutoGUI + keyboard + clipboard.
# Used by the agent when no existing action file covers the task.
#
# Capabilities:
#   - Type text anywhere (active window, forms, fields)
#   - Mouse click, double-click, right-click, drag
#   - Keyboard shortcuts and key combinations
#   - Scroll (up/down/left/right)
#   - Window management (minimize, maximize, close, focus)
#   - Clipboard (copy, paste, get content)
#   - Screenshot + locate element on screen
#   - Wait / smart wait for element to appear
#   - Random data generation (name, email, username, password, phone, address)
#   - Hotkey sequences
#   - Find and click image/element on screen

import base64
import json
import re
import secrets
import sys
import time

from config.models import MODEL_FLASH_LITE
import random
import string
import subprocess
import platform
from pathlib import Path

_PG_MOD = None


def _pg():
    """Lazily import pyautogui; returns the module or None."""
    global _PG_MOD
    if _PG_MOD is None:
        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE    = 0.05
            _PG_MOD = pyautogui
        except ImportError:
            _PG_MOD = False
    return _PG_MOD or None

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _load_user_profile() -> dict:
    """Load user profile from long_term.json for form filling."""
    memory_path = BASE_DIR / "memory" / "long_term.json"
    try:
        if memory_path.exists():
            data = json.loads(memory_path.read_text(encoding="utf-8"))
            identity = data.get("identity", {})
            return {
                "name":  identity.get("name",  {}).get("value", ""),
                "age":   identity.get("age",   {}).get("value", ""),
                "city":  identity.get("city",  {}).get("value", ""),
                "email": identity.get("email", {}).get("value", ""),
            }
    except Exception:
        pass
    return {}


def _ensure_pyautogui():
    if _pg() is None:
        raise RuntimeError(
            "PyAutoGUI not installed. Run: pip install pyautogui"
        )


def _screen_size_tuple() -> tuple[int, int] | None:
    try:
        pg = _pg()
        if pg is None:
            return None
        width, height = pg.size()
        return max(1, int(width)), max(1, int(height))
    except Exception:
        return None


def _clamp_number(value, low: float, high: float, default: float = 0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clamp_coord(value, axis: str) -> int:
    size = _screen_size_tuple()
    limit = (size[0] if axis == "x" else size[1]) if size else 3840 if axis == "x" else 2160
    return int(_clamp_number(value, 0, max(0, limit - 1), 0))


_FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Drew", "Quinn",
    "Avery", "Blake", "Cameron", "Dakota", "Emerson", "Finley", "Harper"
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Moore", "Taylor", "Anderson", "Thomas", "Jackson"
]
_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "proton.me", "mail.com"]


def _cryptographic_choice(sequence):
    """Single character chosen with the OS CSPRNG (never ``random``)."""
    return secrets.choice(sequence)


def generate_random_data(data_type: str, locale: str = "") -> str:
    """
    Generates random realistic data for form filling.

    Types: name | first_name | last_name | email | username |
           password | phone | birthday | address | zip_code

    ``locale="IN"`` (also accepted as ``password_IN`` / ``password|IN``)
    produces a longer, India-flavoured password.  Passwords always use
    :mod:`secrets`, never the Mersenne Twister used for fake profile data.
    """
    raw = str(data_type or "").strip()
    locale = str(locale or "").strip()
    dt = raw.lower()
    for separator in ("|", "_", ":"):
        head, sep, tail = dt.partition(separator)
        if sep and head == "password" and tail in ("in", "india", "ind"):
            dt, locale = "password", "IN"
            break

    if dt == "first_name":
        return random.choice(_FIRST_NAMES)

    elif dt == "last_name":
        return random.choice(_LAST_NAMES)

    elif dt == "name":
        return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"

    elif dt == "email":
        first = random.choice(_FIRST_NAMES).lower()
        last  = random.choice(_LAST_NAMES).lower()
        num   = random.randint(10, 999)
        return f"{first}.{last}{num}@{random.choice(_DOMAINS)}"

    elif dt == "username":
        first = random.choice(_FIRST_NAMES).lower()
        num   = random.randint(100, 9999)
        return f"{first}{num}"

    elif dt == "password":
        specials = "!@#$%"
        length = 12
        if str(locale).strip().upper() in ("IN", "IND", "INDIA"):
            # Indian locale: longer, richer alphabet; still pasted safely via
            # the Unicode-safe clipboard path below.
            specials = "!@#$%&₹"
            length = 16
        chars = string.ascii_letters + string.digits + specials
        pwd = [
            _cryptographic_choice(string.ascii_uppercase),
            _cryptographic_choice(string.ascii_lowercase),
            _cryptographic_choice(string.digits),
            _cryptographic_choice(specials),
        ]
        pwd += [_cryptographic_choice(chars) for _ in range(length - len(pwd))]
        # Fisher-Yates with the CSPRNG, so the required character classes do
        # not stay in predictable positions.
        for idx in range(len(pwd) - 1, 0, -1):
            swap = secrets.randbelow(idx + 1)
            pwd[idx], pwd[swap] = pwd[swap], pwd[idx]
        return "".join(pwd)

    elif dt == "phone":
        return f"+1{random.randint(200,999)}{random.randint(1000000,9999999)}"

    elif dt == "birthday":
        year  = random.randint(1980, 2000)
        month = random.randint(1, 12)
        day   = random.randint(1, 28)
        return f"{month:02d}/{day:02d}/{year}"

    elif dt == "address":
        num    = random.randint(100, 9999)
        street = random.choice(["Main St", "Oak Ave", "Park Blvd", "Elm St", "Cedar Ln"])
        return f"{num} {street}"

    elif dt == "zip_code":
        return str(random.randint(10000, 99999))

    elif dt == "city":
        return random.choice(["New York", "Los Angeles", "Chicago", "Houston", "Phoenix"])

    return f"random_{data_type}_{random.randint(1000,9999)}"


def _needs_paste(text: str) -> bool:
    """Unicode, newlines and long strings cannot be typed key-by-key."""
    if not isinstance(text, str):
        return False
    return (not text.isascii()) or "\n" in text or "\r" in text or len(text) > 20


def _paste_with_restore(text: str) -> str:
    """Paste ``text`` via the clipboard, restoring the old clipboard in finally."""
    if not _PYPERCLIP:
        return "pyperclip not available"
    previous = None
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None
    try:
        pyperclip.copy(str(text))
        time.sleep(0.05)
        _hotkey("ctrl", "v")
        return f"Pasted: {str(text)[:50]}"
    finally:
        if previous is not None:
            try:
                pyperclip.copy(previous)
            except Exception:
                pass


def _type_text(text: str, interval: float = 0.03) -> str:
    """Types text at the current cursor position (Unicode-safe)."""
    _ensure_pyautogui()
    text = str(text or "")
    time.sleep(0.2)
    if _needs_paste(text) and _PYPERCLIP:
        return _paste_with_restore(text)
    _pg().typewrite(text, interval=max(0.0, min(float(interval), 0.5)))
    return f"Typed: {text[:50]}{'...' if len(text) > 50 else ''}"


def _click(x: int = None, y: int = None, button: str = "left",
           clicks: int = 1, image: str = None) -> str:
    """
    Clicks at coordinates or on a screen image.
    If image path given, locates it on screen and clicks.
    """
    _ensure_pyautogui()

    if image:
        try:
            loc = _pg().locateCenterOnScreen(image, confidence=0.8)
            if loc:
                _pg().click(loc.x, loc.y, button=button, clicks=clicks)
                return f"Clicked image: {image}"
            return f"Image not found on screen: {image}"
        except Exception as e:
            return f"Image click failed: {e}"

    if x is not None and y is not None:
        x, y = _clamp_coord(x, "x"), _clamp_coord(y, "y")
        _pg().click(x, y, button=button, clicks=clicks)
        return f"Clicked ({x}, {y}) with {button} button"

    _pg().click(button=button, clicks=clicks)
    return "Clicked at current position"


_HOTKEY_ALIASES = {
    "control": "ctrl", "ctl": "ctrl", "cmd": "win", "command": "win",
    "meta": "win", "super": "win", "return": "enter", "esc": "esc",
    "escape": "esc", "del": "delete", "spacebar": "space", "space_bar": "space",
    "pageup": "pageup", "pagedown": "pagedown", "shiftkey": "shift",
}


def _normalise_hotkey(keys) -> tuple[str, ...]:
    """Accept lists or 'ctrl+shift+t' style strings and normalise aliases."""
    if isinstance(keys, str):
        parts = [part for part in re.split(r"[+,]", keys) if part.strip()]
    else:
        parts = []
        for key in keys or ():
            parts.extend(p for p in re.split(r"\s*\+\s*", str(key)) if p.strip())
    normalised = []
    for part in parts:
        token = str(part).strip().lower()
        token = _HOTKEY_ALIASES.get(token, token)
        # The new Google keyboard uses 'meta' on macOS / 'win' on Windows;
        # pyautogui expects the platform name.
        if token == "win" and platform.system() != "Windows":
            token = "super"
        normalised.append(token)
    return tuple(normalised)


def _hotkey(*keys) -> str:
    """Presses a key combination. E.g. hotkey('ctrl', 'c')"""
    _ensure_pyautogui()
    normalised = _normalise_hotkey(keys)
    _pg().hotkey(*normalised)
    return f"Hotkey: {'+'.join(normalised)}"


def _press(key: str) -> str:
    """Presses a single key."""
    _ensure_pyautogui()
    _pg().press(key)
    return f"Pressed: {key}"


def _scroll(direction: str = "down", amount: int = 3) -> str:
    """Scrolls in the specified direction (bounded to a sane number of ticks)."""
    _ensure_pyautogui()
    amount = int(_clamp_number(amount, 1, 5000, 3))
    direction = str(direction or "down").lower().strip()
    clicks = amount if direction in ("up", "right") else -amount
    if direction in ("up", "down"):
        _pg().scroll(clicks)
    else:
        _pg().hscroll(clicks)
    return f"Scrolled {direction} {amount} times"


def _move_mouse(x: int, y: int, duration: float = 0.3) -> str:
    """Moves mouse to coordinates, clamped to the logical screen."""
    _ensure_pyautogui()
    x, y = _clamp_coord(x, "x"), _clamp_coord(y, "y")
    duration = _clamp_number(duration, 0.0, 5.0, 0.3)
    _pg().moveTo(x, y, duration=duration)
    return f"Mouse moved to ({x}, {y})"


def _drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5) -> str:
    """Drags from (x1,y1) to (x2,y2); both endpoints stay on-screen."""
    _ensure_pyautogui()
    x1, y1 = _clamp_coord(x1, "x"), _clamp_coord(y1, "y")
    x2, y2 = _clamp_coord(x2, "x"), _clamp_coord(y2, "y")
    duration = _clamp_number(duration, 0.0, 5.0, 0.5)
    _pg().moveTo(x1, y1)
    _pg().dragTo(x2, y2, duration=duration)
    return f"Dragged from ({x1},{y1}) to ({x2},{y2})"


def _mouse_pos() -> str:
    """Reports the current cursor position."""
    _ensure_pyautogui()
    x, y = _pg().position()
    return f"Mouse position: ({int(x)}, {int(y)})"


def _clipboard_copy() -> str:
    """Gets current clipboard content."""
    if _PYPERCLIP:
        return pyperclip.paste()
    _hotkey("ctrl", "c")
    time.sleep(0.2)
    return "Copied to clipboard"


def _clipboard_set(text: str) -> str:
    """Sets clipboard content and pastes it."""
    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.1)
        _hotkey("ctrl", "v")
        return f"Pasted: {text[:50]}"
    return "pyperclip not available"


def _screenshot(save_path: str = None) -> str:
    """Takes a screenshot."""
    _ensure_pyautogui()
    if not save_path:
        save_path = str(Path.home() / "Desktop" / "screenshot.png")
    img = _pg().screenshot()
    img.save(save_path)
    return f"Screenshot saved: {save_path}"


def _wait(seconds: float) -> str:
    """Waits for specified seconds, bounded to 0..60 to avoid hung tools."""
    seconds = _clamp_number(seconds, 0.0, 60.0, 1.0)
    time.sleep(seconds)
    return f"Waited {seconds:g}s"


def _wait_for_image(image_path: str, timeout: int = 10) -> str:
    """Waits until an image appears on screen (up to timeout seconds)."""
    _ensure_pyautogui()
    start = time.time()
    while time.time() - start < timeout:
        try:
            loc = _pg().locateCenterOnScreen(image_path, confidence=0.8)
            if loc:
                return f"Image found at ({loc.x}, {loc.y})"
        except Exception:
            pass
        time.sleep(0.5)
    return f"Image not found within {timeout}s: {image_path}"


def _get_screen_size() -> str:
    """Returns current screen resolution."""
    _ensure_pyautogui()
    w, h = _pg().size()
    return f"{w}x{h}"


def _native_user32():
    """Returns ctypes.windll.user32 on Windows, otherwise None (never imports ctypes on Linux paths that matter)."""
    if platform.system() != "Windows":
        return None
    try:
        import ctypes
        return ctypes.windll.user32
    except Exception:
        return None


def _enum_windows_native() -> list[tuple[int, str]]:
    """Enumerate visible top-level windows as ``(hwnd, title)`` pairs."""
    user32 = _native_user32()
    if user32 is None:
        return []
    import ctypes
    found: list[tuple[int, str]] = []
    skip = {"", "Program Manager", "Default IME"}
    proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @proc_type
    def _visit(hwnd, _unused):
        try:
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value.strip()
                    if title and title not in skip:
                        found.append((int(hwnd), title))
        except Exception:
            pass
        return True

    user32.EnumWindows(_visit, None)
    return found


def _find_window(title: str):
    """Find a visible window by exact (preferred) or substring title match."""
    needle = str(title or "").strip().lower()
    if not needle:
        return None
    windows = _enum_windows_native()
    for hwnd, name in windows:
        if name.lower() == needle:
            return hwnd, name
    for hwnd, name in windows:
        if needle in name.lower():
            return hwnd, name
    return None


def _focus_window(title: str) -> str:
    """Brings a window to the foreground via native Win32 APIs."""
    if platform.system() == "Windows":
        match = _find_window(title)
        if match:
            try:
                user32 = _native_user32()
                hwnd, name = match
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)   # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.2)
                return f"Focused window: {name}"
            except Exception as e:
                return f"Could not focus window: {e}"
        try:
            # Single-quote the title and double embedded quotes so a malicious
            # title can never break out of the PowerShell script (fallback).
            safe_title = str(title or "").replace("'", "''")
            script = f"(New-Object -ComObject WScript.Shell).AppActivate('{safe_title}')"
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True, timeout=5
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except Exception as e:
            return f"Could not focus window: {e}"
    return "Window focus only supported on Windows"


def _list_windows() -> str:
    """Enumerates open top-level window titles (Windows)."""
    titles = [title for _, title in _enum_windows_native()]
    if not titles:
        return "Window listing only supported on Windows" if platform.system() != "Windows" else "No windows detected."
    return "Open windows: " + " | ".join(list(dict.fromkeys(titles))[:15])


def _active_window() -> str:
    """Reports the currently focused window title."""
    user32 = _native_user32()
    if user32 is None:
        return "Active window only supported on Windows"
    try:
        import ctypes
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "No active window detected."
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return f"Active window: {buf.value.strip() or '(untitled)'}"
    except Exception as e:
        return f"Could not get active window: {e}"


_WINDOW_SW = {"minimize": 6, "minimize_all": 6, "maximize": 3, "restore": 9, "show": 9}


def _window_action(action: str, title: str = "") -> str:
    """Minimize / maximize / restore / close a window without a shell."""
    action = str(action or "").strip().lower()
    user32 = _native_user32()
    if user32 is None:
        return "Window management only supported on Windows"
    if action not in set(_WINDOW_SW) | {"close", "focus"}:
        return f"Unknown window_action: '{action}'"
    match = _find_window(title)
    if not match:
        return f"Window not found: {title}"
    hwnd, name = match
    try:
        if action == "focus":
            user32.SetForegroundWindow(hwnd)
        elif action == "close":
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        else:
            user32.ShowWindow(hwnd, _WINDOW_SW[action])
            if action in ("restore", "show", "maximize"):
                user32.SetForegroundWindow(hwnd)
        return f"{action.capitalize()}d window: {name}"
    except Exception as e:
        return f"Could not {action} window: {e}"


def _select_all() -> str:
    return _hotkey("ctrl", "a")


def _clear_field() -> str:
    """Selects all and deletes — clears an input field."""
    _hotkey("ctrl", "a")
    time.sleep(0.1)
    _press("delete")
    return "Field cleared"


def _smart_type(text: str, clear_first: bool = True) -> str:
    """Types into the focused field. Unicode/newline/long text goes via paste."""
    _ensure_pyautogui()
    text = str(text or "")

    if clear_first:
        _clear_field()
        time.sleep(0.1)

    if _needs_paste(text) and _PYPERCLIP:
        _paste_with_restore(text)
        return f"Smart-typed (clipboard): {text[:50]}"
    _pg().typewrite(text, interval=0.04)
    return f"Smart-typed: {text[:50]}"


def _parse_grounding_response(text: str, width: int, height: int) -> tuple[int, int] | None:
    """Parse model grounding output, supporting JSON 0..1000 and legacy ``x,y``.

    Modern responses are JSON (``{"x": 512, "y": 250}``) whose coordinates are
    normalised to a 0..1000 logical grid.  Older models returned raw
    ``x,y`` pixels; those are still accepted and clamped to the screen.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    raw = text.strip()
    if "NOT_FOUND" in raw.upper():
        return None
    # Be tolerant of ```json ... ``` fences.
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.S | re.I)
    if fenced:
        raw = fenced.group(1).strip()

    payload = None
    try:
        start, end = raw.index("{"), raw.rindex("}")
        payload = json.loads(raw[start:end + 1])
    except (ValueError, TypeError):
        payload = None

    if isinstance(payload, dict):
        # New normalised grid.
        if all(key in payload for key in ("x", "y")):
            try:
                x_norm = float(payload["x"])
                y_norm = float(payload["y"])
            except (TypeError, ValueError):
                return None
            # Explicit pixel overrides are useful for high-DPI callers.
            if "pixel_x" in payload or "pixel_y" in payload:
                try:
                    x_norm = float(payload.get("pixel_x", x_norm)) * 1000.0 / max(1, width)
                    y_norm = float(payload.get("pixel_y", y_norm)) * 1000.0 / max(1, height)
                except (TypeError, ValueError):
                    pass
            value = payload.get("found", True)
            if isinstance(value, str) and value.lower() in ("false", "no"):
                return None
            return (
                int(max(0, min(width - 1, round(x_norm * width / 1000.0)))),
                int(max(0, min(height - 1, round(y_norm * height / 1000.0)))),
            )
        # Also accept {"point": [x, y]} / nested point objects.
        point = payload.get("point")
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                return (
                    int(max(0, min(width - 1, round(float(point[0]) * width / 1000.0)))),
                    int(max(0, min(height - 1, round(float(point[1]) * height / 1000.0)))),
                )
            except (TypeError, ValueError):
                return None

    # Legacy raw-pixel format.
    match = re.search(r"(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)", raw)
    if match:
        return (
            int(max(0, min(width - 1, round(float(match.group(1)))))),
            int(max(0, min(height - 1, round(float(match.group(2)))))),
        )
    return None


def _analyze_screen_for_element(description: str) -> tuple[int, int] | None:
    """Screenshot + Gemini grounding → logical ``(x, y)`` pixel coordinates."""
    try:
        import io
        import api_keys

        _ensure_pyautogui()
        w, h = _screen_size_tuple() or (1920, 1080)
        img = _pg().screenshot()
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        prompt = (
            f"This is a screenshot of a {w}x{h} logical screen. "
            f"Locate: '{description}'. "
            "Return ONLY JSON: {\"x\": <0-1000>, \"y\": <0-1000>} using "
            "coordinates scaled to a 1000x1000 grid, or {\"found\": false}."
        )
        response = api_keys.generate_with_retry(
            MODEL_FLASH_LITE,
            [
                {"inline_data": {"mime_type": "image/png",
                                  "data": base64.b64encode(buf.getvalue()).decode("ascii")}},
                prompt,
            ],
            config={"temperature": 0},
        )
        return _parse_grounding_response(
            getattr(response, "text", "") or "", w, h
        )
    except Exception as e:
        print(f"[ComputerControl] ⚠️ Screen analysis failed: {e}")
    return None

def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")
    return bool(value) if value is not None else default


_SENSITIVE_PARAM_KEYS = {"text", "value", "password", "content", "message", "data"}


def _safe_params(parameters: dict) -> dict:
    """Return params with potentially sensitive values masked for console logs."""
    if not isinstance(parameters, dict):
        return {}
    out = {}
    for k, v in parameters.items():
        if isinstance(v, str) and k.lower() in _SENSITIVE_PARAM_KEYS and len(v) > 3:
            out[k] = v[:2] + "…"
        else:
            out[k] = v
    return out

def computer_control(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Universal computer control action.

    Actions:
      type          : Type text at current cursor position
      smart_type    : Clear field + type (clipboard-based for long text)
      click         : Click at coordinates or on image
      double_click  : Double click
      right_click   : Right click
      hotkey        : Key combination (e.g. ctrl+c)
      press         : Single key press
      scroll        : Scroll up/down/left/right
      move          : Move mouse to coordinates
      drag          : Drag from one point to another
      copy          : Get clipboard content
      paste         : Set and paste clipboard content
      screenshot    : Take a screenshot
      wait          : Wait N seconds
      wait_image    : Wait for image to appear on screen
      clear_field   : Select all + delete in current field
      focus_window  : Bring window to foreground
      screen_find   : AI-powered element finder — returns coordinates
      screen_click  : AI-powered element finder + click
      random_data   : Generate random data for forms (locale="IN" available)
      user_data     : Get user's real data from memory
      triple_click / middle_click / hover / mouse_pos
      active_window / window_action (minimize|maximize|restore|close|focus)
    """
    parameters = parameters if isinstance(parameters, dict) else {}
    action = str(parameters.get("action", "") or "").lower().strip()

    if not action:
        return "Please specify an action for computer_control, sir."

    if player:
        player.write_log(f"[Computer] {action}")

    print(f"[ComputerControl] ▶️ Action: {action}  Params: {_safe_params(parameters)}")

    try:
        if action == "type":
            text = parameters.get("text", "")
            return _type_text(text)

        elif action == "smart_type":
            text        = parameters.get("text", "")
            clear_first = _as_bool(parameters.get("clear_first"), True)
            return _smart_type(text, clear_first=clear_first)
        
        elif action in ("click", "left_click"):
            return _click(
                x=parameters.get("x"),
                y=parameters.get("y"),
                button="left",
                clicks=1,
                image=parameters.get("image")
            )

        elif action == "double_click":
            return _click(
                x=parameters.get("x"),
                y=parameters.get("y"),
                button="left",
                clicks=2,
                image=parameters.get("image")
            )

        elif action == "right_click":
            return _click(
                x=parameters.get("x"),
                y=parameters.get("y"),
                button="right",
                clicks=1
            )

        elif action == "triple_click":
            return _click(
                x=parameters.get("x"), y=parameters.get("y"),
                button="left", clicks=3, image=parameters.get("image")
            )

        elif action == "middle_click":
            return _click(
                x=parameters.get("x"), y=parameters.get("y"),
                button="middle", clicks=1, image=parameters.get("image")
            )

        elif action in ("move", "hover"):
            return _move_mouse(
                x=int(parameters.get("x", 0)),
                y=int(parameters.get("y", 0)),
                duration=float(parameters.get("duration", 0.3))
            )

        elif action in ("mouse_pos", "mouse_position", "cursor_position"):
            return _mouse_pos()

        elif action == "drag":
            return _drag(
                x1=int(parameters.get("x1", 0)),
                y1=int(parameters.get("y1", 0)),
                x2=int(parameters.get("x2", 0)),
                y2=int(parameters.get("y2", 0)),
                duration=float(parameters.get("duration", 0.5)),
            )

        elif action == "hotkey":
            keys = parameters.get("keys", "")
            if isinstance(keys, str):
                keys = [k.strip() for k in keys.split("+")]
            return _hotkey(*keys)

        elif action == "press":
            return _press(parameters.get("key", "enter"))

        elif action == "scroll":
            return _scroll(
                direction=parameters.get("direction", "down"),
                amount=int(parameters.get("amount", 3))
            )

        elif action == "copy":
            return _clipboard_copy()

        elif action == "paste":
            return _clipboard_set(parameters.get("text", ""))

        elif action == "screenshot":
            return _screenshot(parameters.get("path"))

        elif action == "wait":
            return _wait(float(parameters.get("seconds", 1.0)))

        elif action == "wait_image":
            return _wait_for_image(
                parameters.get("image", ""),
                timeout=int(parameters.get("timeout", 10))
            )

        elif action == "clear_field":
            return _clear_field()

        elif action == "focus_window":
            return _focus_window(parameters.get("title", ""))

        elif action in ("active_window", "current_window"):
            return _active_window()

        elif action == "window_action":
            return _window_action(
                parameters.get("window_action") or parameters.get("mode") or parameters.get("value", ""),
                parameters.get("title", ""),
            )

        elif action in ("list_windows", "windows", "what_is_open"):
            return _list_windows()

        elif action == "screen_size":
            return _get_screen_size()

        elif action == "screen_find":
            description = parameters.get("description", "")
            coords = _analyze_screen_for_element(description)
            if coords:
                return f"{coords[0]},{coords[1]}"
            return "NOT_FOUND"

        elif action == "screen_click":
            description = parameters.get("description", "")
            coords = _analyze_screen_for_element(description)
            if coords:
                time.sleep(0.2)
                _click(x=coords[0], y=coords[1])
                return f"Found and clicked: {description} at {coords}"
            return f"Could not find on screen: {description}"

        elif action == "random_data":
            data_type = parameters.get("type", "name")
            result    = generate_random_data(data_type, parameters.get("locale", ""))
            print(f"[ComputerControl] 🎲 Random {data_type} generated")
            return result

        elif action == "user_data":
            field   = parameters.get("field", "name")
            profile = _load_user_profile()
            value   = profile.get(field, "")
            if not value:
                value = generate_random_data(field)
                print(f"[ComputerControl] ⚠️ No user {field} in memory, generated random")
            return value

        else:
            return f"Unknown computer_control action: '{action}'"

    except Exception as e:
        print(f"[ComputerControl] ❌ Error: {e}")
        return f"computer_control failed: {e}"