# actions/system_tray.py
# Friday — background system-tray icon + optional global hotkeys.
# Optional deps: pystray (tray) and keyboard (global hotkeys). Both are
# guarded — missing ones simply disable that feature instead of crashing.

import threading

try:
    import pystray
    from PIL import Image, ImageDraw
    _PYSTRAY = True
except Exception:
    _PYSTRAY = False

try:
    import keyboard
    _KEYBOARD = True
except Exception:
    _KEYBOARD = False

_tray_icon          = None
_tray_thread        = None
_hotkeys_registered = False

DEFAULT_HOTKEYS = {
    "ctrl+alt+m": "toggle_mute",
    "ctrl+alt+w": "toggle_wake",
    "ctrl+alt+h": "hide_show",
    "ctrl+alt+b": "briefing",
    "ctrl+alt+q": "quit",
}


def _build_icon():
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

def configure_icon(menu_factory, title: str = "Friday") -> bool:
    """Build a pystray icon with the given menu factory and show it in a thread."""
    global _tray_icon, _tray_thread
    if not _PYSTRAY:
        return False
    if _tray_icon is not None:
        return True
    try:
        menu = menu_factory() if callable(menu_factory) else menu_factory
        icon = pystray.Icon(
            name="Friday",
            icon=_build_icon(),
            title=title,
            menu=menu,
        )
        _tray_icon   = icon
        _tray_thread = threading.Thread(target=icon.run, daemon=True, name="TrayThread")
        _tray_thread.start()
        return True
    except Exception as e:
        print(f"[Tray] ⚠️ {e}")
        return False


def register_hotkeys(callbacks: dict) -> int:
    """Register global hotkeys mapped to callback keys (toggle_mute, etc)."""
    global _hotkeys_registered
    if not _KEYBOARD:
        return 0
    try:
        count = 0
        for hotkey, cb_key in DEFAULT_HOTKEYS.items():
            cb = callbacks.get(cb_key)
            if not cb:
                continue
            keyboard.add_hotkey(hotkey, cb, suppress=False)
            count += 1
        _hotkeys_registered = True
        return count
    except Exception as e:
        print(f"[Tray] ⚠️ Hotkey registration failed: {e}")
        return 0


def stop():
    global _tray_icon, _tray_thread, _hotkeys_registered
    if _tray_icon is not None:
        try:
            _tray_icon.stop()
        except Exception:
            pass
        _tray_icon   = None
        _tray_thread = None
    if _KEYBOARD and _hotkeys_registered:
        try:
            keyboard.unhook_all_hotkeys()
            _hotkeys_registered = False
        except Exception:
            pass


def available() -> str:
    parts = []
    parts.append("tray: on" if _PYSTRAY else "tray: off (pip install pystray)")
    parts.append("hotkeys: on" if _KEYBOARD else "hotkeys: off (pip install keyboard)")
    return ", ".join(parts)