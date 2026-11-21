"""FRIDAY ambient UI — Windows 10/11 native behaviour layer.

Everything here is **best-effort and non-fatal**. The contract (see
``docs/UI_AUDIT_CONTRACT.md`` §4.5) is:

* Every public function returns a plain value and never raises.
* Every function reports *why* it degraded, so the UI can log it once and move
  on instead of silently looking broken.
* Importing this module on Linux/macOS must succeed (it is imported by the
  cross-platform test-suite).

Feature surface
---------------
* Click-through (``WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE``)
* Always-on-top / bottom / no-activate z-ordering
* Capture exclusion (``SetWindowDisplayAffinity`` WDA_EXCLUDEFROMCAPTURE)
* Per-monitor DPI awareness v2
* Dark/light theme + transparency + reduced-motion probes
* Do-Not-Disturb probe (``SHQueryUserNotificationState``)
* DWM rounded corners / system backdrop / immersive dark titlebar
* Monitor enumeration with virtual-desktop coordinates
"""

from __future__ import annotations

import ctypes
import sys
import threading
from dataclasses import dataclass, asdict, field
from typing import Any

try:  # ctypes.wintypes is not importable on every platform
    import ctypes.wintypes as wintypes
except Exception:  # pragma: no cover - only on exotic hosts
    wintypes = None  # type: ignore[assignment]

IS_WINDOWS = sys.platform == "win32"

# ── Constants ────────────────────────────────────────────────────────────────
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Windows 10 2004+

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWCP_ROUND = 2
DWMSBT_TRANSIENTWINDOW = 3
DWMSBT_MAINWINDOW = 2

SPI_GETCLIENTAREAANIMATION = 0x1042
SPI_GETACTIVEWINDOWTX = 0x1024

QUNS_ACCEPTS_NOTIFICATIONS = 5
QUNS_QUIET_TIME = 6
QUNS_APP = 7
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4

MDT_EFFECTIVE_DPI = 0
DARK_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

# SetProcessDpiAwarenessContext constants
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

_lock = threading.Lock()
_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    with _lock:
        if key in _warned:
            return
        _warned.add(key)
    print(f"[Win32] ⚠️ {message}")


def warned() -> tuple[str, ...]:
    with _lock:
        return tuple(sorted(_warned))


# ── Capability descriptor ────────────────────────────────────────────────────

@dataclass
class Capabilities:
    """What this machine/process can actually do. Read once at startup."""

    windows: bool = IS_WINDOWS
    user32: bool = False
    dwmapi: bool = False
    shcore: bool = False
    shell32: bool = False
    dpi_aware: bool = False
    capture_exclusion: bool = False
    layered_windows: bool = False
    backdrop: bool = False
    rounded_corners: bool = False
    dark_mode: bool = True
    transparency: bool = True
    animations: bool = True
    dnd: bool = False
    reasons: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


CAPS = Capabilities()

# ── ctypes plumbing ──────────────────────────────────────────────────────────

if IS_WINDOWS:  # pragma: no cover - Windows-only
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        CAPS.user32 = True
    except Exception as exc:
        user32 = None
        CAPS.reasons["user32"] = str(exc)

    try:
        dwmapi = ctypes.WinDLL("dwmapi")
        CAPS.dwmapi = True
    except Exception as exc:
        dwmapi = None
        CAPS.reasons["dwmapi"] = str(exc)

    try:
        shcore = ctypes.WinDLL("shcore")
        CAPS.shcore = True
    except Exception as exc:
        shcore = None
        CAPS.reasons["shcore"] = str(exc)

    try:
        shell32 = ctypes.WinDLL("shell32")
        CAPS.shell32 = True
    except Exception as exc:
        shell32 = None
        CAPS.reasons["shell32"] = str(exc)
else:  # pragma: no cover - non-Windows
    user32 = dwmapi = shcore = shell32 = None
    CAPS.reasons["platform"] = f"not windows ({sys.platform})"


def _hwnd(window: Any) -> int:
    """Extract an HWND from a Qt window (or pass an int straight through)."""
    if window is None:
        return 0
    if isinstance(window, int):
        return window
    for attr in ("winId",):
        fn = getattr(window, attr, None)
        if callable(fn):
            try:
                value = int(fn())
                if value:
                    return value
            except Exception:
                pass
    try:
        return int(window)
    except Exception:
        return 0


# ── Style helpers ────────────────────────────────────────────────────────────

def get_ex_style(hwnd: int) -> int:
    if not CAPS.user32 or not hwnd:
        return 0
    try:
        return int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF
    except Exception:
        return 0


def set_ex_style(hwnd: int, style: int) -> bool:
    if not CAPS.user32 or not hwnd:
        return False
    try:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(style))
        return True
    except Exception as exc:
        _warn_once("exstyle", f"SetWindowLongW failed: {exc}")
        return False


