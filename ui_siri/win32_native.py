"""FRIDAY ambient UI — Windows 10/11 native behaviour layer.

Everything here is **best-effort and non-fatal**. The contract (see
``docs/UI_AUDIT_CONTRACT.md`` §4.5) is:

- Every public function returns a plain value and never raises.
- Every function reports *why* it degraded, so the UI can log it once and move
  on instead of silently looking broken.
- Importing this module on Linux/macOS must succeed (it is imported by the
  cross-platform test-suite).

Feature surface
---------------
- Click-through (``WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE``)
- Always-on-top / bottom / no-activate z-ordering
- Capture exclusion (``SetWindowDisplayAffinity`` WDA_EXCLUDEFROMCAPTURE)
- Per-monitor DPI awareness v2
- Dark/light theme + transparency + reduced-motion probes
- Do-Not-Disturb probe (``SHQueryUserNotificationState``)
- DWM rounded corners / system backdrop / immersive dark titlebar
- Monitor enumeration with virtual-desktop coordinates
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
DWMWA_BORDER_COLOR = 34
DWMWA_NCRENDERING_ENABLED = 1
DWMWA_NCRENDERING_POLICY = 2
DWMWCP_ROUND = 2
DWMWCP_DONOTROUND = 1
DWMSBT_TRANSIENTWINDOW = 3
DWMSBT_MAINWINDOW = 2
DWMNCRP_DISABLED = 1
#: COLORREF sentinel: DWM draws no border at all.
DWMWA_COLOR_NONE = 0xFFFFFFFE

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

    # Resolved at *instantiation*, not import: a dataclass default is frozen at
    # class-creation time, so `windows: bool = IS_WINDOWS` would latch whatever
    # the platform was when the module loaded and silently ignore any later
    # change (and made the capability table untestable).
    windows: bool = field(default_factory=lambda: IS_WINDOWS)
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
    build: int = 0
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


#: Common Win32 error codes for the failures this module actually sees.
_WIN32_ERRORS = {
    0: "ERROR_SUCCESS",
    5: "ERROR_ACCESS_DENIED",
    6: "ERROR_INVALID_HANDLE",
    8: "ERROR_NOT_ENOUGH_MEMORY",
    57: "ERROR_INVALID_PARAMETER",
    87: "ERROR_INVALID_PARAMETER",
    1156: "ERROR_IO_DEVICE",
    127: "ERROR_PROCEDURE_NOT_FOUND",
}


def describe_error(code: int) -> str:
    """Human-readable Win32 error, e.g. ``87 ERROR_INVALID_PARAMETER``."""
    if not code:
        return "no error set"
    name = _WIN32_ERRORS.get(code, "unknown")
    return f"{code} {name}"


def explain_last_error() -> str:
    """Describe the current thread's last Win32 error (``""`` if unset)."""
    if not IS_WINDOWS:
        return ""
    try:
        code = ctypes.get_last_error()
    except Exception:
        return ""
    return describe_error(code) if code else ""


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


# ── ctypes signatures ────────────────────────────────────────────────────────
# ctypes defaults every argument to C ``int`` (32-bit). On 64-bit Windows an
# HWND, HMONITOR or HANDLE is pointer-sized, so passing one as a Python int
# silently *truncates* it -- the call then fails, or worse, addresses a
# different window. Every function we use therefore gets explicit argtypes and
# restypes. Everything here is best-effort: on a non-Windows host, or if a
# symbol is missing on an older build, we simply skip it.

_LRESULT = ctypes.c_ssize_t          # LONG_PTR: 64-bit on 64-bit Windows
_UINT_PTR = ctypes.c_size_t

