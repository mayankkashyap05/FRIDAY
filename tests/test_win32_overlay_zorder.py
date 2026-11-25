"""Offline unit tests for Win32 overlay Z-order & topmost guard."""

from __future__ import annotations

import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ui.island import win32_overlay


class _FakeRect(ctypes.Structure):
    _fields_ = [
        ('left', ctypes.c_long),
        ('top', ctypes.c_long),
        ('right', ctypes.c_long),
        ('bottom', ctypes.c_long),
    ]


class _FakeMSG(ctypes.Structure):
    _fields_ = [
        ('hwnd', ctypes.c_void_p),
        ('message', ctypes.c_uint),
        ('wParam', ctypes.c_size_t),
        ('lParam', ctypes.c_ssize_t),
    ]


class _FakeWintypes:
    HWND = ctypes.c_void_p
    UINT = ctypes.c_uint
    BOOL = ctypes.c_int
    DWORD = ctypes.c_uint32
    LONG = ctypes.c_long
    HANDLE = ctypes.c_void_p
    HMODULE = ctypes.c_void_p
    WPARAM = ctypes.c_size_t
    LPARAM = ctypes.c_ssize_t
    RECT = _FakeRect
    MSG = _FakeMSG


class _FakeWINDOWPOS(ctypes.Structure):
    _fields_ = [
        ('hwnd', ctypes.c_void_p),
        ('hwndInsertAfter', ctypes.c_void_p),
        ('x', ctypes.c_int),
        ('y', ctypes.c_int),
        ('cx', ctypes.c_int),
        ('cy', ctypes.c_int),
        ('flags', ctypes.c_uint),
    ]


class _FakeUser32:
    def __init__(self, *, exstyle=0, z_chain=None, visible=None, rects=None):
        self.exstyle = exstyle
        self.z_chain = dict(z_chain or {})
        self.visible = set(visible or ())
        self.rects = dict(rects or {})
        self.set_window_pos_calls = []
        self.set_window_long_calls = []
        self.set_foreground_calls = 0
        self.hooks = []
        self.unhooked = []
        self._next_hook = 100

    def IsWindow(self, hwnd):
        return 1 if hwnd else 0

    def IsWindowVisible(self, hwnd):
        return 1 if int(hwnd) in self.visible else 0

    def GetWindowLongPtrW(self, hwnd, index):
        return self.exstyle

    def SetWindowLongPtrW(self, hwnd, index, value):
        self.set_window_long_calls.append((int(hwnd), index, value))
        old = self.exstyle
        self.exstyle = value
        return old

    def GetWindow(self, hwnd, cmd):
        return self.z_chain.get(int(hwnd), 0)

    def GetWindowRect(self, hwnd, rect_ptr):
        l, t, r, b = self.rects.get(int(hwnd), (0, 0, 800, 600))
        rect = ctypes.cast(rect_ptr, ctypes.POINTER(_FakeRect)).contents
        rect.left, rect.top, rect.right, rect.bottom = l, t, r, b
        return 1

    def SetWindowPos(self, hwnd, insert_after, x, y, cx, cy, flags):
        ia = insert_after.value if isinstance(insert_after, ctypes.c_void_p) else insert_after
        self.set_window_pos_calls.append((int(hwnd), ia, flags))
        # Simulate that HWND_TOPMOST clears external occlusion unless overridden
        if ia == win32_overlay.HWND_TOPMOST.value and int(hwnd) in self.z_chain:
            self._after_topmost(int(hwnd))
        return 1

    def _after_topmost(self, hwnd):
        pass

    def SetForegroundWindow(self, hwnd):
        self.set_foreground_calls += 1
        return 1

    def SetWinEventHook(self, ev_min, ev_max, hmod, proc, pid, tid, flags):
        self._next_hook += 1
        self.hooks.append((ev_min, ev_max, proc, pid, tid, flags, self._next_hook))
        return self._next_hook

    def UnhookWinEvent(self, handle):
        self.unhooked.append(handle)
        return 1