def make_click_through(window: Any, enabled: bool = True) -> bool:
    """Let mouse events fall through an overlay window.

    Requires ``WS_EX_LAYERED`` on Win10 (a top-level window without it will
    simply not receive the style). We always set ``WS_EX_NOACTIVATE`` too so
    the overlay never steals focus from whatever the user is doing.
    """
    hwnd = _hwnd(window)
    if not hwnd or not CAPS.user32:
        CAPS.reasons.setdefault("click_through", "no user32/hwnd")
        return False
    try:
        style = get_ex_style(hwnd)
        if enabled:
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            style &= ~WS_EX_APPWINDOW
        else:
            style &= ~WS_EX_TRANSPARENT
        ok = set_ex_style(hwnd, style)
        CAPS.layered_windows = ok
        return ok
    except Exception as exc:
        _warn_once("click_through", f"click-through unavailable: {exc}")
        return False


def keep_on_top(window: Any, enabled: bool = True, no_activate: bool = True) -> bool:
    hwnd = _hwnd(window)
    if not hwnd or not CAPS.user32:
        return False
    try:
        flags = SWP_NOMOVE | SWP_NOSIZE
        if no_activate:
            flags |= SWP_NOACTIVATE
        user32.SetWindowPos(hwnd, HWND_TOPMOST if enabled else HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        return True
    except Exception as exc:
        _warn_once("topmost", f"SetWindowPos failed: {exc}")
        return False


def exclude_from_capture(window: Any, enabled: bool = True) -> bool:
    """Hide the overlay from screen recordings / screenshots / Teams share.

    ``WDA_EXCLUDEFROMCAPTURE`` exists only from Windows 10 build 19041 (2004).
    On older builds it returns 0 and we fall back to plain ``WDA_MONITOR`` —
    which hides from *some* capture paths without blanking the real screen.
    """
    hwnd = _hwnd(window)
    if not hwnd or not CAPS.user32:
        return False
    affinity = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
    try:
        ok = bool(user32.SetWindowDisplayAffinity(hwnd, affinity))
        if ok:
            CAPS.capture_exclusion = enabled
            return True
        if enabled:
            ok = bool(user32.SetWindowDisplayAffinity(hwnd, WDA_MONITOR))
            if ok:
                CAPS.capture_exclusion = True
                CAPS.reasons["capture"] = "pre-2004 build: WDA_MONITOR fallback"
                return True
        CAPS.reasons["capture"] = "SetWindowDisplayAffinity rejected"
        return False
    except Exception as exc:
        _warn_once("capture", f"capture exclusion unavailable: {exc}")
        return False


def apply_dwm_polish(window: Any, dark: bool = True, rounded: bool = True) -> dict:
    """Rounded corners + immersive dark titlebar + transient backdrop.

    Each attribute is applied independently: Win10 2004 accepts only some of
    them and returns ``E_INVALIDARG`` for the rest.
    """
    result = {"dark": False, "rounded": False, "backdrop": False}
    hwnd = _hwnd(window)
    if not hwnd or not CAPS.dwmapi:
        return result
    try:
        value = ctypes.c_int(1 if dark else 0)
        result["dark"] = dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value), ctypes.sizeof(value)) == 0
        if result["dark"]:
            CAPS.dark_mode = dark
    except Exception:
        pass
    try:
        pref = ctypes.c_int(DWMWCP_ROUND if rounded else 1)
        result["rounded"] = dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(pref), ctypes.sizeof(pref)) == 0
        if result["rounded"]:
            CAPS.rounded_corners = rounded
    except Exception:
        pass
    try:
        backdrop = ctypes.c_int(DWMSBT_TRANSIENTWINDOW)
        result["backdrop"] = dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(backdrop), ctypes.sizeof(backdrop)) == 0
        if result["backdrop"]:
            CAPS.backdrop = True
    except Exception:
        pass
    return result


def enable_per_monitor_dpi() -> bool:
    """Must be called *before* QApplication is constructed."""
    if not IS_WINDOWS:
        return False
    try:
        ok = bool(user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)))
        if ok:
            CAPS.dpi_aware = True
            return True
    except Exception:
        pass
    try:
        # Fall back to the Win8.1 API, then the Vista one.
        shcore.SetProcessDpiAwareness(2)
        CAPS.dpi_aware = True
        return True
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
        CAPS.dpi_aware = True
        CAPS.reasons["dpi"] = "system-DPI only"
        return True
    except Exception as exc:
        CAPS.reasons["dpi"] = str(exc)
        return False


def _spi_bool(action: int, default: bool = True) -> bool:
    if not CAPS.user32:
        return default
    try:
        buf = ctypes.c_int(1 if default else 0)
        ok = user32.SystemParametersInfoW(action, 0, ctypes.byref(buf), 0)
        if not ok:
            return default
        return bool(buf.value)
    except Exception:
        return default


def animations_enabled() -> bool:
    """Windows "Show animations in Windows" toggle → reduced-motion signal."""
    CAPS.animations = _spi_bool(SPI_GETCLIENTAREAANIMATION, True)
    return CAPS.animations


def transparency_enabled() -> bool:
    """Read the Personalize\\EnableTransparency value."""
    if not IS_WINDOWS:
        return True
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, DARK_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "EnableTransparency")
            CAPS.transparency = bool(int(value))
            return CAPS.transparency
    except Exception:
        CAPS.transparency = True
        return True


