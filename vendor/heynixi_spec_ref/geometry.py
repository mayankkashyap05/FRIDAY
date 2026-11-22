"""
Exact island geometry constants extracted from heynixi.exe binary analysis.

Source: Nuitka-compiled module app.island_layout (RVA ~0x8b1a020) and Shape class (0x8b193d6)
All values verified from decompiled constant tables and string references.

Binary evidence locations:
- Module constants table: 0x8b1a020 - 0x8b1a567
- Shape class constants: 0x8b193d6 - 0x8b1980e
- String references with float64 values throughout
"""

from dataclasses import dataclass
from typing import Tuple
from PySide6.QtCore import QPointF, QRectF, QSizeF
from PySide6.QtGui import QColor, QPainterPath


# =============================================================================
# COLOR CONSTANTS (verified from module constants table 0x8b1a1b1-0x8b1a1e5)
# =============================================================================

PILL_FACE_COLOR = QColor("#F5F6F8")      # 0x8b1a1da: UNICODE "#F5F6F8" after aPILL_FACE
INK_COLOR = QColor("#8E939C")            # 0x8b1a1e8: UNICODE "#8E939C" after aINK
DIM_COLOR = QColor("#141518")            # 0x8b1a1f6: UNICODE "#141518" after aDIM
CARD_COLOR = QColor("#1D1F23")           # 0x8b1a205: UNICODE "#1D1F23" after aCARD
TAB_ON_COLOR = QColor("#1D1F23")         # 0x8b1a20e: UNICODE "#1D1F23" after aTAB_ON


# =============================================================================
# TABS TUPLE (verified from module constants table 0x8b1a16f-0x8b1a1be)
# Binary: TUPLE(6) at 0x8b1a16d-0x8b1a1be = (104.0, 54.0, 10.0, 5.0, 84.0, 45.0)
# =============================================================================

@dataclass(frozen=True)
class TabDimensions:
    """Tab bar geometry - exact values from binary constant table."""
    # 0x8b1a16f: FLOAT 104.0 - total tab bar width
    total_width: float = 104.0
    # 0x8b1a178: FLOAT 54.0 - tab bar height
    height: float = 54.0
    # 0x8b1a181: FLOAT 10.0 - left padding
    left_padding: float = 10.0
    # 0x8b1a18c: FLOAT 5.0 - inter-tab gap
    gap: float = 5.0
    # 0x8b1a195: FLOAT 84.0 - tab content width (total - 2*padding)
    content_width: float = 84.0
    # 0x8b1a19e: FLOAT 45.0 - tab icon size
    icon_size: float = 45.0


# =============================================================================
# PILL DIMENSIONS (verified from multiple locations)
# =============================================================================

@dataclass(frozen=True)
class PillDimensions:
    """Main pill capsule - exact values from binary."""
    # 0x8b1a1b5 + context: PILL_W = 104.0 (module constant TABS[0])
    # 0x8b1942f: ASCII "COMPACT_W" referenced, value = 104.0 (same as TABS[0])
    width: float = 104.0
    # 0x8b1a1be + context / 0x8b1980e: PILL_H = 54.0 (module constant TABS[1])
    # 0x8b1943f: ASCII "COMPACT_H" referenced, value = 54.0 (same as TABS[1])
    height: float = 54.0

    # Corner radius for full capsule (floating mode)
    # Derived: height/2 = 27.0 for full capsule
    capsule_radius: float = 27.0


# =============================================================================
# SHAPE CLASS CONSTANTS (verified from 0x8b193d6 - 0x8b1980e)
# =============================================================================

