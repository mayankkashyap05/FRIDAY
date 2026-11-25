"""Native hit testing, topmost Z-order management, and non-activation, isolated from the rest of the UI.

No APIs are loaded on non-Windows hosts. Windows integration requires native
multi-monitor/DPI verification; coordinates here are converted from physical
screen pixels to Qt logical coordinates.
"""
import ctypes
import sys

IS_WINDOWS = sys.platform == 'win32'

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOREDRAW = 0x0008
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_NOOWNERZORDER = 0x0200

GW_HWNDPREV = 3
WM_WINDOWPOSCHANGING = 0x0046

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_SWITCHEND = 0x0015
EVENT_SYSTEM_MINIMIZEEND = 0x0017
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002

HWND_TOPMOST = ctypes.c_void_p(-1)
HWND_NOTOPMOST = ctypes.c_void_p(-2)
HWND_BOTTOM = ctypes.c_void_p(1)

if IS_WINDOWS:
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL

    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long

    if hasattr(user32, 'GetWindowLongPtrW'):
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    if hasattr(user32, 'SetWindowLongPtrW'):
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t

    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL

    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND

    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL

    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    WINEVENTPROC = ctypes.WINFUNCTYPE(
        None,
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.HWND,
        wintypes.LONG,
        wintypes.LONG,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    user32.SetWinEventHook.argtypes = [
        wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
        WINEVENTPROC, wintypes.DWORD, wintypes.DWORD, wintypes.UINT,
    ]
    user32.SetWinEventHook.restype = wintypes.HANDLE
    user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
    user32.UnhookWinEvent.restype = wintypes.BOOL

    class WINDOWPOS(ctypes.Structure):
        _fields_ = [
            ('hwnd', wintypes.HWND),
            ('hwndInsertAfter', wintypes.HWND),
            ('x', ctypes.c_int),
            ('y', ctypes.c_int),
            ('cx', ctypes.c_int),
            ('cy', ctypes.c_int),
            ('flags', wintypes.UINT),
        ]
else:
    wintypes = None
    user32 = None
    WINEVENTPROC = None
    WINDOWPOS = None


def _get_window_long_ptr(hwnd, index):
    if hasattr(user32, 'GetWindowLongPtrW'):
        return user32.GetWindowLongPtrW(hwnd, index)
    return user32.GetWindowLongW(hwnd, index)


def _set_window_long_ptr(hwnd, index, value):
    if hasattr(user32, 'SetWindowLongPtrW'):
        return user32.SetWindowLongPtrW(hwnd, index, value)
    return user32.SetWindowLongW(hwnd, index, value)


def _has_visible_external_above(hwnd, allow_above):
    """Walk upward in Z-order; return True if any visible external window sits above hwnd."""
    cur = hwnd
    for _ in range(64):
        cur = user32.GetWindow(cur, GW_HWNDPREV)
        if not cur:
            break
        cur_int = int(cur)
        if cur_int in allow_above:
            continue
        if not user32.IsWindowVisible(cur):
            continue
        rect = wintypes.RECT()
        if user32.GetWindowRect(cur, ctypes.byref(rect)):
            if rect.right <= rect.left or rect.bottom <= rect.top:
                continue
        return True
    return False


def configure_overlay(widget):
    if not IS_WINDOWS:
        return
    hwnd = int(widget.winId())
    style = _get_window_long_ptr(hwnd, GWL_EXSTYLE)
    new_style = style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
    _set_window_long_ptr(hwnd, GWL_EXSTYLE, new_style)
    user32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_FRAMECHANGED,
    )


def set_accepts_focus(widget, accepts):
    """Temporarily allow activation when the user explicitly selects an input."""
    if not IS_WINDOWS:
        return
    hwnd = int(widget.winId())
    style = _get_window_long_ptr(hwnd, GWL_EXSTYLE)
    style = (style & ~WS_EX_NOACTIVATE) if accepts else (style | WS_EX_NOACTIVATE)
    _set_window_long_ptr(hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW)


def ensure_topmost(hwnd, *, force=False, allow_above_hwnds=()):
    """Ensure hwnd is topmost without stealing focus or flickering.

    Returns True if a Z-order re-assertion was performed, False otherwise.
    """
    if not IS_WINDOWS or not hwnd:
        return False
    hwnd_int = int(hwnd)
    if not user32.IsWindow(hwnd_int) or not user32.IsWindowVisible(hwnd_int):
        return False

    allow_above = {int(h) for h in allow_above_hwnds if h}
    style = _get_window_long_ptr(hwnd_int, GWL_EXSTYLE)
    style_stripped = (style & WS_EX_TOPMOST) == 0
    occluded = False if force else _has_visible_external_above(hwnd_int, allow_above)

    if not force and not style_stripped and not occluded:
        return False

    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER
    user32.SetWindowPos(hwnd_int, HWND_TOPMOST, 0, 0, 0, 0, flags)

    if style_stripped or _has_visible_external_above(hwnd_int, allow_above):
        if style_stripped:
            _set_window_long_ptr(hwnd_int, GWL_EXSTYLE, style | WS_EX_TOPMOST)
        user32.SetWindowPos(hwnd_int, HWND_NOTOPMOST, 0, 0, 0, 0, flags | SWP_NOREDRAW)
        user32.SetWindowPos(hwnd_int, HWND_TOPMOST, 0, 0, 0, 0, flags)

    for popup_hwnd in allow_above_hwnds:
        if not popup_hwnd:
            continue
        p_int = int(popup_hwnd)
        if p_int != hwnd_int and user32.IsWindow(p_int) and user32.IsWindowVisible(p_int):
            user32.SetWindowPos(p_int, HWND_TOPMOST, 0, 0, 0, 0, flags)

    return True