#: (library attribute name, function name, argtypes, restype)
_SIGNATURES: tuple[tuple[str, str, tuple, Any], ...] = (
    # window styles
    ("user32", "GetWindowLongW", (wintypes.HWND, ctypes.c_int), ctypes.c_long),
    ("user32", "GetWindowLongPtrW", (wintypes.HWND, ctypes.c_int), _LRESULT),
    ("user32", "GetAsyncKeyState", (ctypes.c_int,), ctypes.c_short),
    ("user32", "SetWindowLongW", (wintypes.HWND, ctypes.c_int, ctypes.c_long),
     ctypes.c_long),
    ("user32", "SetWindowLongPtrW", (wintypes.HWND, ctypes.c_int, _LRESULT),
     _LRESULT),
    ("user32", "SetWindowPos",
     (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
      ctypes.c_int, ctypes.c_int, _UINT_PTR), wintypes.BOOL),
    # capture exclusion
    ("user32", "SetWindowDisplayAffinity", (wintypes.HWND, wintypes.DWORD),
     wintypes.BOOL),
    # theme / DPI / notification state
    ("user32", "SystemParametersInfoW",
     (wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT),
     wintypes.BOOL),
    ("user32", "SetProcessDPIAware", (), wintypes.BOOL),
    ("user32", "SetProcessDpiAwarenessContext", (ctypes.c_void_p,),
     wintypes.BOOL),
    ("user32", "GetMonitorInfoW", (wintypes.HMONITOR, ctypes.c_void_p),
     wintypes.BOOL),
    ("user32", "EnumDisplayMonitors",
     (wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p, _LRESULT), wintypes.BOOL),
    ("shcore", "SetProcessDpiAwareness", (ctypes.c_int,), ctypes.c_long),
    ("shell32", "SHQueryUserNotificationState", (ctypes.c_void_p,),
     ctypes.c_long),
    # DwmSetWindowAttribute(HWND, DWORD, LPCVOID, DWORD) -> HRESULT
    ("dwmapi", "DwmSetWindowAttribute",
     (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD),
     ctypes.c_long),
)


def _declare_signatures() -> list[str]:
    """Apply :data:`_SIGNATURES`. Returns the names that could not be bound."""
    failed: list[str] = []
    for lib_name, func_name, argtypes, restype in _SIGNATURES:
        lib = globals().get(lib_name)
        if lib is None:
            continue
        try:
            func = getattr(lib, func_name)
        except Exception:
            # Absent on this Windows build -- callers already guard for that.
            continue
        try:
            func.argtypes = argtypes
            func.restype = restype
        except Exception as exc:  # pragma: no cover - defensive
            failed.append(f"{func_name}: {exc}")
    return failed


if IS_WINDOWS:  # pragma: no cover - Windows-only
    _SIG_FAILURES = _declare_signatures()
    if _SIG_FAILURES:
        CAPS.reasons["signatures"] = "; ".join(_SIG_FAILURES)
else:  # pragma: no cover - non-Windows
    _SIG_FAILURES: list[str] = []


# ── Style helpers ────────────────────────────────────────────────────────────