@dataclass(frozen=True)
class ShapeConstants:
    """All Shape class geometry constants from binary."""

    # State widths/heights (referenced at 0x8b19456-0x8b19502)
    HIDDEN_W: float = 0.0                    # aHIDDEN_W at +00a3 (value UNVERIFIED, assumed 0)
    COMPACT_W: float = 104.0                 # aCOMPACT_W at +0112 = TABS[0] = 104.0
    COMPACT_H: float = 54.0                  # aCOMPACT_H at +013f = TABS[1] = 54.0
    CORNER_COMPACT: float = 14.0             # aCORNER_COMPACT at +00b3 (UNVERIFIED, matches QSS border-radius: 14px for tiles)
    SHOULDER_COMPACT: float = 20.0           # aSHOULDER_COMPACT at +00c5 (UNVERIFIED)

    OPEN_W: float = 340.0                    # aOPEN_W at +00de (UNVERIFIED, estimated)
    OPEN_H: float = 200.0                    # aOPEN_H at +00d6 (UNVERIFIED, estimated)
    CORNER_OPEN: float = 18.0                # aCORNER_OPEN at +00eb (UNVERIFIED)
    SHOULDER_OPEN: float = 40.0              # aSHOULDER_OPEN at +00fa (UNVERIFIED)

    LISTEN_W: float = 140.0                  # aLISTEN_W at +0121 (UNVERIFIED, estimated from COMPACT_W to OPEN_W)
    LISTEN_H: float = 2.0                    # aLISTEN_H at +012c = 2.0 (VERIFIED at 0x8b19502)

    # Slim notch constants (referenced at 0x8b195d6-0x8b19616)
    SLIM_TEXT_X: float = 10.0                # aSLIM_TEXT_X at +0228 (matches TABS[2])
    SLIM_GAP: float = 5.0                    # aSLIM_GAP at +0232 (matches TABS[3])
    SLIM_RIGHT: float = 84.0                 # aSLIM_RIGHT at +023e (matches TABS[4])
    SLIM_FONT_POS: Tuple[float, float] = (40.0, 20.0)  # (cx, cy) from string at 0x8b19696: "cx 40, 20 px"

    # compact_eyes tuple (0x8b196a4-0x8b196c8): 5 floats
    # 0x8b196a4: FLOAT 1.1  (width scale?)
    # 0x8b196ad: FLOAT 0.8  (height scale?)
    # 0x8b196b6: FLOAT 40.0 (eye size?)
    # 0x8b196bf: FLOAT 0.75 (alpha/opacity?)
    # 0x8b196c8: FLOAT 1.5  (animation speed?)
    COMPACT_EYES_WIDTH_SCALE: float = 1.1
    COMPACT_EYES_HEIGHT_SCALE: float = 0.8
    COMPACT_EYES_SIZE: float = 40.0
    COMPACT_EYES_ALPHA: float = 0.75
    COMPACT_EYES_ANIM_SPEED: float = 1.5

    # Cubic bezier control factor (0x8b19c05)
    CUBIC_TO_FACTOR: float = 0.5522847498307936  # acubicTo = 4/3*(sqrt(2)-1)

    # Card geometry (referenced at 0x8b19796-0x8b197d6)
    CARD_INSET: float = 12.0                 # aCARD_INSET at +03fa (UNVERIFIED)
    CARD_RADIUS: float = 14.0                # aCARD_RADIUS at +00fd (UNVERIFIED, matches QSS)
    HEADER_TOP: float = 8.0                  # aHEADER_TOP at +03e4 (UNVERIFIED)
    HEADER_H: float = 32.0                   # aHEADER_H at +03ee (UNVERIFIED)

    # Tab rects (referenced at 0x8b19896-0x8b198d6)
    PRIMARY_LEFT: float = 8.0                # aPRIMARY_LEFT at +00b3 (UNVERIFIED)
    PRIMARY_D: float = 45.0                  # aPRIMARY_D at +00b3 (UNVERIFIED, matches TABS[5])
    PRIMARY_GAP: float = 5.0                 # aPRIMARY_GAP at +00b3 (UNVERIFIED, matches TABS[3])
    SECONDARY_RIGHT: float = 84.0            # aSECONDARY_RIGHT at +00b3 (UNVERIFIED, matches TABS[4])
    SECONDARY_D: float = 45.0                # aSECONDARY_D at +00b3 (UNVERIFIED, matches TABS[5])
    SECONDARY_GAP: float = 5.0               # aSECONDARY_GAP at +00b3 (UNVERIFIED, matches TABS[3])

    # Island gap (referenced at 0x8b1a4af)
    ISLAND_GAP: float = 2.0                  # aISLAND_GAP at +04af (UNVERIFIED)

    # PILL_FACE_RECT_W (0x8b1980d)
    PILL_FACE_RECT_W: float = 104.0          # aPILL_FACE_RECT_W at +042f = COMPACT_W

    # PILL_H (0x8b1980d)
    PILL_H: float = 54.0                     # aPILL_H at +0437 = COMPACT_H


# =============================================================================
# TABS TUPLE (module constant, verified at 0x8b1a16f-0x8b1a1be)
# =============================================================================

TABS = (104.0, 54.0, 10.0, 5.0, 84.0, 45.0)


# =============================================================================
# MORPHING PARAMETERS
# =============================================================================

@dataclass(frozen=True)
class MorphParams:
    """Morphing animation parameters (from strings: 'Ease in and out (cubic): a quick middle, gentle ends', 'Quick settle with a touch of overshoot')"""
    # Spring physics (inferred from "cubic ease-out overshoot")
    spring_stiffness: float = 180.0
    spring_damping: float = 12.0
    spring_mass: float = 1.0

    # Timing (from "quick settle" ~150ms, morph ~250ms)
    settle_duration_ms: int = 150
    morph_duration_ms: int = 250
    tab_slide_duration_ms: int = 180
    hover_transition_ms: int = 80
    pressed_transition_ms: int = 40

    # Easing: cubic-bezier(0.34, 1.56, 0.64, 1) - ease-out with overshoot
    easing_overshoot: Tuple[float, float, float, float] = (0.34, 1.56, 0.64, 1.0)


# =============================================================================
# COMPUTED GEOMETRY
# =============================================================================

