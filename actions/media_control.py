# actions/media_control.py
# Friday — universal media/music control (Spotify, YouTube, VLC, ...).
#
# Windows uses the low-level SendInput API with the complete INPUT union and
# the documented virtual-key codes — no third-party keyboard hook is needed.
# macOS/Linux fall back to osascript / playerctl / pactl, and the old
# `keyboard` package remains a final fallback everywhere.

import ctypes
import platform
import subprocess

# Windows virtual-key codes (winuser.h).
_VK = {
    "play": 0xB3, "pause": 0xB3, "toggle": 0xB3, "play_pause": 0xB3,
    "next": 0xB0, "skip": 0xB0, "next_track": 0xB0,
    "prev": 0xB1, "previous": 0xB1, "back": 0xB1, "prev_track": 0xB1,
    "volume_up": 0xAF, "vol_up": 0xAF,
    "volume_down": 0xAE, "vol_down": 0xAE,
    "mute": 0xAD, "unmute": 0xAD, "volume_mute": 0xAD,
}
_HUMAN = {
    0xB3: "play/pause", 0xB0: "next track", 0xB1: "previous track",
    0xAF: "volume up", 0xAE: "volume down", 0xAD: "mute/unmute",
}
_KEYBOARD_KEYS = {
    0xB3: "play/pause media", 0xB0: "next track", 0xB1: "prev track",
    0xAF: "volume up", 0xAE: "volume down", 0xAD: "volume mute",
}
# AppleScript key codes used by System Events.
_MAC_KEYS = {
    0xB3: 16, 0xB0: 17, 0xB1: 18,
    0xAF: 28, 0xAE: 27, 0xAD: 26,
}
_PLAYERCTL = {
    0xB3: "play-pause", 0xB0: "next", 0xB1: "previous",
}


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _input_types():
    """Build the full Win32 INPUT union (lazily, so Linux imports cleanly)."""
    DWORD = ctypes.c_ulong
    WORD = ctypes.c_ushort
    ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                    ("mouseData", DWORD), ("dwFlags", DWORD), ("time", DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD),
                    ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", DWORD), ("u", _INPUTUNION)]

    return INPUT, KEYBDINPUT, _INPUTUNION


def _windows_send_vk(vk: int, steps: int = 1) -> bool:
    """Send a virtual key press N times via user32.SendInput (hook-free)."""
    if platform.system() != "Windows":
        return False
    try:
        INPUT, _KEYBDINPUT, _UNION = _input_types()
        user32 = ctypes.windll.user32
        KEYEVENTF_KEYUP = 0x0002
        sent = 0
        for _ in range(max(1, int(steps))):
            down = INPUT(type=1)
            down.ki.wVk = vk
            sent += user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
            up = INPUT(type=1)
            up.ki.wVk = vk
            up.ki.dwFlags = KEYEVENTF_KEYUP
            sent += user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
        return sent > 0
    except Exception:
        return False


def _windows_set_volume(percent: int) -> bool:
    """Set the master output volume on Windows via winmm (0-100)."""
    if platform.system() != "Windows":
        return False
    try:
        value = int(max(0, min(100, percent)) / 100 * 0xFFFF)
        left = value | (value << 16)
        return ctypes.windll.winmm.waveOutSetVolume(0, left) == 0
    except Exception:
        return False


def _keyboard_fallback(vk: int, steps: int) -> bool:
    try:
        import keyboard
        for _ in range(max(1, int(steps))):
            keyboard.press_and_release(_KEYBOARD_KEYS[vk])
        return True
    except Exception:
        return False


def _mac_send(vk: int, steps: int) -> bool:
    try:
        code = _MAC_KEYS.get(vk)
        if code is None:
            return False
        for _ in range(max(1, int(steps))):
            subprocess.run(
                ["osascript", "-e",
                 f'tell application "System Events" to key code {code}'],
                capture_output=True, timeout=5, check=False,
            )
        return True
    except Exception:
        return False


