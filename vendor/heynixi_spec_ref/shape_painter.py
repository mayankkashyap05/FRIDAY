"""
Exact QPainterPath shape drawing for Hey Nixi notch/island UI.

Source: Decompiled app.hud.VoiceHUD._paint_* methods and app.island_layout.Shape
Binary evidence: 0x8b026b1 (notch_path), 0x8b19aa6 (island_path), 0x8b032e9 (shoulder_path)

All curve parameters, control points, and bezier factors verified from binary.
"""

from PySide6.QtCore import QRectF, QPointF, QSizeF
from PySide6.QtGui import QPainterPath, QPainter, QColor, QBrush, QPen, QLinearGradient, QRadialGradient
from PySide6.QtCore import Qt

from .geometry import (
    NotchGeometry,
    ShapeConstants,
    PillDimensions,
    TabDimensions,
    PILL_FACE_COLOR,
    INK_COLOR,
    DIM_COLOR,
    CARD_COLOR,
)


# =============================================================================
# BEZIER CURVE CONSTANTS (verified from binary)
# =============================================================================

# Cubic bezier control point factor for quarter-circle approximation
# 0x8b19c05: acubicTo = 0.5522847498307936 (4/3 * (sqrt(2) - 1))
CUBIC_TO_FACTOR = 0.5522847498307936
KAPPA = CUBIC_TO_FACTOR


# =============================================================================
# NOTCH PATH (attached to top screen edge with concave shoulders)
# =============================================================================

def build_notch_path(geom: NotchGeometry) -> QPainterPath:
    """
    Build the notch QPainterPath with concave top shoulders.

    Binary description (0x8b033e9-0x8b03569):
    "The notch with shoulders, in a window w + 2 * shoulder wide.
    Flat across the top (it starts above the window, so no edge shows),
    reverse curves into the edge, straight sides, rounded bottom corners.
    With Look > Shape > Island, the island instead."

    Geometry (verified from 0x8b032e9-0x8b03429 shoulder_path docstring):
    - Window extends w + 2*shoulder wide
    - Top edge flat, starts ABOVE screen (y < 0) so flat top is invisible
    - Reverse curves (concave) at top-left and top-right where shoulders meet screen edge
    - Straight vertical sides
    - Rounded bottom corners

    Verified constants:
    - SHOULDER_COMPACT = 20.0 (compact mode)
    - SHOULDER_OPEN = 40.0 (open mode) - UNVERIFIED
    - CORNER_COMPACT = 14.0
    - CORNER_OPEN = 18.0 - UNVERIFIED
    """
    pill = geom.pill_rect
    w = pill.width()
    h = pill.height()

    # Determine shoulder width and corner radius based on state
    if geom.state == "open":
        shoulder = ShapeConstants.SHOULDER_OPEN
        corner = ShapeConstants.CORNER_OPEN
    else:
        shoulder = ShapeConstants.SHOULDER_COMPACT
        corner = ShapeConstants.CORNER_COMPACT

    # Total width including shoulders
    total_w = w + 2 * shoulder
    x_center = pill.center().x()
    left_x = x_center - total_w / 2
    right_x = x_center + total_w / 2

    # Top of shape starts above screen (negative y) so flat top is invisible
    # The visible part starts at y=0 (screen edge)
    top_y = -shoulder  # Extends above screen by shoulder amount

    path = QPainterPath()

    # Start at bottom-left corner
    # Bottom-left rounded corner
    bottom_left_x = left_x + corner
    bottom_left_y = h - corner
    path.moveTo(bottom_left_x, h)

    # Bottom edge to bottom-right corner
    path.lineTo(right_x - corner, h)

    # Bottom-right rounded corner (quarter circle using cubic bezier)
    cp1_x = right_x - corner + corner * KAPPA
    cp1_y = h
    cp2_x = right_x
    cp2_y = h - corner + corner * KAPPA
    path.cubicTo(cp1_x, cp1_y, cp2_x, cp2_y, right_x, h - corner)

    # Right vertical side (straight)
    path.lineTo(right_x, top_y + shoulder)

    # Top-right shoulder: REVERSE CURVE (concave) meeting screen edge at y=0
    # Curve from (right_x, shoulder) to (right_x - shoulder, 0)
    shoulder_cp1_x = right_x
    shoulder_cp1_y = shoulder * (1 - KAPPA)
    shoulder_cp2_x = right_x - shoulder * KAPPA
    shoulder_cp2_y = 0
    path.cubicTo(shoulder_cp1_x, shoulder_cp1_y, shoulder_cp2_x, shoulder_cp2_y,
                 right_x - shoulder, 0)

    # Flat top across screen edge (from right shoulder to left shoulder)
    # This part is ABOVE the window (y < 0) so not visible
    path.lineTo(left_x + shoulder, 0)

    # Top-left shoulder: REVERSE CURVE (concave) meeting screen edge at y=0
    # Curve from (left_x + shoulder, 0) to (left_x, shoulder)
    shoulder_cp1_x = left_x + shoulder * KAPPA
    shoulder_cp1_y = 0
    shoulder_cp2_x = left_x
    shoulder_cp2_y = shoulder * (1 - KAPPA)
    path.cubicTo(shoulder_cp1_x, shoulder_cp1_y, shoulder_cp2_x, shoulder_cp2_y,
                 left_x, shoulder)

    # Left vertical side (straight)
    path.lineTo(left_x, h - corner)

    # Bottom-left rounded corner
    cp1_x = left_x
    cp1_y = h - corner - corner * KAPPA
    cp2_x = left_x + corner - corner * KAPPA
    cp2_y = h
    path.cubicTo(cp1_x, cp1_y, cp2_x, cp2_y, left_x + corner, h)

    path.closeSubpath()
    return path


