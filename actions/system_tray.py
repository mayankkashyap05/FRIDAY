# actions/system_tray.py
# Friday — background system-tray icon + optional global hotkeys.
# Optional deps: pystray (tray) and keyboard (global hotkeys). Both are
# guarded — missing ones simply disable that feature instead of crashing.

import json
import re
import threading
import time
from pathlib import Path

try:
    import pystray
    from PIL import Image, ImageDraw
    _PYSTRAY = True
except Exception:
    pystray = None
    Image = ImageDraw = None
    _PYSTRAY = False

try:
    import keyboard
    _KEYBOARD = True
except Exception:
    keyboard = None
    _KEYBOARD = False

BASE_DIR = Path(__file__).resolve().parent.parent
UI_SETTINGS_PATH = BASE_DIR / "config" / "ui_settings.json"
DEFAULT_DEBOUNCE_S = 0.25

_LOCK = threading.RLock()
_tray_icon          = None
_tray_thread        = None
_hotkeys_registered = False
_registered_handles = {}   # {normalized_hotkey: keyboard handle}
_hotkey_callbacks   = {}   # {normalized_hotkey: (callback, action_name)}
_last_fired         = {}   # {normalized_hotkey: monotonic timestamp}
_hotkeys_paused     = False
_last_error         = ""
_cached_icon        = None

DEFAULT_HOTKEYS = {
    "ctrl+alt+m": "toggle_mute",
    "ctrl+alt+w": "toggle_wake",
    "ctrl+alt+h": "hide_show",
    "ctrl+alt+b": "briefing",
    "ctrl+alt+q": "quit",
}


