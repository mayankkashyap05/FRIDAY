"""Native macOS Cocoa/AppKit overlay bridge.

Drop-in macOS equivalents matching the public API of
``ui.island.win32_overlay``. Every function degrades cleanly to a safe no-op
when running off macOS or when PyObjC (objc/AppKit/Quartz) is unavailable.

The module also mirrors Win32 constant/helper names so that runtime injection
into ``ui.island.win32_overlay`` leaves no missing attributes.
"""
from __future__ import annotations

import ctypes
import sys

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

# --- Mirror Win32 constants for drop-in compatibility ---
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

# Win32 ctypes surface mirrored for attribute compatibility.
if IS_WINDOWS:
    try:
        from ctypes import wintypes  # type: ignore
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
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

        class WINDOWPOS(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("hwndInsertAfter", wintypes.HWND),
                ("x", ctypes.c_int),
                ("y", ctypes.c_int),
                ("cx", ctypes.c_int),
                ("cy", ctypes.c_int),
                ("flags", wintypes.UINT),
            ]
    except Exception:
        wintypes = None  # type: ignore
        user32 = None  # type: ignore
        WINEVENTPROC = None  # type: ignore
        WINDOWPOS = None  # type: ignore
else:
    wintypes = None  # type: ignore
    user32 = None  # type: ignore
    WINEVENTPROC = None  # type: ignore
    WINDOWPOS = None  # type: ignore


# --- Cocoa helpers ---

def _load_cocoa():
    """Return (objc, AppKit, Quartz, Foundation) or (None, None, None, None)."""
    if not IS_MACOS:
        return None, None, None, None
    try:
        import objc  # type: ignore
        import AppKit  # type: ignore
        import Quartz  # type: ignore
        import Foundation  # type: ignore
        return objc, AppKit, Quartz, Foundation
    except Exception:
        return None, None, None, None


def is_macos_cocoa_available() -> bool:
    """Check macOS platform and lazy availability of objc + AppKit."""
    if not IS_MACOS:
        return False
    objc, AppKit, _Quartz, _Foundation = _load_cocoa()
    return objc is not None and AppKit is not None


def set_accessory_activation_policy() -> bool:
    """Set NSApplicationActivationPolicyAccessory (idempotent).

    Allows FRIDAY's overlay panel to float above other apps' full-screen Spaces
    without kicking the user out of full screen.
    """
    objc, AppKit, _Q, _F = _load_cocoa()
    if AppKit is None:
        return False
    try:
        app = AppKit.NSApplication.sharedApplication()
        policy = getattr(AppKit, "NSApplicationActivationPolicyAccessory", 1)
        app.setActivationPolicy_(policy)
        return True
    except Exception:
        return False


def ns_window_from_wid(wid: int):
    """Safely unwrap Qt6's QNSView* to the underlying NSWindow/QNSPanel.

    Returns None safely if invalid or PyObjC is unavailable.
    """
    objc, _AppKit, _Q, _F = _load_cocoa()
    if objc is None:
        return None
    try:
        wid_int = int(wid)
        if not wid_int:
            return None
        # Qt6 on macOS returns a pointer to the NSView as the winId.
        view = objc.objc_object(c_void_p=ctypes.c_void_p(wid_int))
        if view is None:
            return None
        window = view.window()
        return window
    except Exception:
        return None


def _ns_collection_behavior(AppKit):
    behavior = 0
    for name in (
        "NSWindowCollectionBehaviorCanJoinAllSpaces",
        "NSWindowCollectionBehaviorFullScreenAuxiliary",
        "NSWindowCollectionBehaviorStationary",
        "NSWindowCollectionBehaviorIgnoresCycle",
    ):
        behavior |= int(getattr(AppKit, name, 0))
    # Optional on newer macOS/PyObjC versions.
    behavior |= int(getattr(AppKit, "NSWindowCollectionBehaviorCanJoinAllApplications", 0))
    return behavior