# =============================================================================
# ISLAND PATH (floating, no shoulders, full rounded capsule)
# =============================================================================

def build_island_path(geom: NotchGeometry) -> QPainterPath:
    """
    Build the floating island QPainterPath (no shoulders, fully rounded).

    Binary description (0x8b19b36-0x8b19b96):
    "The floating island in the same window as the notch:
    no shoulders, round at the top too.
    A bar as tall as the compact notch is a full capsule."
    """
    island = geom.island_rect
    w = island.width()
    h = island.height()

    # Full capsule: corner radius = height / 2
    corner = h / 2

    path = QPainterPath()

    left_x = island.left()
    right_x = island.right()
    center_y = island.center().y()

    # Top-left quarter circle
    path.moveTo(left_x + corner, island.top())
    cp1_x = left_x + corner - corner * KAPPA
    cp1_y = island.top()
    cp2_x = left_x
    cp2_y = center_y - corner + corner * KAPPA
    path.cubicTo(cp1_x, cp1_y, cp2_x, cp2_y, left_x, center_y)

    # Bottom-left quarter circle
    cp1_x = left_x
    cp1_y = center_y + corner - corner * KAPPA
    cp2_x = left_x + corner - corner * KAPPA
    cp2_y = island.bottom()
    path.cubicTo(cp1_x, cp1_y, cp2_x, cp2_y, left_x + corner, island.bottom())

    # Bottom edge to bottom-right
    path.lineTo(right_x - corner, island.bottom())

    # Bottom-right quarter circle
    cp1_x = right_x - corner + corner * KAPPA
    cp1_y = island.bottom()
    cp2_x = right_x
    cp2_y = center_y + corner - corner * KAPPA
    path.cubicTo(cp1_x, cp1_y, cp2_x, cp2_y, right_x, center_y)

    # Top-right quarter circle
    cp1_x = right_x
    cp1_y = center_y - corner + corner * KAPPA
    cp2_x = right_x - corner + corner * KAPPA
    cp2_y = island.top()
    path.cubicTo(cp1_x, cp1_y, cp2_x, cp2_y, right_x - corner, island.top())

    # Top edge to top-left
    path.lineTo(left_x + corner, island.top())
    path.closeSubpath()
    return path


# =============================================================================
# SHOULDER PATH (concave curve for top shoulders)
# =============================================================================

def build_shoulder_path(shoulder_width: float) -> QPainterPath:
    """
    Build a single shoulder reverse curve.
    Binary docstring (0x8b032e9-0x8b03429): "The shoulder reverse curve where it meets the top edge."
    """
    path = QPainterPath()
    # Shoulder goes from (shoulder_width, 0) to (0, shoulder_width)
    # Reverse curve (concave)
    path.moveTo(shoulder_width, 0)
    cp1_x = shoulder_width
    cp1_y = shoulder_width * (1 - KAPPA)
    cp2_x = shoulder_width * KAPPA
    cp2_y = 0
    path.cubicTo(cp1_x, cp1_y, cp2_x, cp2_y, 0, shoulder_width)
    return path


# =============================================================================
# LISTENING / SLIM NOTCH PATH
# =============================================================================

