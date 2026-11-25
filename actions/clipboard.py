# actions/clipboard.py
# Friday — lock-safe, secret-aware clipboard manager.

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote, unquote

import sys

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

try:
    BASE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
except Exception:
    BASE_DIR = Path(__file__).resolve().parent.parent

_HISTORY_LIMIT = 50
_PINNED_LIMIT = 50
_HISTORY: list[str] = []
_PINNED: list[str] = []
_history = _HISTORY                      # legacy alias kept for callers/tests
_CLIP_LOCK = threading.RLock()
_history_lock = _CLIP_LOCK               # legacy alias

_SECRET_PATTERNS = [
    r"AIza[0-9A-Za-z_\-]{20,}",
    r"sk-[0-9A-Za-z]{16,}",
    r"gh[pousr]_[0-9A-Za-z]{20,}",
    r"AKIA[0-9A-Z]{16}",
    r"xox[baprs]-[0-9A-Za-z-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
]
_SECRET_RE = re.compile("|".join(_SECRET_PATTERNS), re.IGNORECASE)


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")
    return bool(value) if value is not None else default


def _is_secret(text: str) -> bool:
    return bool(text) and bool(_SECRET_RE.search(str(text)))


def _win32_clipboard():
    if sys.platform != "win32":
        return None, None
    try:
        import ctypes
        return ctypes.windll.user32, ctypes.windll.kernel32
    except Exception:
        return None, None


def _open_win_clipboard(user32) -> bool:
    for _ in range(5):
        if user32.OpenClipboard(0):
            return True
        time.sleep(0.02)
    return False


def _win_clip_get() -> str | None:
    """Read CF_UNICODETEXT with retry-on-lock and guaranteed cleanup."""
    user32, kernel32 = _win32_clipboard()
    if user32 is None or kernel32 is None:
        return None
    import ctypes
    if not _open_win_clipboard(user32):
        return None
    try:
        if not user32.IsClipboardFormatAvailable(13):      # CF_UNICODETEXT
            return ""
        handle = user32.GetClipboardData(13)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    except Exception:
        return None
    finally:
        user32.CloseClipboard()


def _win_clip_set(text: str) -> bool:
    """Write UTF-16 text using the Win32 clipboard API (with retry)."""
    user32, kernel32 = _win32_clipboard()
    if user32 is None or kernel32 is None:
        return False
    import ctypes
    value = str(text or "")
    if not _open_win_clipboard(user32):
        return False
    handle = None
    pointer = None
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(0x0002, (len(value) + 1) * 2)  # GMEM_MOVEABLE
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return False
        ctypes.memmove(pointer, value.encode("utf-16le") + b"\x00\x00", (len(value) + 1) * 2)
        kernel32.GlobalUnlock(handle)
        pointer = None
        if not user32.SetClipboardData(13, handle):
            return False
        handle = None       # ownership transferred to the system
        return True
    except Exception:
        return False
    finally:
        if pointer is not None and handle is not None:
            try:
                kernel32.GlobalUnlock(handle)
            except Exception:
                pass
        if handle is not None:
            try:
                kernel32.GlobalFree(handle)
            except Exception:
                pass
        user32.CloseClipboard()


def _command_clip_get() -> str | None:
    for program, args in (
        ("pbcopy", None), ("xclip", ["-selection", "clipboard", "-o"]),
        ("wl-paste", None),
    ):
        if program == "pbcopy":
            continue
        if not shutil.which(program):
            continue
        try:
            result = subprocess.run([program] + (args or []), capture_output=True,
                                    text=True, timeout=5)
            if result.returncode == 0:
                return result.stdout
        except Exception:
            continue
    return None


def _command_clip_set(text: str) -> bool:
    commands = []
    if shutil.which("pbcopy"):
        commands.append(["pbcopy"])
    if shutil.which("xclip"):
        commands.append(["xclip", "-selection", "clipboard"])
    if shutil.which("wl-copy"):
        commands.append(["wl-copy"])
    for command in commands:
        try:
            result = subprocess.run(command, input=str(text or ""), text=True,
                                    capture_output=True, timeout=5)
            if result.returncode == 0:
                return True
        except Exception:
            continue
    return False


def _clipboard_get() -> str:
    if _PYPERCLIP:
        try:
            return pyperclip.paste() or ""
        except Exception:
            pass
    value = _win_clip_get()
    if value is not None:
        return value
    return _command_clip_get() or ""