def configure_cocoa_overlay(
    wid: int,
    *,
    non_activating: bool = True,
    above_fullscreen: bool = True,
    translucent: bool = True,
) -> bool:
    """Apply Apple-verified full-screen overlay settings to an NSWindow."""
    objc, AppKit, _Q, _F = _load_cocoa()
    if AppKit is None:
        return False
    try:
        ns_window = ns_window_from_wid(wid)
        if ns_window is None:
            return False

        # 1. Non-activating panel style when supported (QNSPanel / NSPanel).
        if non_activating:
            try:
                style_mask = ns_window.styleMask()
                non_activating_mask = int(getattr(AppKit, "NSWindowStyleMaskNonactivatingPanel", 1 << 7))
                ns_window.setStyleMask_(int(style_mask) | non_activating_mask)
            except Exception:
                # Not a panel or styleMask unavailable; continue with other flags.
                pass

        # 2. Window level.
        try:
            if above_fullscreen:
                level = int(getattr(AppKit, "NSScreenSaverWindowLevel", 1000))
            else:
                level = int(getattr(AppKit, "NSStatusWindowLevel", 25))
            ns_window.setLevel_(level)
        except Exception:
            pass

        # 3. Collection behavior.
        try:
            ns_window.setCollectionBehavior_(_ns_collection_behavior(AppKit))
        except Exception:
            pass

        # 4. Keep visible when app deactivates (critical for NSPanel/Qt.Tool).
        try:
            ns_window.setHidesOnDeactivate_(False)
        except Exception:
            pass

        # 5. Translucent rendering.
        if translucent:
            try:
                ns_window.setOpaque_(False)
            except Exception:
                pass
            try:
                ns_window.setBackgroundColor_(AppKit.NSColor.clearColor())
            except Exception:
                pass
            try:
                ns_window.setHasShadow_(False)
            except Exception:
                pass

        # 6. Show without activating.
        try:
            ns_window.orderFrontRegardless()
        except Exception:
            pass
        return True
    except Exception:
        return False


# --- Win32-overlay compatible API (mirrored names) ---

def _get_window_long_ptr(hwnd, index):
    if IS_WINDOWS and user32 is not None:
        if hasattr(user32, "GetWindowLongPtrW"):
            return user32.GetWindowLongPtrW(hwnd, index)
        return user32.GetWindowLongW(hwnd, index)
    return 0


def _set_window_long_ptr(hwnd, index, value):
    if IS_WINDOWS and user32 is not None:
        if hasattr(user32, "SetWindowLongPtrW"):
            return user32.SetWindowLongPtrW(hwnd, index, value)
        return user32.SetWindowLongW(hwnd, index, value)
    return 0


def _has_visible_external_above(hwnd, allow_above):
    # Win32-only Z-order walk; on macOS return False (no-op equivalent).
    return False


def configure_overlay(widget):
    """Drop-in for win32_overlay.configure_overlay(widget)."""
    if not IS_MACOS:
        return
    try:
        wid = int(widget.winId())
    except Exception:
        return
    configure_cocoa_overlay(wid)


def set_accepts_focus(widget, accepts):
    """Drop-in for win32_overlay.set_accepts_focus(widget, accepts)."""
    if not IS_MACOS:
        return
    objc, AppKit, _Q, _F = _load_cocoa()
    if AppKit is None:
        return
    try:
        ns_window = ns_window_from_wid(int(widget.winId()))
        if ns_window is None:
            return
        # NSPanel supports becomesKeyOnlyIfNeeded / ignoresMouseEvents is too
        # aggressive; toggling style mask is the safest equivalent.
        non_activating_mask = int(getattr(AppKit, "NSWindowStyleMaskNonactivatingPanel", 1 << 7))
        style = int(ns_window.styleMask())
        if accepts:
            style &= ~non_activating_mask
        else:
            style |= non_activating_mask
        ns_window.setStyleMask_(style)
    except Exception:
        return