def enforce_topmost_on_windowposchanging(msg_ptr):
    """Rewrite WM_WINDOWPOSCHANGING Z-order demotions back to HWND_TOPMOST."""
    if not IS_WINDOWS or not msg_ptr:
        return False
    try:
        msg = ctypes.cast(int(msg_ptr), ctypes.POINTER(wintypes.MSG)).contents
        if msg.message != WM_WINDOWPOSCHANGING or not msg.lParam:
            return False
        wp = ctypes.cast(msg.lParam, ctypes.POINTER(WINDOWPOS)).contents
        if wp.flags & SWP_NOZORDER:
            return False
        demote_values = (1, -2, HWND_NOTOPMOST.value, HWND_BOTTOM.value)
        if wp.hwndInsertAfter in demote_values:
            wp.hwndInsertAfter = HWND_TOPMOST.value
            return True
    except Exception:
        return False
    return False


class Win32TopmostGuard:
    """Lightweight event-driven WinEvent hook to re-assert topmost on foreground/Alt+Tab changes."""

    _EVENTS = (
        EVENT_SYSTEM_FOREGROUND,
        EVENT_SYSTEM_SWITCHEND,
        EVENT_SYSTEM_MINIMIZEEND,
    )

    def __init__(self, on_foreground_change):
        self._on_foreground_change = on_foreground_change
        self._hooks = []
        self._callback = None
        self._installed = False

    def install(self):
        if not IS_WINDOWS or self._installed:
            return False
        if WINEVENTPROC is None or user32 is None:
            return False

        def _proc(_hook, _event, _hwnd, _id_object, _id_child, _thread, _time):
            try:
                if self._on_foreground_change is not None:
                    self._on_foreground_change()
            except Exception:
                pass

        self._callback = WINEVENTPROC(_proc)
        flags = WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS
        for event_id in self._EVENTS:
            handle = user32.SetWinEventHook(
                event_id, event_id, None, self._callback, 0, 0, flags
            )
            if handle:
                self._hooks.append(handle)
        self._installed = bool(self._hooks)
        return self._installed

    def uninstall(self):
        if not self._hooks:
            self._installed = False
            self._callback = None
            return
        for handle in self._hooks:
            try:
                if IS_WINDOWS and user32 is not None:
                    user32.UnhookWinEvent(handle)
            except Exception:
                pass
        self._hooks.clear()
        self._installed = False
        self._callback = None

    stop = uninstall


def hit_test(widget, message):
    if not IS_WINDOWS:
        return None
    from PySide6.QtCore import QPointF
    msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
    if msg.message != 0x0084:  # WM_NCHITTEST
        return None
    # GetCursorPos is physical on the DPI-aware Qt process. Use Qt's mapping
    # rather than dividing by a screen's DPR, which fails at mixed-DPI origins.
    from PySide6.QtGui import QCursor
    local = widget.mapFromGlobal(QCursor.pos())
    if not widget.current_path().contains(QPointF(local)):
        return -1  # HTTRANSPARENT
    return 1  # HTCLIENT


def foreground_is_fullscreen(widget):
    """Check whether a different foreground window covers its monitor.

    The comparison is made in Win32 screen coordinates throughout. It deliberately
    excludes this process and shell windows. Non-Windows systems return False.
    """
    if not IS_WINDOWS:
        return False
    class MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                    ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetShellWindow.restype = wintypes.HWND
    user32.GetDesktopWindow.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    hwnd = user32.GetForegroundWindow()
    if not hwnd or hwnd in (user32.GetShellWindow(), user32.GetDesktopWindow(), int(widget.winId())):
        return False
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    import os
    if pid.value == os.getpid():
        return False
    rect = wintypes.RECT()
    monitor = user32.MonitorFromWindow(hwnd, 2)
    info = MONITORINFO(); info.cbSize = ctypes.sizeof(info)
    if not monitor or not user32.GetWindowRect(hwnd, ctypes.byref(rect)) or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    screen = info.rcMonitor
    return rect.left <= screen.left and rect.top <= screen.top and rect.right >= screen.right and rect.bottom >= screen.bottom


def foreground_window():
    """Return (window handle, title), or (0, '') when unavailable."""
    if not IS_WINDOWS:
        return 0, ''
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, ''
    buffer = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return int(hwnd), buffer.value


def clipboard_sequence():
    if not IS_WINDOWS:
        raise RuntimeError('Clipboard sequence is only available on Windows')
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    return user32.GetClipboardSequenceNumber()


def send_paste():
    """Send Ctrl+V without bringing any window to the foreground."""
    if not IS_WINDOWS:
        raise RuntimeError('External paste is only available on Windows')
    # INPUT must include the full union, including MOUSEINPUT, on 64-bit Windows.
    ULONG_PTR = wintypes.WPARAM
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                    ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ULONG_PTR)]
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD),
                    ('time', wintypes.DWORD), ('dwExtraInfo', ULONG_PTR)]
    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [('uMsg', wintypes.DWORD), ('wParamL', wintypes.WORD), ('wParamH', wintypes.WORD)]
    class INPUTUNION(ctypes.Union):
        _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]
    class INPUT(ctypes.Structure):
        _fields_ = [('type', wintypes.DWORD), ('data', INPUTUNION)]
    def key(code, up=False):
        return INPUT(1, INPUTUNION(ki=KEYBDINPUT(code, 0, 2 if up else 0, 0, 0)))
    keys = (INPUT * 4)(key(0x11), key(0x56), key(0x56, True), key(0x11, True))
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    if user32.SendInput(len(keys), keys, ctypes.sizeof(INPUT)) != len(keys):
        raise OSError('Windows did not accept the complete paste keystroke')