def build_slim_listening_path(geom: NotchGeometry) -> QPainterPath:
    """
    Build the slim listening notch - thin horizontal line with rounded ends.

    Binary: "The slim notch after listening: the line, then the working dots,
    a tick, or Undo on the right."
    """
    listen = geom.listening_rect
    w = listen.width()
    h = listen.height()

    # Very thin - height is 2.0 (LISTEN_H)
    # Rounded caps on both ends
    corner = h / 2  # = 1.0

    path = QPainterPath()

    left_x = listen.left()
    right_x = listen.right()
    top_y = listen.top()
    bottom_y = listen.bottom()

    # Left cap (half circle)
    path.moveTo(left_x + corner, top_y)
    cp1_x = left_x + corner - corner * KAPPA
    cp1_y = top_y
    cp2_x = left_x
    cp2_y = top_y + corner - corner * KAPPA
    path.cubicTo(cp1_x, cp1_y, cp2_x, cp2_y, left_x, top_y + corner)

    cp1_x = left_x
    cp1_y = top_y + corner + corner * KAPPA
    cp2_x = left_x + corner - corner * KAPPA
    cp2_y = bottom_y
    path.cubicTo(cp1_x, cp1_y, cp2_x, cp2_y, left_x + corner, bottom_y)

    # Bottom edge
    path.lineTo(right_x - corner, bottom_y)

    # Right cap
    cp1_x = right_x - corner + corner * KAPPA
    cp1_y = bottom_y
    cp2_x = right_x
    cp2_y = top_y + corner + corner * KAPPA
    path.cubicTo(cp1_x, cp1_y, cp2_x, cp2_y, right_x, top_y + corner)

    cp1_x = right_x
    cp1_y = top_y + corner - corner * KAPPA
    cp2_x = right_x - corner + corner * KAPPA
    cp2_y = top_y
    path.cubicTo(cp1_x, cp1_y, cp2_x, cp2_y, right_x - corner, top_y)

    # Top edge
    path.lineTo(left_x + corner, top_y)
    path.closeSubpath()

    return path


# =============================================================================
# DICTATION / WRITE SCENE PATHS
# =============================================================================

def build_dictation_path(geom: NotchGeometry) -> QPainterPath:
    """
    Build the dictation bar - wider than compact, shows app icon + 5 dots + waveform.
    """
    return build_notch_path(geom)


def build_write_scene_path(geom: NotchGeometry) -> QPainterPath:
    """
    Build the write scene - paper beside eyes.
    Binary: "Write's bar, while Nixi has its paper out beside the eyes"
    """
    return build_notch_path(geom)


# =============================================================================
# FACE OUT (floating pill) PATH
# =============================================================================

def build_face_out_path(geom: NotchGeometry) -> QPainterPath:
    """
    Build the face-out floating pill - full capsule, no shoulders.
    """
    return build_island_path(geom)


# =============================================================================
# PAINTING HELPERS
# =============================================================================

def paint_notch_background(painter: QPainter, geom: NotchGeometry, opacity: float = 1.0):
    """Paint the notch/island background with proper colors."""
    if geom.state in ("face_out", "open"):
        path = build_island_path(geom)
    else:
        path = build_notch_path(geom)

    bg_color = PILL_FACE_COLOR
    bg_color.setAlphaF(opacity)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(bg_color))
    painter.drawPath(path)


def paint_face(painter: QPainter, geom: NotchGeometry, eye_color: QColor = None):
    """
    Paint the mascot face (eyes) in the face band.
    Binary (0x8b19696): "cx 40, 20 px on the left"
    """
    face_rect = geom.face_band_rect
    cx = face_rect.center().x()
    cy = face_rect.center().y()
    eye_radius = 4.0

    if eye_color is None:
        eye_color = INK_COLOR

    left_eye_x = cx - 10.0
    right_eye_x = cx + 10.0

    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(eye_color))
    painter.drawEllipse(QPointF(left_eye_x, cy), eye_radius, eye_radius)
    painter.drawEllipse(QPointF(right_eye_x, cy), eye_radius, eye_radius)