def ensure_topmost(hwnd_or_wid, *, force=False, allow_above_hwnds=()):
    """Ensure topmost without stealing focus.

    Drop-in for win32_overlay.ensure_topmost. Returns True if a re-assertion
    was performed, False otherwise.
    """
    if not IS_MACOS or not hwnd_or_wid:
        return False
    objc, AppKit, _Q, _F = _load_cocoa()
    if AppKit is None:
        return False
    try:
        wid = int(hwnd_or_wid)
    except Exception:
        return False

    ns_window = ns_window_from_wid(wid)
    if ns_window is None:
        # Maybe hwnd_or_wid is already an NSWindow-like objc object? Try direct.
        try:
            ns_window = hwnd_or_wid if hasattr(hwnd_or_wid, "orderFrontRegardless") else None
        except Exception:
            ns_window = None
    if ns_window is None:
        return False

    did = False
    try:
        if force or True:
            # Always apply overlay config; idempotent and cheap.
            configure_cocoa_overlay(wid)
            ns_window.orderFrontRegardless()
            did = True
    except Exception:
        pass

    # Re-order companion popups above the dock.
    for popup in allow_above_hwnds:
        if not popup:
            continue
        try:
            p_int = int(popup)
        except Exception:
            continue
        if p_int == wid:
            continue
        popup_window = ns_window_from_wid(p_int)
        if popup_window is None:
            continue
        try:
            is_visible = popup_window.isVisible()
        except Exception:
            is_visible = True
        if not is_visible:
            continue
        try:
            popup_window.orderFrontRegardless()
            did = True
        except Exception:
            pass
    return bool(did)


def enforce_topmost_on_windowposchanging(*args, **kwargs):
    """Clean no-op on macOS (Win32 WM_WINDOWPOSCHANGING is not applicable)."""
    return False


class CocoaTopmostGuard:
    """Drop-in replacement for Win32TopmostGuard.

    Observes NSWorkspace space/app activation changes to re-assert topmost.
    Degrades cleanly to a no-op when PyObjC is absent.
    """

    _EVENTS = (
        EVENT_SYSTEM_FOREGROUND,
        EVENT_SYSTEM_SWITCHEND,
        EVENT_SYSTEM_MINIMIZEEND,
    )

    def __init__(self, on_foreground_change=None):
        self._on_foreground_change = on_foreground_change
        self._hooks = []
        self._callback = None
        self._installed = False
        self._observers = []
        self._workspace = None

    def install(self):
        if self._installed:
            return False
        objc, AppKit, _Q, Foundation = _load_cocoa()
        if AppKit is None or Foundation is None:
            return False
        try:
            workspace = AppKit.NSWorkspace.sharedWorkspace()
            center = workspace.notificationCenter()
        except Exception:
            return False

        def _fire(_notification=None):
            try:
                if self._on_foreground_change is not None:
                    self._on_foreground_change()
            except Exception:
                pass

        self._workspace = workspace
        self._callback = _fire
        names = []
        for name in (
            "NSWorkspaceActiveSpaceDidChangeNotification",
            "NSWorkspaceDidActivateApplicationNotification",
        ):
            val = getattr(AppKit, name, None)
            if val:
                names.append(val)
        if not names:
            # Still mark installed as no-op guard to match idempotent semantics.
            self._installed = True
            return True
        try:
            # Use a simple Python-callable observer via Foundation/objc block if
            # available; otherwise fall back to selector-based observer.
            for notif_name in names:
                try:
                    obs = center.addObserverForName_object_queue_usingBlock_(
                        notif_name,
                        None,
                        None,
                        lambda _n, cb=_fire: cb(_n),
                    )
                except Exception:
                    # Fallback: no block API. Skip observer; we still have timer in HUD.
                    obs = None
                if obs is not None:
                    self._observers.append(obs)
        except Exception:
            pass
        self._installed = True
        return True

    def uninstall(self):
        if not self._installed and not self._observers:
            self._callback = None
            self._workspace = None
            return
        try:
            if self._workspace is not None and self._observers:
                center = self._workspace.notificationCenter()
                for obs in self._observers:
                    try:
                        center.removeObserver_(obs)
                    except Exception:
                        pass
        except Exception:
            pass
        self._observers.clear()
        self._hooks.clear()
        self._installed = False
        self._callback = None
        self._workspace = None

    stop = uninstall