def system_dark_mode() -> bool:
    """AppsUseLightTheme → 0 means dark."""
    if not IS_WINDOWS:
        return True
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, DARK_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            CAPS.dark_mode = int(value) == 0
            return CAPS.dark_mode
    except Exception:
        CAPS.dark_mode = True
        return True


def do_not_disturb() -> bool:
    """True when Windows is in a notification-suppressing mode.

    Busy / full-screen / presentation / quiet-hours all count. We use this to
    suppress mic earcons and to soft-mute the perimeter glow.
    """
    if not CAPS.shell32:
        return False
    try:
        state = ctypes.c_int(0)
        if shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
        suppressing = {
            QUNS_BUSY,
            QUNS_RUNNING_D3D_FULL_SCREEN,
            QUNS_PRESENTATION_MODE,
            QUNS_QUIET_TIME,
        }
        CAPS.dnd = state.value in suppressing
        return CAPS.dnd
    except Exception:
        return False


# ── Monitor geometry ─────────────────────────────────────────────────────────

@dataclass
class Monitor:
    index: int
    x: int
    y: int
    width: int
    height: int
    primary: bool = False
    name: str = ""
    dpr: float = 1.0

    @property
    def geometry(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.width and self.y <= py < self.y + self.height


def enumerate_monitors() -> list[Monitor]:
    """EnumDisplayMonitors with virtual-desktop coordinates.

    Falls back to a single synthetic monitor sized from Qt when unavailable.
    """
    monitors: list[Monitor] = []
    if IS_WINDOWS and CAPS.user32 and wintypes is not None:
        try:
            MonitorEnumProc = ctypes.WINFUNCTYPE(
                ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                ctypes.POINTER(wintypes.RECT), ctypes.c_double)

            class MONITORINFOEXW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_ulong),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", ctypes.c_ulong),
                    ("szDevice", ctypes.c_wchar * 32),
                ]

            out: list[Monitor] = []

            def _cb(hmon, hdc, lprc, data):  # noqa: ANN001
                info = MONITORINFOEXW()
                info.cbSize = ctypes.sizeof(info)
                try:
                    if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                        rect = info.rcMonitor
                        out.append(Monitor(
                            index=len(out),
                            x=rect.left, y=rect.top,
                            width=rect.right - rect.left,
                            height=rect.bottom - rect.top,
                            primary=bool(info.dwFlags & 1),
                            name=str(info.szDevice),
                        ))
                except Exception:
                    pass
                return 1

            proc = MonitorEnumProc(_cb)
            user32.EnumDisplayMonitors(0, None, proc, 0)
            monitors = out
        except Exception as exc:
            _warn_once("monitors", f"EnumDisplayMonitors failed: {exc}")
    if not monitors:
        # Non-Windows or API failure: single monitor from Qt if possible.
        try:
            from PySide6.QtGui import QGuiApplication

            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                geo = screen.geometry()
                monitors = [Monitor(0, geo.x(), geo.y(), geo.width(), geo.height(),
                                    primary=True, dpr=screen.devicePixelRatio())]
        except Exception:
            monitors = [Monitor(0, 0, 0, 1920, 1080, primary=True)]
    return monitors


def monitor_for_point(monitors: list[Monitor], x: int, y: int) -> Monitor:
    for mon in monitors:
        if mon.contains(x, y):
            return mon
    return monitors[0] if monitors else Monitor(0, 0, 0, 1920, 1080, primary=True)


# ── Startup probe ────────────────────────────────────────────────────────────

def probe(warn: bool = True) -> Capabilities:
    """Populate and return :data:`CAPS`. Must run before QApplication."""
    if not IS_WINDOWS:
        CAPS.reasons.setdefault("platform", f"not windows ({sys.platform})")
        if warn:
            _warn_once("nonwin", "Windows native features disabled (non-Windows host)")
        return CAPS
    enable_per_monitor_dpi()
    # Probe, don't apply: these only read state.
    system_dark_mode()
    transparency_enabled()
    animations_enabled()
    do_not_disturb()
    monitors = enumerate_monitors()
    CAPS.reasons.setdefault("monitors", f"{len(monitors)} display(s)")
    return CAPS


def summary() -> str:
    caps = CAPS.to_dict()
    reasons = caps.pop("reasons", {})
    bits = [f"{k}={'on' if v else 'off'}" for k, v in caps.items() if isinstance(v, bool)]
    return ", ".join(bits) + (f" | {reasons}" if reasons else "")


__all__ = [
    "IS_WINDOWS",
    "Capabilities",
    "CAPS",
    "Monitor",
    "probe",
    "summary",
    "warned",
    "enable_per_monitor_dpi",
    "make_click_through",
    "keep_on_top",
    "exclude_from_capture",
    "apply_dwm_polish",
    "system_dark_mode",
    "transparency_enabled",
    "animations_enabled",
    "do_not_disturb",
    "enumerate_monitors",
    "monitor_for_point",
    "get_ex_style",
]