def _draw_icon():
    """Draw FRIDAY's tray mark: an Apple-Siri-style glass orb.

    A fully transparent 64x64 canvas. A soft, feathered cyan-violet-magenta
    halo fades out past the sphere, then the glass body is painted as a
    deep indigo -> sapphire -> violet vertical gradient with a spherical
    shade curve, a cyan silk band sweeping the diagonal, a warm white-hot
    core along the band's crossing, a razor-thin rim, an upper-left
    crescent sheen and a bright specular accent. No flat rings or gradients
    outside the silhouette: the silhouette is a crisp disc and nothing
    beyond it survives.
    """
    import math
    from PIL import Image, ImageDraw, ImageFilter

    size = 64
    center = size // 2

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # 1. Halo: feathered aqua -> violet -> rose bloom, fading to zero.
    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    for i in range(60, 0, -1):
        r = (i / 60.0) * 30.0
        t = i / 60.0
        col = tuple(int((6, 247, 255)[j] + ((176, 48, 255)[j] - (6, 247, 255)[j]) * (1.0 - t)) for j in range(3))
        a = 0.16 * t * t
        hd.ellipse((center - r, center - r, center + r, center + r),
                   fill=(*col, int(a * 255)))
    halo = halo.filter(ImageFilter.GaussianBlur(1.5))
    img = Image.alpha_composite(img, halo)

    # 2. Glass sphere: vertical indigo -> sapphire -> magenta gradient with
    #    spherical shading (rim darker, centre brighter).
    R = 21.0
    top = (26, 18, 84)
    mid = (48, 96, 255)
    bot = (184, 54, 255)
    glass = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gpx = glass.load()
    for y in range(size):
        for x in range(size):
            dx = x - center
            dy = y - center
            dist = math.hypot(dx, dy)
            if dist > R:
                continue
            norm = dist / R
            v = (dy + R) / (2.0 * R)
            v = min(1.0, max(0.0, v))
            if v < 0.5:
                k = v / 0.5
                col = tuple(top[i] + (mid[i] - top[i]) * k for i in range(3))
            else:
                k = (v - 0.5) / 0.5
                col = tuple(mid[i] + (bot[i] - mid[i]) * k for i in range(3))
            shade = 1.0 - 0.22 * norm ** 1.5 + 0.14 * max(0.0, 1.0 - norm * 2.2)
            shade = max(0.25, min(1.3, shade))
            col = tuple(int(min(255, max(0, c * shade))) for c in col)
            feather = min(1.0, max(0.0, (R - dist) / 1.0))
            gpx[x, y] = (*col, int(255 * feather))
    img = Image.alpha_composite(img, glass)

    # 3. Silk band: a cyan sweep along the diagonal, clipped to the glass.
    silk = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(silk)
    span = R * 1.9
    for step in range(260):
        u = -1.0 + 2.0 * step / 259
        taper = math.cos(abs(u) * math.pi / 2) ** 0.8
        if taper <= 0.02:
            continue
        yLocal = 0.30 * R * math.sin(u * math.pi * 0.42 + 1.1) * taper + 0.10 * u * R
        xLocal = u * span
        ca, sa = math.cos(-0.26), math.sin(-0.26)
        px = center + xLocal * ca - yLocal * sa
        py = center + xLocal * sa + yLocal * ca
        radius = (0.16 + 0.22 * taper) * R
        a = int(150 * taper * taper)
        sd.ellipse((px - radius, py - radius, px + radius, py + radius),
                   fill=(6, 247, 255, a))
    silk = silk.filter(ImageFilter.GaussianBlur(2.6))
    sphere_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(sphere_mask).ellipse((center - R, center - R, center + R, center + R),
                                         fill=255)
    silk.putalpha(Image.composite(silk.getchannel("A"), Image.new("L", (size, size), 0), sphere_mask))
    img = Image.alpha_composite(img, silk)

    # 4. Warm white-hot core along the band's crossing.
    core = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cd = ImageDraw.Draw(core)
    cd.ellipse((center - 8, center - 8, center + 8, center + 8),
               fill=(255, 255, 255, 110))
    core = core.filter(ImageFilter.GaussianBlur(3.2))
    core.putalpha(Image.composite(core.getchannel("A"), Image.new("L", (size, size), 0), sphere_mask))
    img = Image.alpha_composite(img, core)

    # 5. Razor-thin rim along the silhouette edge.
    ImageDraw.Draw(img).ellipse((center - R, center - R, center + R, center + R),
                                 outline=(255, 255, 255, 90), width=1)

    # 6. Upper-left crescent sheen, tucked inside the contour.
    sheen = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd2 = ImageDraw.Draw(sheen)
    sd2.ellipse((center - 12, center - 14, center - 2, center - 6),
                fill=(255, 255, 255, 74))
    sheen = sheen.filter(ImageFilter.GaussianBlur(3.4))
    sheen.putalpha(Image.composite(sheen.getchannel("A"), Image.new("L", (size, size), 0), sphere_mask))
    img = Image.alpha_composite(img, sheen)

    # 7. Bright specular accent on the upper-left rim.
    ImageDraw.Draw(img).ellipse((center - 13, center - 12, center - 6, center - 6),
                                fill=(255, 255, 255, 205))
    return img

def _build_icon(force_rebuild: bool = False):
    """Return the cached 64x64 RGBA orb, drawing it only when needed."""
    global _cached_icon, _last_error
    with _LOCK:
        if _cached_icon is not None and not force_rebuild:
            return _cached_icon
        try:
            _cached_icon = _draw_icon()
        except Exception as exc:
            with _LOCK:
                _last_error = f"icon build failed: {exc}"
            return None
        return _cached_icon


def _normalize_hotkey(hotkey) -> str:
    """Lowercase and canonicalise chord spacing: 'CTRL + ALT + M' → ctrl+alt+m."""
    text = str(hotkey or "").strip().lower()
    text = re.sub(r"\s*\+\s*", "+", text)
    return re.sub(r"\s+", "", text)