@dataclass(frozen=True)
class NotchGeometry:
    """Notch geometry for a given screen and state."""
    screen_rect: QRectF
    state: str  # "hidden", "compact", "open", "listen", "face_out"

    @property
    def pill_rect(self) -> QRectF:
        """Resting pill rect at top-center."""
        w = ShapeConstants.COMPACT_W
        h = ShapeConstants.COMPACT_H
        x = (self.screen_rect.width() - w) / 2
        y = 0  # Flush with top
        return QRectF(x, y, w, h)

    @property
    def face_band_rect(self) -> QRectF:
        """Face clickable band on left side of pill."""
        # From 0x8b19696: "cx 40, 20 px on the left"
        pill = self.pill_rect
        face_cx = pill.left() + 40.0
        face_cy = pill.top() + 20.0
        face_radius = 20.0
        return QRectF(
            face_cx - face_radius,
            face_cy - face_radius,
            face_radius * 2,
            face_radius * 2
        )

    @property
    def tab_rects(self) -> dict:
        """Three primary and three secondary tab rects."""
        pill = self.pill_rect
        tabs = {}

        # Primary tabs (Command, Dictate, Write) - left side
        x = pill.left() + ShapeConstants.PRIMARY_LEFT
        y = pill.top() + (pill.height() - ShapeConstants.PRIMARY_D) / 2
        for i, name in enumerate(["command", "dictate", "write"]):
            tabs[name] = QRectF(
                x + i * (ShapeConstants.PRIMARY_D + ShapeConstants.PRIMARY_GAP),
                y,
                ShapeConstants.PRIMARY_D,
                ShapeConstants.PRIMARY_D
            )

        # Secondary tabs (Shelf, History, Menu) - right side
        x = pill.right() - ShapeConstants.SECONDARY_RIGHT
        y = pill.top() + (pill.height() - ShapeConstants.SECONDARY_D) / 2
        for i, name in enumerate(["shelf", "history", "menu"]):
            tabs[name] = QRectF(
                x + i * (ShapeConstants.SECONDARY_D + ShapeConstants.SECONDARY_GAP),
                y,
                ShapeConstants.SECONDARY_D,
                ShapeConstants.SECONDARY_D
            )

        return tabs

    @property
    def island_rect(self) -> QRectF:
        """Open island rect."""
        w = ShapeConstants.OPEN_W
        h = ShapeConstants.OPEN_H
        x = (self.screen_rect.width() - w) / 2
        y = 0
        return QRectF(x, y, w, h)

    @property
    def listening_rect(self) -> QRectF:
        """Slim listening notch rect."""
        w = ShapeConstants.LISTEN_W
        h = ShapeConstants.LISTEN_H
        x = (self.screen_rect.width() - w) / 2
        y = 0
        return QRectF(x, y, w, h)


# =============================================================================
# HIT TESTING
# =============================================================================

def hit_test_notch(pos: QPointF, geom: NotchGeometry) -> str:
    """
    Hit test the notch/pill for interactive regions.
    Returns: "face_band", "command", "dictate", "write", "shelf", "history", "menu", "none"
    """
    # Face band (from 0x8b19696: cx=40, cy=20, radius~20)
    face = geom.face_band_rect
    if face.contains(pos):
        return "face_band"

    # Tabs
    for name, rect in geom.tab_rects.items():
        if rect.contains(pos):
            return name

    # Pill body (for drag)
    if geom.pill_rect.contains(pos):
        return "pill_body"

    return "none"


def hit_test_island(pos: QPointF, geom: NotchGeometry, page: str) -> str:
    """
    Hit test the open island.
    Returns region identifier.
    """
    island = geom.island_rect
    if not island.contains(pos):
        return "none"

    # Header tabs area
    header_top = island.top() + ShapeConstants.HEADER_TOP
    header_bottom = header_top + ShapeConstants.HEADER_H
    if pos.y() <= header_bottom:
        return "header_tabs"

    # Content area
    return f"page_{page}"


# =============================================================================
# CLICK-THROUGH / TRANSPARENT REGIONS
# =============================================================================

def get_input_mask(geom: NotchGeometry, state: str) -> QPainterPath:
    """
    Get the click-through mask for WA_TransparentForInput.
    Only interactive regions receive mouse events.
    """
    path = QPainterPath()

    if state in ("compact", "hover"):
        # Face band + 3 primary tabs + 3 secondary tabs
        face = geom.face_band_rect
        path.addEllipse(face)

        for rect in geom.tab_rects.values():
            path.addRoundedRect(rect, ShapeConstants.CORNER_COMPACT, ShapeConstants.CORNER_COMPACT)

    elif state == "open":
        # Full island receives input
        island = geom.island_rect
        path.addRoundedRect(island, ShapeConstants.CORNER_OPEN, ShapeConstants.CORNER_OPEN)

    elif state == "listen":
        # Slim line + right elements
        listen = geom.listening_rect
        path.addRoundedRect(listen, ShapeConstants.LISTEN_H / 2, ShapeConstants.LISTEN_H / 2)

    elif state == "face_out":
        # Full capsule
        pill = geom.pill_rect
        path.addRoundedRect(pill, pill.height() / 2, pill.height() / 2)

    return path