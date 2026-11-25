"""Dynamic MacBook notch and menu-bar anchor inspection.

Degrades cleanly to zero-inset defaults off macOS or when PyObjC is unavailable.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass
class MacDisplayInsets:
    has_notch: bool = False
    top_inset: float = 0.0
    notch_width: float = 0.0
    menu_bar_height: float = 0.0


def _load_appkit():
    if sys.platform != "darwin":
        return None
    try:
        import AppKit  # type: ignore

        return AppKit
    except Exception:
        return None


def _screen_for_point(AppKit, screen_rect=None):
    try:
        screens = AppKit.NSScreen.screens() or []
    except Exception:
        screens = []
    if not screens:
        return None

    # Prefer screen matching provided coordinates (Qt logical coords approximation).
    if screen_rect is not None:
        try:
            # screen_rect may be QRect/QRectF-like or tuple (x,y,w,h)
            if hasattr(screen_rect, "x") and hasattr(screen_rect, "center"):
                cx = float(screen_rect.center().x())
            elif isinstance(screen_rect, (tuple, list)) and len(screen_rect) >= 4:
                cx = float(screen_rect[0]) + float(screen_rect[2]) / 2.0
            else:
                cx = None
            if cx is not None:
                # NSScreen coordinates have origin at bottom-left of primary screen;
                # Qt uses top-left. We do a best-effort match by checking width/height.
                for s in screens:
                    try:
                        fr = s.frame()
                        if abs(float(fr.size.width) - float(getattr(screen_rect, "width", lambda: screen_rect[2])())) < 2:
                            return s
                    except Exception:
                        continue
        except Exception:
            pass
    try:
        return AppKit.NSScreen.mainScreen() or screens[0]
    except Exception:
        return screens[0]


def get_display_insets(screen_rect=None) -> MacDisplayInsets:
    """Inspect NSScreen safe area / auxiliary areas for notch geometry."""
    AppKit = _load_appkit()
    if AppKit is None:
        return MacDisplayInsets()
    try:
        screen = _screen_for_point(AppKit, screen_rect)
        if screen is None:
            return MacDisplayInsets()

        top_inset = 0.0
        try:
            safe = screen.safeAreaInsets()
            # NSDirectionalEdgeInsets / NSEdgeInsets on macOS 12+: has .top
            top_inset = float(getattr(safe, "top", 0.0) or 0.0)
        except Exception:
            top_inset = 0.0

        notch_width = 0.0
        try:
            left = screen.auxiliaryTopLeftArea()
            right = screen.auxiliaryTopRightArea()
            frame = screen.frame()
            full_w = float(frame.size.width)
            left_w = float(getattr(left.size, "width", 0.0) or 0.0)
            right_w = float(getattr(right.size, "width", 0.0) or 0.0)
            # Notch width is the gap between the two auxiliary areas.
            gap = full_w - left_w - right_w
            if gap > 0:
                notch_width = gap
        except Exception:
            notch_width = 0.0

        has_notch = bool(top_inset > 0 and notch_width > 0)

        menu_bar_height = 0.0
        try:
            # visibleFrame excludes menu bar and dock; frame is full.
            frame = screen.frame()
            visible = screen.visibleFrame()
            # Menu bar is at top in Cocoa coords; top gap = frame.maxY - visible.maxY
            top_gap = float(frame.origin.y + frame.size.height) - float(visible.origin.y + visible.size.height)
            if top_gap >= 0:
                menu_bar_height = top_gap
        except Exception:
            menu_bar_height = 0.0

        if has_notch and menu_bar_height <= 0:
            menu_bar_height = top_inset

        return MacDisplayInsets(
            has_notch=has_notch,
            top_inset=top_inset,
            notch_width=notch_width,
            menu_bar_height=menu_bar_height,
        )
    except Exception:
        return MacDisplayInsets()


def adjust_anchor_y(default_y: int, screen_rect=None) -> int:
    """Adjust dock anchor Y for notched vs non-notched displays.

    - Notched MacBooks: keep the dock flush at the top edge (y = 0) so it
      visually hugs and expands from the physical notch.
    - Non-notched / external displays: keep clean alignment at top edge.
    - Fallback: return default_y unchanged.
    """
    insets = get_display_insets(screen_rect)
    try:
        y = int(default_y)
    except Exception:
        return 0
    if not sys.platform == "darwin":
        return y
    # When AppKit is unavailable, get_display_insets returns zeros; preserve default.
    AppKit = _load_appkit()
    if AppKit is None:
        return y
    if insets.has_notch:
        return 0
    # Non-notched: ensure we don't clip off-screen; top edge is fine.
    return max(0, y)