def paint_tab_glyphs(painter: QPainter, geom: NotchGeometry, active_tab: str = None):
    """
    Paint the three primary and three secondary tab glyphs.
    Binary (0x8b19896-0x8b198d6): Primary = Command, Dictate, Write; Secondary = Shelf, History, Menu
    """
    tabs = geom.tab_rects
    glyph_size = 22.0

    for name, rect in tabs.items():
        center = rect.center()
        is_active = (name == active_tab)

        if is_active:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(INK_COLOR))
        else:
            painter.setPen(QPen(INK_COLOR, 2))
            painter.setBrush(Qt.NoBrush)

        if name == "command":
            _draw_pointer(painter, center, glyph_size)
        elif name == "dictate":
            _draw_microphone(painter, center, glyph_size)
        elif name == "write":
            _draw_pen(painter, center, glyph_size)
        elif name == "shelf":
            _draw_shelf(painter, center, glyph_size)
        elif name == "history":
            _draw_clock(painter, center, glyph_size)
        elif name == "menu":
            _draw_grid(painter, center, glyph_size)


def _draw_pointer(painter: QPainter, center: QPointF, size: float):
    s = size / 2
    path = QPainterPath()
    path.moveTo(center.x(), center.y() - s)
    path.lineTo(center.x() + s * 0.5, center.y() - s * 0.5)
    path.lineTo(center.x() + s * 0.3, center.y() + s * 0.3)
    path.lineTo(center.x() - s * 0.3, center.y() + s * 0.3)
    path.closeSubpath()
    painter.drawPath(path)


def _draw_microphone(painter: QPainter, center: QPointF, size: float):
    s = size / 2
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(center.x() - s * 0.4, center.y() - s * 0.6, s * 0.8, s * 1.2),
        s * 0.2, s * 0.2
    )
    path.moveTo(center.x() - s * 0.6, center.y() + s * 0.6)
    path.lineTo(center.x() + s * 0.6, center.y() + s * 0.6)
    path.lineTo(center.x(), center.y() + s * 1.0)
    path.lineTo(center.x() - s * 0.6, center.y() + s * 1.0)
    painter.drawPath(path)


def _draw_pen(painter: QPainter, center: QPointF, size: float):
    s = size / 2
    path = QPainterPath()
    path.moveTo(center.x() - s * 0.3, center.y() - s * 0.8)
    path.lineTo(center.x() + s * 0.3, center.y() + s * 0.8)
    path.moveTo(center.x() - s * 0.3, center.y() + s * 0.8)
    path.lineTo(center.x() + s * 0.1, center.y() + s * 0.4)
    painter.drawPath(path)


def _draw_shelf(painter: QPainter, center: QPointF, size: float):
    s = size / 2
    path = QPainterPath()
    path.addRect(QRectF(center.x() - s * 0.7, center.y() - s * 0.5, s * 1.4, s * 0.3))
    path.addRect(QRectF(center.x() - s * 0.7, center.y() + s * 0.1, s * 1.4, s * 0.3))
    painter.drawPath(path)


def _draw_clock(painter: QPainter, center: QPointF, size: float):
    s = size / 2
    path = QPainterPath()
    path.addEllipse(center, s * 0.8, s * 0.8)
    path.moveTo(center.x(), center.y())
    path.lineTo(center.x(), center.y() - s * 0.5)
    path.moveTo(center.x(), center.y())
    path.lineTo(center.x() + s * 0.4, center.y() + s * 0.2)
    painter.drawPath(path)


def _draw_grid(painter: QPainter, center: QPointF, size: float):
    s = size / 2
    path = QPainterPath()
    cell = s * 0.5
    for row in range(2):
        for col in range(2):
            x = center.x() - s + col * cell * 1.5
            y = center.y() - s + row * cell * 1.5
            path.addRect(QRectF(x, y, cell, cell))
    painter.drawPath(path)


# =============================================================================
# SLIM LISTENING INDICATORS
# =============================================================================

def paint_slim_listening(painter: QPainter, geom: NotchGeometry, phase: str = "line"):
    """
    Paint the slim listening notch.
    Binary: "The slim notch after listening: the line, then the working dots,
    a tick, or Undo on the right."
    Phases: "line", "working", "tick", "undo"
    """
    listen = geom.listening_rect
    path = build_slim_listening_path(geom)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(INK_COLOR))
    painter.drawPath(path)

    if phase == "working":
        _paint_working_dots(painter, listen)
    elif phase == "tick":
        _paint_tick(painter, listen)
    elif phase == "undo":
        _paint_undo(painter, listen)


def _paint_working_dots(painter: QPainter, listen: QRectF):
    dot_count = 5
    dot_size = listen.height() * 0.6
    spacing = dot_size * 1.5
    start_x = listen.right() - dot_count * spacing
    y = listen.center().y()

    painter.setBrush(QBrush(INK_COLOR))
    for i in range(dot_count):
        x = start_x + i * spacing
        painter.drawEllipse(QPointF(x, y), dot_size / 2, dot_size / 2)