class TestWin32OverlayZOrder(unittest.TestCase):
    def test_non_windows_fallback_is_safe(self):
        with patch.object(win32_overlay, 'IS_WINDOWS', False):
            self.assertFalse(win32_overlay.ensure_topmost(1234))
            self.assertFalse(win32_overlay.enforce_topmost_on_windowposchanging(1234))
            guard = win32_overlay.Win32TopmostGuard(lambda: None)
            self.assertFalse(guard.install())
            guard.stop()

    def test_ensure_topmost_noop_when_already_topmost_and_unoccluded(self):
        u32 = _FakeUser32(
            exstyle=win32_overlay.WS_EX_TOPMOST | win32_overlay.WS_EX_NOACTIVATE,
            z_chain={100: 0},
            visible={100},
        )
        with patch.object(win32_overlay, 'IS_WINDOWS', True), \
             patch.object(win32_overlay, 'user32', u32), \
             patch.object(win32_overlay, 'wintypes', _FakeWintypes):
            self.assertFalse(win32_overlay.ensure_topmost(100))
            self.assertEqual(u32.set_window_pos_calls, [])

    def test_ensure_topmost_ignores_invisible_and_allow_above_hwnds(self):
        # 100 (dock) -> 200 (invisible external) -> 300 (visible FRIDAY popup) -> 0
        u32 = _FakeUser32(
            exstyle=win32_overlay.WS_EX_TOPMOST,
            z_chain={100: 200, 200: 300, 300: 0},
            visible={100, 300},
        )
        with patch.object(win32_overlay, 'IS_WINDOWS', True), \
             patch.object(win32_overlay, 'user32', u32), \
             patch.object(win32_overlay, 'wintypes', _FakeWintypes):
            self.assertFalse(win32_overlay.ensure_topmost(100, allow_above_hwnds=(300,)))
            self.assertEqual(u32.set_window_pos_calls, [])

    def test_ensure_topmost_reasserts_when_external_window_occludes(self):
        # 100 (dock) -> 500 (visible Chrome) -> 0
        u32 = _FakeUser32(
            exstyle=win32_overlay.WS_EX_TOPMOST,
            z_chain={100: 500, 500: 0},
            visible={100, 500},
        )
        u32._after_topmost = lambda hwnd: u32.z_chain.__setitem__(hwnd, 0)
        with patch.object(win32_overlay, 'IS_WINDOWS', True), \
             patch.object(win32_overlay, 'user32', u32), \
             patch.object(win32_overlay, 'wintypes', _FakeWintypes):
            self.assertTrue(win32_overlay.ensure_topmost(100))
            self.assertEqual(len(u32.set_window_pos_calls), 1)
            hwnd, ia, flags = u32.set_window_pos_calls[0]
            self.assertEqual(hwnd, 100)
            self.assertEqual(ia, win32_overlay.HWND_TOPMOST.value)
            self.assertTrue(flags & win32_overlay.SWP_NOACTIVATE)
            self.assertTrue(flags & win32_overlay.SWP_NOMOVE)
            self.assertTrue(flags & win32_overlay.SWP_NOSIZE)
            self.assertEqual(u32.set_foreground_calls, 0)

    def test_ensure_topmost_resets_notopmost_to_topmost_when_stripped_and_reraises_popups(self):
        u32 = _FakeUser32(
            exstyle=0,  # WS_EX_TOPMOST stripped
            z_chain={100: 500, 500: 0},
            visible={100, 300, 500},
        )
        with patch.object(win32_overlay, 'IS_WINDOWS', True), \
             patch.object(win32_overlay, 'user32', u32), \
             patch.object(win32_overlay, 'wintypes', _FakeWintypes):
            self.assertTrue(win32_overlay.ensure_topmost(100, allow_above_hwnds=(300,)))
            ias = [(h, ia) for h, ia, _ in u32.set_window_pos_calls]
            self.assertEqual(
                ias,
                [
                    (100, win32_overlay.HWND_TOPMOST.value),
                    (100, win32_overlay.HWND_NOTOPMOST.value),
                    (100, win32_overlay.HWND_TOPMOST.value),
                    (300, win32_overlay.HWND_TOPMOST.value),
                ],
            )
            self.assertEqual(u32.set_foreground_calls, 0)

    def test_win32_topmost_guard_installs_and_uninstalls_idempotently(self):
        u32 = _FakeUser32()
        hits = []
        fake_wineventproc = lambda fn: fn
        with patch.object(win32_overlay, 'IS_WINDOWS', True), \
             patch.object(win32_overlay, 'user32', u32), \
             patch.object(win32_overlay, 'WINEVENTPROC', fake_wineventproc):
            guard = win32_overlay.Win32TopmostGuard(lambda: hits.append(1))
            self.assertTrue(guard.install())
            self.assertEqual(len(u32.hooks), 3)
            events = {h[0] for h in u32.hooks}
            self.assertEqual(
                events,
                {
                    win32_overlay.EVENT_SYSTEM_FOREGROUND,
                    win32_overlay.EVENT_SYSTEM_SWITCHEND,
                    win32_overlay.EVENT_SYSTEM_MINIMIZEEND,
                },
            )
            for h in u32.hooks:
                self.assertEqual(
                    h[5],
                    win32_overlay.WINEVENT_OUTOFCONTEXT | win32_overlay.WINEVENT_SKIPOWNPROCESS,
                )
            # Trigger callback
            u32.hooks[0][2](0, 0, 0, 0, 0, 0, 0)
            self.assertEqual(hits, [1])
            guard.stop()
            guard.stop()  # idempotent
            self.assertEqual(len(u32.unhooked), 3)

    def test_enforce_topmost_on_windowposchanging_rewrites_demotion(self):
        wp = _FakeWINDOWPOS(hwnd=100, hwndInsertAfter=1, x=0, y=0, cx=100, cy=50, flags=0)
        msg = _FakeMSG(
            hwnd=100,
            message=win32_overlay.WM_WINDOWPOSCHANGING,
            wParam=0,
            lParam=ctypes.addressof(wp),
        )
        with patch.object(win32_overlay, 'IS_WINDOWS', True), \
             patch.object(win32_overlay, 'wintypes', _FakeWintypes), \
             patch.object(win32_overlay, 'WINDOWPOS', _FakeWINDOWPOS):
            self.assertTrue(win32_overlay.enforce_topmost_on_windowposchanging(ctypes.addressof(msg)))
            self.assertEqual(wp.hwndInsertAfter, win32_overlay.HWND_TOPMOST.value)

            # SWP_NOZORDER should be left untouched
            wp.hwndInsertAfter = 1
            wp.flags = win32_overlay.SWP_NOZORDER
            self.assertFalse(win32_overlay.enforce_topmost_on_windowposchanging(ctypes.addressof(msg)))
            self.assertEqual(wp.hwndInsertAfter, 1)

    def test_configure_overlay_never_sets_ws_ex_transparent(self):
        u32 = _FakeUser32(exstyle=0)
        widget = SimpleNamespace(winId=lambda: 100)
        with patch.object(win32_overlay, 'IS_WINDOWS', True), \
             patch.object(win32_overlay, 'user32', u32), \
             patch.object(win32_overlay, 'wintypes', _FakeWintypes):
            win32_overlay.configure_overlay(widget)
            self.assertEqual(u32.exstyle & 0x00000020, 0)  # WS_EX_TRANSPARENT never set
            self.assertTrue(u32.exstyle & win32_overlay.WS_EX_TOPMOST)
            self.assertTrue(u32.exstyle & win32_overlay.WS_EX_TOOLWINDOW)
            self.assertTrue(u32.exstyle & win32_overlay.WS_EX_NOACTIVATE)


if __name__ == '__main__':
    unittest.main()
