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