def get_ex_style(hwnd: int) -> int:
    """Read GWL_EXSTYLE.

    ``GetWindowLongW`` returns a 32-bit ``LONG``, which is correct for styles on
    every architecture -- but the *call* still needs HWND typed as a pointer,
    which :func:`_declare_signatures` handles. We prefer ``GetWindowLongPtrW``
    when present so the intent is explicit, then mask to the low 32 bits.
    """
    if not CAPS.user32 or not hwnd:
        return 0
    for name in ("GetWindowLongPtrW", "GetWindowLongW"):
        fn = getattr(user32, name, None)
        if fn is None:
            continue
        try:
            return int(fn(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF
        except Exception:
            continue
    return 0


def set_ex_style(hwnd: int, style: int) -> bool:
    """Write GWL_EXSTYLE.

    Prefers ``SetWindowLongPtrW`` (pointer-sized value) and falls back to
    ``SetWindowLongW``. The value is passed as a bare Python int with a declared
    argtype, never ``ctypes.c_long(...)``: on x64 the ctypes default would
    already be 32-bit, so wrapping it only hides the truncation.
    """
    if not CAPS.user32 or not hwnd:
        return False
    for name in ("SetWindowLongPtrW", "SetWindowLongW"):
        fn = getattr(user32, name, None)
        if fn is None:
            continue
        try:
            fn(hwnd, GWL_EXSTYLE, int(style))
            return True
        except Exception:
            continue
    _warn_once("exstyle", "SetWindowLong(Ptr)W failed for WS_EX styles")
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
        # Keep the real error: "rejected" alone cannot distinguish a pre-2004
        # build (ERROR_INVALID_PARAMETER) from an unsupported window style
        # (ERROR_ACCESS_DENIED on some layered/tool windows).
        first_error = ctypes.get_last_error()
        if enabled:
            ok = bool(user32.SetWindowDisplayAffinity(hwnd, WDA_MONITOR))
            if ok:
                CAPS.capture_exclusion = True
                CAPS.reasons["capture"] = (
                    f"WDA_EXCLUDEFROMCAPTURE rejected ({describe_error(first_error)}); "
                    f"fell back to WDA_MONITOR")
                return True
        CAPS.reasons["capture"] = (
            f"SetWindowDisplayAffinity rejected ({describe_error(first_error)})")
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


def apply_dwm_polish_no_backdrop(window: Any, dark: bool = True,
                                 rounded: bool = True) -> dict:
    """DWM polish WITHOUT system backdrop.

    SiriWindow must never get DWMWA_SYSTEMBACKDROP_TYPE (Mica/Acrylic) —
    that paints a grey rounded rectangle over the whole window, destroying
    the 100% transparent "orb floating on desktop" look. Only dark mode and
    rounded corners are applied.
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
    # NOTE: deliberately do NOT set DWMWA_SYSTEMBACKDROP_TYPE here.
    return result


def apply_dwm_naked(window: Any) -> bool:
    """Strip **all** DWM decoration from an HWND: no rounded corners, no
    border, no non-client shadow.

    SiriWindow must present a completely naked window on Windows 11 — zero
    visible outline, border, shadow, or rectangle anywhere.  This is the
    opposite of ``apply_dwm_polish``: we explicitly request
    ``DWMWCP_DONOTROUND`` so DWM does *not* draw its 1px rounded border,
    and ``DWMWA_COLOR_NONE`` so there is no border color to paint.
    ``DWMNCRP_DISABLED`` turns off non-client rendering (shadow) too.

    Returns True if every attribute was applied; False on any failure.
    Every failure is non-fatal: the window still works, it just may show a
    subtle DWM outline on some builds.
    """
    hwnd = _hwnd(window)
    if not hwnd or not CAPS.dwmapi:
        return False
    ok = True
    try:
        pref = ctypes.c_int(DWMWCP_DONOTROUND)
        ok = dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(pref), ctypes.sizeof(pref)) == 0
    except Exception:
        ok = False
    try:
        none = ctypes.c_int(DWMWA_COLOR_NONE)
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_BORDER_COLOR,
            ctypes.byref(none), ctypes.sizeof(none))
    except Exception:
        ok = False
    try:
        policy = ctypes.c_int(DWMNCRP_DISABLED)
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_NCRENDERING_POLICY,
            ctypes.byref(policy), ctypes.sizeof(policy))
    except Exception:
        ok = False
    return ok


def enable_alpha_buffer() -> bool:
    """Ask Qt for an alpha channel on every window by default.

    MUST be called before the ``QGuiApplication`` is constructed: Qt reads this
    flag once, when it creates the platform windows. Without it every
    ``setColor(QColor(0,0,0,0))`` is ignored and the ambient overlay renders as
    an opaque black rectangle — which looks exactly like "the UI is broken".
    """
    try:
        from PySide6.QtQuick import QQuickWindow

        QQuickWindow.setDefaultAlphaBuffer(True)
        return True
    except Exception as exc:
        _warn_once("alpha", f"transparent windows unavailable ({exc})")
        return False


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
                wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                ctypes.POINTER(wintypes.RECT), _LRESULT)

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

# ── OS build ─────────────────────────────────────────────────────────────────

class OSVERSIONINFOEXW(ctypes.Structure):
    """RTL_OSVERSIONINFOEXW: the truthful version record (RtlGetVersion)."""

    _fields_ = [
        ("dwOSVersionInfoSize", ctypes.c_ulong),
        ("dwMajorVersion", ctypes.c_ulong),
        ("dwMinorVersion", ctypes.c_ulong),
        ("dwBuildNumber", ctypes.c_ulong),
        ("dwPlatformId", ctypes.c_ulong),
        ("szCSDVersion", ctypes.c_wchar * 128),
        ("wServicePackMajor", ctypes.c_ushort),
        ("wServicePackMinor", ctypes.c_ushort),
        ("wSuiteMask", ctypes.c_ushort),
        ("wProductType", ctypes.c_ubyte),
        ("wReserved", ctypes.c_ubyte),
    ]


# Feature gates below are build-number based, so read the real build once.
# (Windows 10 20H1 = 19041, Windows 11 21H2 = 22000, 22H2 = 22621.)

def windows_build() -> int:
    """Best-effort Windows build number (0 when unavailable)."""
    if not IS_WINDOWS:
        return 0
    try:
        return int(sys.getwindowsversion().build)
    except Exception:
        pass
    # Fall back to RtlGetVersion: GetVersionEx lies unless the process is
    # manifested, and RtlGetVersion reports the true build.
    try:
        ntdll = ctypes.WinDLL("ntdll")
        version = OSVERSIONINFOEXW()
        version.dwOSVersionInfoSize = ctypes.sizeof(version)
        if ntdll.RtlGetVersion(ctypes.byref(version)) == 0:
            return int(version.dwBuildNumber)
    except Exception:
        pass
    return 0


def _has(lib: Any, *names: str) -> bool:
    """True when ``lib`` exports every one of ``names``."""
    if lib is None:
        return False
    return all(getattr(lib, name, None) is not None for name in names)


class _Unset:
    """Sentinel: a reason must be recorded for every capability that is False."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<unset>"


_UNSET = _Unset()


def _reason(key: str, why: str) -> None:
    CAPS.reasons[key] = why


def _require(key: str, ok: bool, why: str) -> bool:
    """Set-and-explain for one capability. Every False gets a reason."""
    if not ok:
        _reason(key, why)
    return ok


def probe_capabilities() -> Capabilities:
    """Determine what this machine can actually do. **No window required.**

    This is a pure capability query -- it only asks whether the relevant export
    exists and whether the OS build is new enough. The earlier version reused
    the *action* helpers (``exclude_from_capture``, ``get_ex_style``,
    ``apply_dwm_polish``, ``do_not_disturb``), which all need a live HWND or
    current notification state. With no window yet they returned False and
    wrote nothing into ``reasons``, so a fully-capable Windows 11 box reported
    five capabilities as unavailable. Capabilities are now derived from
    presence + build, and *every* False records why.
    """
    build = CAPS.build = windows_build()

    # ── libraries ───────────────────────────────────────────────────────────
    # Derived from what is actually bound right now, not from the flags set at
    # import time: those latch the platform the module was first loaded on and
    # would silently disagree with reality.
    for key in ("user32", "dwmapi", "shcore", "shell32"):
        loaded = globals().get(key) is not None
        setattr(CAPS, key, loaded)
        _require(key, loaded, "library failed to load")

    if not IS_WINDOWS:
        # Still explain every capability: silent degradation is the bug we are
        # fixing, and "no reason recorded" is exactly what made it invisible.
        _reason("platform", f"not windows ({sys.platform})")
        for key in ("layered_windows", "capture_exclusion", "backdrop",
                    "rounded_corners", "dnd", "dpi_aware"):
            _reason(key, f"not windows ({sys.platform})")
        return CAPS

    # ── per-monitor DPI awareness ───────────────────────────────────────────
    # The capability is "this process *can* declare per-monitor DPI awareness",
    # i.e. which export exists -- NOT whether ``enable_per_monitor_dpi()`` has
    # already been called. Conflating the two made the probe report False on a
    # fully capable Windows 11 box, because the probe runs before the window
    # exists. Preference order: Win10 1703+ (user32, per-monitor-v2),
    # Win8.1 (shcore), Vista+ (user32 legacy system-DPI).
    dpi_export = ""
    if _has(user32, "SetProcessDpiAwarenessContext"):
        dpi_export = "user32.SetProcessDpiAwarenessContext (per-monitor v2)"
    elif _has(shcore, "SetProcessDpiAwareness"):
        dpi_export = "shcore.SetProcessDpiAwareness (per-monitor v1)"
    elif _has(user32, "SetProcessDPIAware"):
        dpi_export = "user32.SetProcessDPIAware (system DPI only)"
    CAPS.dpi_aware = _require(
        "dpi_aware", bool(dpi_export),
        "no DPI-awareness export found: user32 lacks "
        "SetProcessDpiAwarenessContext and SetProcessDPIAware, and shcore "
        "lacks SetProcessDpiAwareness")
    if dpi_export:
        _reason("dpi_aware", f"via {dpi_export}")

    # ── layered / click-through windows ─────────────────────────────────────
    # GetWindowLongPtrW/SetWindowLongPtrW on x64, the Long forms on x86. Either
    # pair is sufficient; WS_EX_LAYERED itself has existed since Windows 2000.
    layered = (_has(user32, "SetWindowLongPtrW", "GetWindowLongPtrW")
               or _has(user32, "SetWindowLongW", "GetWindowLongW"))
    CAPS.layered_windows = _require(
        "layered_windows", layered,
        "user32 exports neither Get/SetWindowLongPtrW nor Get/SetWindowLongW")

    # ── capture exclusion ───────────────────────────────────────────────────
    # WDA_EXCLUDEFROMCAPTURE requires Windows 10 2004 (build 19041).
    has_affinity = _has(user32, "SetWindowDisplayAffinity")
    if not has_affinity:
        CAPS.capture_exclusion = _require(
            "capture_exclusion", False,
            "SetWindowDisplayAffinity is missing from user32")
    elif build and build < 19041:
        CAPS.capture_exclusion = _require(
            "capture_exclusion", False,
            f"build {build} < 19041; WDA_EXCLUDEFROMCAPTURE unavailable "
            f"(WDA_MONITOR fallback only)")
    else:
        CAPS.capture_exclusion = True

    # ── rounded corners (Windows 11 22000) ──────────────────────────────────
    has_dwm = _has(dwmapi, "DwmSetWindowAttribute")
    if not has_dwm:
        CAPS.rounded_corners = _require(
            "rounded_corners", False, "DwmSetWindowAttribute is missing from dwmapi")
    elif build and build < 22000:
        CAPS.rounded_corners = _require(
            "rounded_corners", False,
            f"build {build} < 22000; DWMWA_WINDOW_CORNER_PREFERENCE unavailable")
    else:
        CAPS.rounded_corners = True

    # ── Mica/acrylic backdrop (Windows 11 22621) ────────────────────────────
    if not has_dwm:
        CAPS.backdrop = _require(
            "backdrop", False, "DwmSetWindowAttribute is missing from dwmapi")
    elif build and build < 22621:
        CAPS.backdrop = _require(
            "backdrop", False,
            f"build {build} < 22621; DWMWA_SYSTEMBACKDROP_TYPE unavailable")
    else:
        CAPS.backdrop = True

    # ── Do Not Disturb query ────────────────────────────────────────────────
    # This capability means "we can *ask*"; the live answer is do_not_disturb().
    CAPS.dnd = _require(
        "dnd", _has(shell32, "SHQueryUserNotificationState"),
        "SHQueryUserNotificationState is missing from shell32")

    # ── theme/appearance reads (read-only probes, safe to run now) ──────────
    CAPS.dark_mode = system_dark_mode()
    CAPS.transparency = transparency_enabled()
    CAPS.animations = animations_enabled()

    monitors = enumerate_monitors()
    _reason("monitors", f"{len(monitors)} display(s)")
    return CAPS


def probe(warn: bool = True) -> Capabilities:
    """Populate and return :data:`CAPS`. Must run before QApplication."""
    caps = probe_capabilities()
    if warn and not caps.windows:
        _warn_once("nonwin", "Windows native features disabled (non-Windows host)")
    return caps


def summary() -> str:
    caps = CAPS.to_dict()
    reasons = caps.pop("reasons", {})
    bits = [f"{k}={'on' if v else 'off'}" for k, v in caps.items() if isinstance(v, bool)]
    return ", ".join(bits) + (f" | {reasons}" if reasons else "")


VK_ESCAPE = 0x1B


def key_is_down(virtual_key: int) -> bool:
    """Non-invasive global key-state query; always False off Windows."""
    if not IS_WINDOWS or not CAPS.user32:
        return False
    try:
        return bool(user32.GetAsyncKeyState(int(virtual_key)) & 0x8000)
    except Exception:
        return False


__all__ = [
    "IS_WINDOWS",
    "Capabilities",
    "CAPS",
    "Monitor",
    "VK_ESCAPE",
    "key_is_down",
    "probe",
    "summary",
    "warned",
    "enable_per_monitor_dpi",
    "make_click_through",
    "keep_on_top",
    "exclude_from_capture",
    "apply_dwm_polish",
    "apply_dwm_naked",
    "system_dark_mode",
    "transparency_enabled",
    "animations_enabled",
    "do_not_disturb",
    "enumerate_monitors",
    "monitor_for_point",
    "get_ex_style",
    "probe_capabilities",
    "windows_build",
    "describe_error",
    "explain_last_error",
]