def _clipboard_set(text: str) -> bool:
    text = str(text or "")
    if _PYPERCLIP:
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            pass
    if sys.platform == "win32" and _win_clip_set(text):
        return True
    return _command_clip_set(text)


def _push_history(text: str):
    text = (text or "").strip()
    if not text or _is_secret(text):
        return
    with _CLIP_LOCK:
        if text in _HISTORY:
            _HISTORY.remove(text)
        _HISTORY.insert(0, text)
        del _HISTORY[_HISTORY_LIMIT:]


def _transform(name: str, text: str) -> str:
    name = (name or "").strip().lower().replace("-", "_")
    if name == "upper":
        return text.upper()
    if name == "lower":
        return text.lower()
    if name == "title":
        return text.title()
    if name == "strip":
        return text.strip()
    if name == "trim_lines":
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if name == "clean_whitespace":
        return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()
    if name == "dedupe_lines":
        return "\n".join(dict.fromkeys(line for line in text.splitlines() if line.strip()))
    if name == "sort_lines":
        return "\n".join(sorted(line for line in text.splitlines() if line.strip()))
    if name == "json_pretty":
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    if name == "json_minify":
        return json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False)
    if name == "url_encode":
        return quote(text, safe="")
    if name == "url_decode":
        return unquote(text)
    if name == "word_count":
        return str(len(re.findall(r"\b\w+\b", text)))
    raise ValueError(f"Unknown transform '{name}'")


def _content_type(text: str) -> str:
    if not text:
        return "empty"
    if re.match(r"^https?://\S+$", text.strip()):
        return "url"
    if re.match(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", text.strip()):
        return "email"
    try:
        json.loads(text)
        return "json"
    except Exception:
        pass
    if re.match(r"^[/\\]|^[A-Za-z]:[\\/]", text.strip()):
        return "file_path"
    if re.search(r"\b(def |class |import |from |function |const |#include)", text):
        return "code"
    if "\n" in text:
        return "multiline_text"
    return "text"


def _clipboard_formats() -> list[str]:
    user32, _ = _win32_clipboard()
    if user32 is not None:
        codes = {13: "text", 2: "text", 15: "files", "49156": "html"}
        found = []
        for code in (13, 15, "49156"):
            try:
                if user32.IsClipboardFormatAvailable(int(code)):
                    found.append(codes[code])
            except Exception:
                pass
        return list(dict.fromkeys(found)) or ["empty"]
    return ["text"] if _clipboard_get() else ["empty"]


def _get_copied_files() -> list[str]:
    user32, _ = _win32_clipboard()
    if user32 is None or not _open_win_clipboard(user32):
        return []
    import ctypes
    from ctypes import wintypes
    try:
        if not user32.IsClipboardFormatAvailable(15):      # CF_HDROP
            return []
        handle = user32.GetClipboardData(15)
        if not handle:
            return []
        shell32 = ctypes.windll.shell32
        shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                           wintypes.LPWSTR, wintypes.UINT]
        count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        paths = []
        for index in range(min(int(count), 100)):
            buffer = ctypes.create_unicode_buffer(1024)
            shell32.DragQueryFileW(handle, index, buffer, 1024)
            if buffer.value:
                paths.append(buffer.value)
        return paths
    except Exception:
        return []
    finally:
        user32.CloseClipboard()


