"""Antialiased island outline in logical Qt coordinates."""
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPath

KAPPA = 0.5522847498307936


def notch_path(body: QRectF, shoulder: float = 14, corner: float = 14,
               shoulder_scale: float = 1.0) -> QPainterPath:
    """Return a top-docked notch, morphing continuously to a capsule at 0.

    body describes the vertical body, not the ears. The ears extend outside it.
    At scale 0 all four corners are convex and the shape is detached-compatible.
    """
    x, y, w, h = body.x(), body.y(), body.width(), body.height()
    path = QPainterPath()
    if w <= 0 or h <= 0:
        return path
    t = max(0.0, min(1.0, shoulder_scale))
    s = max(0.0, shoulder) * t
    r = max(0.0, min(corner, w/2, h/2))
    top_r = r * (1.0 - t)
    # Interpolate the cubic control points, not two separate paths: at t=1
    # this is the outward concave ear; at t=0 it is the convex capsule top.
    # This avoids the old shape popping to a rounded rectangle in its last frame.
    k = KAPPA
    path.moveTo(x-s+top_r, y)
    path.lineTo(x+w+s-top_r, y)
    path.cubicTo(x+w+s-top_r+k*(top_r-s), y,
                 x+w, y+(s+top_r)*(1-k), x+w, y+s+top_r)
    path.lineTo(x+w, y+h-r)
    path.cubicTo(x+w, y+h-r+k*r, x+w-r+k*r, y+h, x+w-r, y+h)
    path.lineTo(x+r, y+h)
    path.cubicTo(x+r-k*r, y+h, x, y+h-r+k*r, x, y+h-r)
    path.lineTo(x, y+s+top_r)
    path.cubicTo(x, y+(s+top_r)*(1-k),
                 x-s+top_r-k*(top_r-s), y, x-s+top_r, y)
    path.closeSubpath()
    return path
    k = KAPPA
    path.moveTo(x-s, y)
    path.lineTo(x+w+s, y)
    # Outside edge -> body side. Horizontal and vertical tangents match.
    path.cubicTo(x+w+s-k*s, y, x+w, y+s-k*s, x+w, y+s)
    path.lineTo(x+w, y+h-r)
    path.cubicTo(x+w, y+h-r+k*r, x+w-r+k*r, y+h, x+w-r, y+h)
    path.lineTo(x+r, y+h)
    path.cubicTo(x+r-k*r, y+h, x, y+h-r+k*r, x, y+h-r)
    path.lineTo(x, y+s)
    path.cubicTo(x, y+s-k*s, x-s+k*s, y, x-s, y)
    path.closeSubpath()
    return path


def contains(path: QPainterPath, x: float, y: float) -> bool:
    return path.contains(QPointF(x, y))


def docked_origin(screen: QRectF, width: float) -> QPointF:
    return QPointF(screen.x() + (screen.width()-width)/2, screen.y())


def clamp_to_screen(rect: QRectF, screen: QRectF) -> QRectF:
    w, h = min(rect.width(), screen.width()), min(rect.height(), screen.height())
    x = max(screen.left(), min(rect.x(), screen.right()-w))
    y = max(screen.top(), min(rect.y(), screen.bottom()-h))
    return QRectF(x, y, w, h)