def _config_hotkeys() -> dict:
    """Read optional hotkey overrides from config/ui_settings.json."""
    try:
        data = json.loads(UI_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    overrides = data.get("hotkeys") if isinstance(data, dict) else None
    if not isinstance(overrides, dict):
        return {}
    clean = {}
    for hotkey, action in overrides.items():
        chord = _normalize_hotkey(hotkey)
        name = str(action or "").strip()
        if chord and name:
            clean[chord] = name
    return clean


def _merged_hotkeys(hotkeys=None) -> dict:
    merged = dict(DEFAULT_HOTKEYS)
    merged.update(_config_hotkeys())
    if isinstance(hotkeys, dict):
        for hotkey, action in hotkeys.items():
            chord = _normalize_hotkey(hotkey)
            name = str(action or "").strip()
            if chord and name:
                merged[chord] = name
    return merged


def _make_handler(hotkey: str, callback, debounce_s: float):
    """Debounce, pause-guard and dispatch a callback off the hook thread."""
    def handler():
        with _LOCK:
            if _hotkeys_paused:
                return
            now = time.monotonic()
            if hotkey in _last_fired and \
                    now - _last_fired[hotkey] < debounce_s:
                return
            _last_fired[hotkey] = now
        thread_name = "FridayHotkey-" + re.sub(r"[^A-Za-z0-9_-]", "_", hotkey)[:40]

        def run():
            global _last_error
            try:
                callback()
            except Exception as exc:
                with _LOCK:
                    _last_error = f"hotkey {hotkey}: {exc}"
                print(f"[Tray] ⚠️ Hotkey callback failed: {exc}")

        threading.Thread(target=run, daemon=True, name=thread_name).start()
    return handler


def _unregister_all_locked() -> None:
    """Unregister only hotkeys this module registered."""
    global _hotkeys_registered, _last_error
    if not _KEYBOARD or keyboard is None:
        _registered_handles.clear()
        _hotkey_callbacks.clear()
        _last_fired.clear()
        _hotkeys_registered = False
        return
    remover = getattr(keyboard, "remove_hotkey", None)
    for hotkey, handle in list(_registered_handles.items()):
        try:
            if callable(remover):
                remover(handle)
            else:
                keyboard.unhook_all_hotkeys()
                break
        except Exception as exc:
            with _LOCK:
                _last_error = f"unregister {hotkey}: {exc}"
    _registered_handles.clear()
    _hotkey_callbacks.clear()
    _last_fired.clear()
    _hotkeys_registered = False


def configure_icon(menu_factory, title: str = "Friday") -> bool:
    """Build a pystray icon with the given menu factory and show it in a thread."""
    global _tray_icon, _tray_thread, _last_error
    if not _PYSTRAY or pystray is None:
        return False
    with _LOCK:
        if _tray_icon is not None:
            return True
        try:
            menu = menu_factory() if callable(menu_factory) else menu_factory
            icon = pystray.Icon(
                name="Friday", icon=_build_icon(), title=str(title or "Friday"), menu=menu,
            )
            _tray_icon = icon
            _tray_thread = threading.Thread(
                target=icon.run, daemon=True, name="TrayThread"
            )
            _tray_thread.start()
            return True
        except Exception as exc:
            _last_error = str(exc)
            _tray_icon = None
            _tray_thread = None
            print(f"[Tray] ⚠️ {exc}")
            return False


def update_tray_title(title: str) -> bool:
    """Safely update the running tray icon's tooltip/title."""
    global _last_error
    with _LOCK:
        if _tray_icon is None:
            return False
        try:
            _tray_icon.title = str(title or "Friday")
            return True
        except Exception as exc:
            _last_error = str(exc)
            return False


def register_hotkeys(callbacks: dict, hotkeys=None,
                     debounce_s: float = DEFAULT_DEBOUNCE_S) -> int:
    """Register FRIDAY global hotkeys, replacing any previous registration."""
    global _hotkeys_registered, _last_error
    options = callbacks if isinstance(callbacks, dict) else {}
    try:
        debounce = max(0.0, float(debounce_s))
    except (TypeError, ValueError):
        debounce = DEFAULT_DEBOUNCE_S

    with _LOCK:
        _unregister_all_locked()
        if not _KEYBOARD or keyboard is None:
            _last_error = "keyboard module unavailable"
            return 0
        count = 0
        try:
            for hotkey, action_name in _merged_hotkeys(hotkeys).items():
                callback = options.get(action_name)
                if not callable(callback):
                    continue
                handle = keyboard.add_hotkey(
                    hotkey, _make_handler(hotkey, callback, debounce), suppress=False
                )
                _registered_handles[hotkey] = handle
                _hotkey_callbacks[hotkey] = (callback, action_name)
                count += 1
            _hotkeys_registered = bool(_registered_handles)
            _last_error = ""
        except Exception as exc:
            _last_error = f"hotkey registration failed: {exc}"
            print(f"[Tray] ⚠️ {exc}")
            _unregister_all_locked()
            return 0
        return count


def add_hotkey(hotkey: str, callback, name: str = "",
               debounce_s: float = DEFAULT_DEBOUNCE_S) -> bool:
    """Register one additional tracked hotkey at runtime."""
    global _hotkeys_registered, _last_error
    chord = _normalize_hotkey(hotkey)
    if not chord or not callable(callback) or not _KEYBOARD or keyboard is None:
        return False
    try:
        debounce = max(0.0, float(debounce_s))
    except (TypeError, ValueError):
        debounce = DEFAULT_DEBOUNCE_S
    with _LOCK:
        if chord in _registered_handles:
            _unregister_one_locked(chord)
        try:
            handle = keyboard.add_hotkey(
                chord, _make_handler(chord, callback, debounce), suppress=False
            )
            _registered_handles[chord] = handle
            _hotkey_callbacks[chord] = (callback, str(name or chord))
            _hotkeys_registered = True
            return True
        except Exception as exc:
            _last_error = str(exc)
            return False


def _unregister_one_locked(hotkey: str) -> bool:
    global _last_error, _hotkeys_registered
    chord = _normalize_hotkey(hotkey)
    if chord not in _registered_handles:
        return False
    try:
        remover = getattr(keyboard, "remove_hotkey", None) if _KEYBOARD else None
        if callable(remover):
            remover(_registered_handles[chord])
        elif _KEYBOARD and keyboard is not None:
            keyboard.unhook_all_hotkeys()
    except Exception as exc:
        _last_error = str(exc)
    _registered_handles.pop(chord, None)
    _hotkey_callbacks.pop(chord, None)
    _last_fired.pop(chord, None)
    _hotkeys_registered = bool(_registered_handles)
    return True


def remove_hotkey(hotkey: str) -> bool:
    """Unregister one FRIDAY hotkey by chord."""
    with _LOCK:
        return _unregister_one_locked(hotkey)


def pause_hotkeys() -> None:
    global _hotkeys_paused
    with _LOCK:
        _hotkeys_paused = True


def resume_hotkeys() -> None:
    global _hotkeys_paused
    with _LOCK:
        _hotkeys_paused = False


def list_hotkeys() -> dict:
    with _LOCK:
        return {
            hotkey: (metadata[1] if isinstance(metadata, tuple) else str(metadata))
            for hotkey, metadata in _hotkey_callbacks.items()
        }


def diagnostics() -> dict:
    with _LOCK:
        return {
            "pystray_available": bool(_PYSTRAY),
            "keyboard_available": bool(_KEYBOARD),
            "tray_running": _tray_icon is not None and _tray_thread is not None,
            "hotkeys_registered": bool(_hotkeys_registered),
            "hotkeys_paused": bool(_hotkeys_paused),
            "hotkey_count": len(_registered_handles),
            "active_hotkeys": list(_registered_handles),
            "last_error": _last_error,
        }


def stop():
    """Stop the tray thread and remove only FRIDAY's tracked hotkeys."""
    global _tray_icon, _tray_thread, _hotkeys_registered, _last_error
    with _LOCK:
        icon, thread = _tray_icon, _tray_thread
        _tray_icon = None
        _tray_thread = None
        _unregister_all_locked()
    if icon is not None:
        try:
            icon.stop()
        except Exception as exc:
            _last_error = str(exc)
    if thread is not None and thread is not threading.current_thread():
        try:
            thread.join(timeout=1.5)
        except Exception:
            pass
    with _LOCK:
        _hotkeys_registered = False
        _registered_handles.clear()
        _hotkey_callbacks.clear()
        _last_fired.clear()


def available() -> str:
    info = diagnostics()
    parts = [
        "tray: on" if info["pystray_available"] else "tray: off (pip install pystray)",
        "hotkeys: on" if info["keyboard_available"] else "hotkeys: off (pip install keyboard)",
    ]
    return ", ".join(parts)