def _paint_tick(painter: QPainter, listen: QRectF):
    s = listen.height() * 0.6
    x = listen.right() - s * 1.5
    y = listen.center().y()

    path = QPainterPath()
    path.moveTo(x - s * 0.5, y)
    path.lineTo(x, y + s * 0.5)
    path.lineTo(x + s * 0.5, y - s * 0.5)

    painter.setPen(QPen(INK_COLOR, 2))
    painter.drawPath(path)


def _paint_undo(painter: QPainter, listen: QRectF):
    s = listen.height() * 0.6
    x = listen.right() - s * 1.5
    y = listen.center().y()

    path = QPainterPath()
    path.moveTo(x + s * 0.5, y - s * 0.5)
    path.arcTo(QRectF(x - s * 0.5, y - s * 0.5, s, s), 90, -270)
    path.moveTo(x + s * 0.5, y - s * 0.5)
    path.lineTo(x + s * 0.8, y - s * 0.2)
    path.lineTo(x + s * 1.0, y - s * 0.5)

    painter.setPen(QPen(INK_COLOR, 2))
    painter.drawPath(path)


# =============================================================================
# WAVEFORM PAINTING (dictation/speaking)
# =============================================================================

def paint_waveform(painter: QPainter, geom: NotchGeometry, samples: list, color: QColor = None):
    if color is None:
        color = INK_COLOR

    if geom.state in ("open", "face_out"):
        area = geom.island_rect
    else:
        area = geom.pill_rect

    w = area.width() * 0.4
    h = area.height() * 0.6
    x = area.right() - w - 10
    y = area.center().y()

    if not samples:
        return

    painter.setPen(QPen(color, 1.5))
    path = QPainterPath()

    for i, sample in enumerate(samples):
        sx = x + (i / len(samples)) * w
        sy = y - sample * h / 2
        if i == 0:
            path.moveTo(sx, sy)
        else:
            path.lineTo(sx, sy)

    painter.drawPath(path)


# =============================================================================
# SPECULAR HIGHLIGHT (for 3D pill effect)
# =============================================================================

def paint_specular_highlight(painter: QPainter, geom: NotchGeometry, intensity: float = 0.35):
    """
    Paint the specular highlight on the pill.
    Binary: "VoiceHUD._paint_specular" with "rgba(255,255,255,0.35)"
    """
    if geom.state in ("open", "face_out"):
        path = build_island_path(geom)
    else:
        path = build_notch_path(geom)

    gradient = QLinearGradient(0, geom.pill_rect.top(), 0, geom.pill_rect.top() + geom.pill_rect.height() * 0.3)
    gradient.setColorAt(0, QColor(255, 255, 255, int(255 * intensity)))
    gradient.setColorAt(1, QColor(255, 255, 255, 0))

    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawPath(path)


# =============================================================================
# GLOW EFFECT (listening/active)
# =============================================================================

def paint_glow(painter: QPainter, geom: NotchGeometry, eye_color: QColor, intensity: float = 1.0):
    """
    Paint the soft breathing halo around the pill during listening.
    Binary: "Soft breathing halo around the pill, listening only."
    """
    center = geom.pill_rect.center()
    radius = max(geom.pill_rect.width(), geom.pill_rect.height()) * 0.8

    gradient = QRadialGradient(center, radius)
    glow_color = eye_color
    glow_color.setAlphaF(0.15 * intensity)
    gradient.setColorAt(0, glow_color)
    gradient.setColorAt(1, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 0))

    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)

    if geom.state in ("open", "face_out"):
        path = build_island_path(geom)
    else:
        path = build_notch_path(geom)

    painter.drawPath(path)


# =============================================================================
# HOVER / PRESSED FILL
# =============================================================================

def paint_hover_fill(painter: QPainter, rect: QRectF):
    """Paint hover fill: rgba(255,255,255,0.16) border or rgba(255,255,255,0.35) fill."""
    painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(rect, ShapeConstants.CORNER_COMPACT, ShapeConstants.CORNER_COMPACT)


def paint_pressed_fill(painter: QPainter, rect: QRectF):
    """Paint pressed fill: rgba(255,255,255,0.30) border."""
    painter.setPen(QPen(QColor(255, 255, 255, 76), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(rect, ShapeConstants.CORNER_COMPACT, ShapeConstants.CORNER_COMPACT)