# Alias so runtime injection can also replace the Win32 name.
Win32TopmostGuard = CocoaTopmostGuard


def hit_test(widget, message):
    """macOS has no WM_NCHITTEST; return None to let Qt handle it."""
    return None


def foreground_is_fullscreen(widget):
    """Best-effort fullscreen detection on macOS.

    Returns True if a different app appears to own a full-screen space. This is
    a conservative heuristic; degrades to False when unavailable.
    """
    objc, AppKit, _Q, _F = _load_cocoa()
    if AppKit is None:
        return False
    try:
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        front = workspace.frontmostApplication()
        if front is None:
            return False
        try:
            my_pid = int(AppKit.NSProcessInfo.processInfo().processIdentifier())
            if int(front.processIdentifier()) == my_pid:
                return False
        except Exception:
            pass
        # Heuristic: active app has a full-screen window occupying main screen.
        try:
            import Quartz  # type: ignore
            opts = (
                int(getattr(AppKit, "NSWindowListOptionOnScreenOnly", 1))
                | int(getattr(AppKit, "NSWindowListExcludeDesktopElements", 16))
            )
            window_info = Quartz.CGWindowListCopyWindowInfo(opts, 0)
        except Exception:
            return False
        try:
            front_pid = int(front.processIdentifier())
        except Exception:
            return False
        try:
            main_screen = AppKit.NSScreen.mainScreen()
            if main_screen is None:
                return False
            sf = main_screen.frame()
            sw, sh = float(sf.size.width), float(sf.size.height)
        except Exception:
            return False
        for info in window_info or []:
            try:
                pid = int(info.get("kCGWindowOwnerPID", -1))
                if pid != front_pid:
                    continue
                bounds = info.get("kCGWindowBounds", {})
                bw = float(bounds.get("Width", 0))
                bh = float(bounds.get("Height", 0))
                layer = int(info.get("kCGWindowLayer", 0))
                if layer != 0:
                    continue
                if bw >= sw - 2 and bh >= sh - 2:
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def foreground_window():
    """Return (window handle/pid, title) on macOS, or (0, '') when unavailable."""
    objc, AppKit, _Q, _F = _load_cocoa()
    if AppKit is None:
        return 0, ""
    try:
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        front = workspace.frontmostApplication()
        if front is None:
            return 0, ""
        pid = int(front.processIdentifier())
        name = str(front.localizedName() or "")
        return pid, name
    except Exception:
        return 0, ""


def clipboard_sequence():
    """Return NSPasteboard.generalPasteboard().changeCount() on macOS."""
    objc, AppKit, _Q, _F = _load_cocoa()
    if AppKit is None:
        raise RuntimeError("Clipboard sequence is only available on macOS with PyObjC")
    try:
        pb = AppKit.NSPasteboard.generalPasteboard()
        return int(pb.changeCount())
    except Exception as exc:
        raise RuntimeError(f"Clipboard sequence unavailable: {exc}") from exc


def send_paste():
    """Post Cmd+V via Quartz CGEvent on macOS."""
    objc, AppKit, Quartz, _F = _load_cocoa()
    if Quartz is None:
        raise RuntimeError("External paste is only available on macOS with PyObjC")
    try:
        key_code = 9  # kVK_ANSI_V
        cmd_flag = int(getattr(Quartz, "kCGEventFlagMaskCommand", 1 << 20))
        src = Quartz.CGEventSourceCreate(getattr(Quartz, "kCGEventSourceStateHIDSystemState", 1))
        key_down = Quartz.CGEventCreateKeyboardEvent(src, key_code, True)
        key_up = Quartz.CGEventCreateKeyboardEvent(src, key_code, False)
        Quartz.CGEventSetFlags(key_down, cmd_flag)
        Quartz.CGEventSetFlags(key_up, cmd_flag)
        tap = getattr(Quartz, "kCGHIDEventTap", 0)
        Quartz.CGEventPost(tap, key_down)
        Quartz.CGEventPost(tap, key_up)
        return True
    except Exception as exc:
        raise OSError(f"macOS did not accept the paste keystroke: {exc}") from exc