def _linux_send(vk: int, steps: int) -> bool:
    playerctl_cmd = _PLAYERCTL.get(vk)
    if playerctl_cmd:
        try:
            for _ in range(max(1, int(steps))):
                subprocess.run(["playerctl", playerctl_cmd], capture_output=True,
                               timeout=5, check=False)
            return True
        except Exception:
            pass
    if vk in (0xAF, 0xAE, 0xAD):
        relative = {0xAF: "5%+", 0xAE: "5%-", 0xAD: "toggle"}[vk]
        try:
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", relative],
                capture_output=True, timeout=5, check=False)
            return True
        except Exception:
            pass
    return False


def _query_media_status() -> dict:
    """Best-effort current media state; returns {} when no engine is present."""
    if platform.system() == "Windows":
        try:
            ps = (
                "$p = Get-Process | Where-Object { $_.MainWindowTitle -ne '' } "
                "| Select-Object -First 1 -ExpandProperty MainWindowTitle; $p"
            )
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            return {"status": "playing" if out else "unknown", "title": out}
        except Exception:
            return {}
    try:
        state = subprocess.run(["playerctl", "metadata", "--format", "{{status}}"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
        title = subprocess.run(
            ["playerctl", "metadata", "--format", "{{artist}} - {{title}}"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        return {"status": state or "unknown", "title": title}
    except Exception:
        return {}


def _set_volume(percent: int) -> bool:
    if platform.system() == "Windows":
        if _windows_set_volume(percent):
            return True
    if platform.system() == "Darwin":
        try:
            subprocess.run(["osascript", "-e",
                            f"set volume output volume {max(0, min(100, percent))}"],
                           capture_output=True, timeout=5, check=False)
            return True
        except Exception:
            return False
    try:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@",
                        f"{max(0, min(100, percent))}%"],
                       capture_output=True, timeout=5, check=False)
        return True
    except Exception:
        return False


def media_control_action(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Control any playing media, set volume, or inspect the current media."""
    params = parameters or {}
    action = str(params.get("action", "toggle")).lower().strip()
    try:
        steps = max(1, min(100, int(params.get("steps", params.get("count", 1)) or 1)))
    except (TypeError, ValueError):
        steps = 1

    if action in ("status", "now_playing", "now playing", "current"):
        info = _query_media_status()
        title = info.get("title") or ""
        state = info.get("status") or "unknown"
        if action == "now_playing" or not title:
            return (f"Now playing: {title}." if title else
                    "No media is playing right now, sir.")
        return f"Media status: {state}" + (f", now playing {title}" if title else "") + "."

    if action in ("volume_set", "set_volume", "volume"):
        try:
            percent = int(float(params.get("volume", params.get("value", 50))))
        except (TypeError, ValueError):
            return "Volume must be a number between 0 and 100, sir."
        percent = max(0, min(100, percent))
        if _set_volume(percent):
            return f"Volume set to {percent} percent, sir."
        # Last resort: step the volume keys to approximate the target.
        vk = _VK["volume_up"] if percent >= 50 else _VK["volume_down"]
        _windows_send_vk(vk, max(1, abs(50 - percent) // 5))
        return f"Volume set to about {percent} percent, sir."

    vk = _VK.get(action)
    if vk is None:
        return ("Unknown media action, sir. Use: play | pause | toggle | next | "
                "prev | volume_up | volume_down | mute | volume_set | status | now_playing.")

    system = platform.system()
    ok = False
    if system == "Windows":
        ok = _windows_send_vk(vk, steps)
    elif system == "Darwin":
        ok = _mac_send(vk, steps)
    else:
        ok = _linux_send(vk, steps)
    if not ok:
        ok = _keyboard_fallback(vk, steps)
    if not ok:
        return "Could not control media on this system, sir."
    repeats = "" if steps == 1 else f" {steps} time(s)"
    return f"Pressed {_HUMAN.get(vk, action)}{repeats}, sir."