def clipboard_action(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Lock-safe clipboard: history, pinning, search, transforms and rich data."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action", "get") or "get").strip().lower().replace("-", "_")

    if action in ("get", "read", "what", "current"):
        text = _clipboard_get()
        return f"Clipboard: {text[:200]}" if text else "The clipboard is empty, sir."

    if action in ("set", "copy", "write"):
        text = str(params.get("text", "") or "")
        if not text:
            return "Nothing to copy, sir."
        if _clipboard_set(text):
            _push_history(text)
            return f"Copied to clipboard: {text[:100]}"
        return "Failed to write to clipboard, sir."

    if action in ("paste", "type"):
        text = _clipboard_get()
        if not text:
            return "Clipboard is empty, nothing to paste, sir."
        try:
            import pyautogui
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.15)
        except Exception:
            try:
                from actions.computer_settings import type_text
                type_text(text)
            except Exception:
                pass
        _push_history(text)
        return f"Pasted from clipboard: {text[:60]}"

    if action in ("clear", "empty", "wipe"):
        return "Clipboard cleared, sir." if _clipboard_set("") else "Could not clear clipboard, sir."

    if action in ("history", "recent"):
        limit = _clamp_limit(params.get("limit", 10), 1, 50, 10)
        with _CLIP_LOCK:
            if not _HISTORY:
                return "No clipboard history yet, sir."
            return "Clipboard history: " + " | ".join(
                f"{i}. '{text[:40]}'" for i, text in enumerate(_HISTORY[:limit], 1))

    if action in ("copy_last", "recall", "paste_last", "restore", "copy_index"):
        with _CLIP_LOCK:
            if not _HISTORY:
                return "No clipboard history to recall, sir."
            index = _clamp_limit(params.get("index", 1), 1, len(_HISTORY), 1) - 1
            text = str(params.get("text", "")) if action == "restore" and params.get("text") else _HISTORY[index]
        if _clipboard_set(text):
            _push_history(text)
            return f"Recalled [{index + 1}]: {text[:100]}"
        return "Could not restore clipboard, sir."

    if action == "pin":
        text = str(params.get("text", "") or _clipboard_get() or "").strip()
        if not text:
            return "Nothing to pin, sir."
        with _CLIP_LOCK:
            if text not in _PINNED:
                _PINNED.insert(0, text)
                del _PINNED[_PINNED_LIMIT:]
        return f"Pinned: {text[:80]}"

    if action == "unpin":
        target = str(params.get("text", "") or "").strip()
        with _CLIP_LOCK:
            if target and target in _PINNED:
                _PINNED.remove(target)
            elif params.get("index"):
                index = _clamp_limit(params.get("index"), 1, len(_PINNED), 1) - 1
                _PINNED.pop(index)
            else:
                return "No matching pinned item, sir."
        return "Pinned item removed."

    if action == "pinned":
        with _CLIP_LOCK:
            if not _PINNED:
                return "No pinned clipboard items, sir."
            return "Pinned: " + " | ".join(f"{i}. '{t[:50]}'" for i, t in enumerate(_PINNED, 1))

    if action == "search":
        needle = str(params.get("query", params.get("text", "")) or "").lower().strip()
        if not needle:
            return "What should I search for in clipboard history, sir?"
        results = []
        with _CLIP_LOCK:
            for index, text in enumerate(_HISTORY, 1):
                if needle in text.lower():
                    results.append(f"history[{index}]: {text[:80]}")
            for index, text in enumerate(_PINNED, 1):
                if needle in text.lower():
                    results.append(f"pinned[{index}]: {text[:80]}")
        return "\n".join(results) if results else "No matching clipboard entries."

    if action == "append":
        separator = str(params.get("separator", "\n"))
        current = _clipboard_get()
        extra = str(params.get("text", "") or "")
        combined = current + (separator if current and extra else "") + extra
        if _clipboard_set(combined):
            _push_history(combined)
            return f"Appended text ({len(combined)} chars)."
        return "Could not append to clipboard, sir."

    if action == "transform":
        text = str(params.get("text", "") or _clipboard_get() or "")
        if not text:
            return "Clipboard is empty, nothing to transform, sir."
        try:
            result = _transform(params.get("transform", params.get("mode", "upper")), text)
        except Exception as exc:
            return f"Transform failed: {exc}"
        if _as_bool(params.get("copy", True), True):
            _clipboard_set(result)
            _push_history(result)
        return f"Transformed ({len(result)} chars): {result[:200]}"

    if action in ("info", "stats"):
        text = _clipboard_get()
        words = len(re.findall(r"\b\w+\b", text))
        lines = [
            f"Characters: {len(text)}",
            f"Words: {words}",
            f"Lines: {len(text.splitlines())}",
            f"Type: {_content_type(text)}",
        ]
        return "Clipboard info:\n" + "\n".join(lines)

    if action == "formats":
        return "Clipboard formats: " + ", ".join(_clipboard_formats())

    if action == "get_files":
        paths = _get_copied_files()
        return "Copied files:\n" + "\n".join(paths) if paths else "No files on the clipboard."

    if action == "save_image":
        target = Path(params.get("save_path", "") or
                      (BASE_DIR / "data" / "screenshots" /
                       f"clipboard_{int(time.time())}.png")).expanduser()
        try:
            from PIL import ImageGrab
            image = ImageGrab.grabclipboard()
            if image is None:
                return "No image on the clipboard."
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(str(target))
            return f"Clipboard image saved: {target}"
        except Exception as exc:
            return f"Could not save clipboard image: {exc}"

    return f"Unknown clipboard action: {action}, sir."


def _clamp_limit(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default
