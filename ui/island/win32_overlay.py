"""Native hit testing and non-activation, isolated from the rest of the UI.

No APIs are loaded on non-Windows hosts. Windows integration requires native
multi-monitor/DPI verification; coordinates here are converted from physical
screen pixels to Qt logical coordinates.
"""
import sys

IS_WINDOWS = sys.platform == 'win32'

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long


def configure_overlay(widget):
    if not IS_WINDOWS:
        return
    hwnd = int(widget.winId())
    style = user32.GetWindowLongW(hwnd, -20)
    user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080)


def set_accepts_focus(widget, accepts):
    """Temporarily allow activation when the user explicitly selects an input."""
    if not IS_WINDOWS:
        return
    hwnd = int(widget.winId())
    style = user32.GetWindowLongW(hwnd, -20)
    style = (style & ~0x08000000) if accepts else (style | 0x08000000)
    user32.SetWindowLongW(hwnd, -20, style | 0x00000080)


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
