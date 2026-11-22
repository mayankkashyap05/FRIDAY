"""Expressive Hey Nixi pill-eye rig with organic blinking, gaze, and soft ambient glow."""
import math
import random
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QLinearGradient, QRadialGradient, QPen


def gaze(dx, dy):
    return math.tanh(dx / 260.0), math.tanh(dy / 200.0)


class Blink:
    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.next_blink = self.rng.uniform(2.5, 6.0)
        self.started = None
        self._double_pending = False

    def trigger(self, now):
        """Force an immediate blink (e.g., on eye poke)."""
        self.started = now
        self._double_pending = False

    def value(self, now):
        if self.started is None and now >= self.next_blink:
            self.started = now
        if self.started is None:
            return 1.0
        elapsed = max(0.0, now - self.started)
        if elapsed >= 0.18:
            self.started = None
            if not self._double_pending and self.rng.random() < 0.14:
                self._double_pending = True
                self.next_blink = now + 0.18
            else:
                self._double_pending = False
                self.next_blink = now + self.rng.uniform(2.6, 5.8)
            return 1.0
        if elapsed < 0.06:
            t = elapsed / 0.06
            return 1.0 - 0.92 * (t * t * (3.0 - 2.0 * t))
        t = (elapsed - 0.06) / 0.12
        return 0.08 + 0.92 * (t * t * (3.0 - 2.0 * t))


def paint_eyes(
    painter,
    cx,
    cy,
    *,
    colour='#ffffff',
    scale=1.0,
    blink=1.0,
    look=(0, 0),
    halo=False,
    dim=False,
    spacing=13,
    halo_strength=1.0,
    halo_colour='#ffffff',
    happy=False,
    mood='normal',
    working_phase=None,
    listening_ring=0.0,
):
    painter.save()
    lx, ly = look[0], look[1]

    if halo:
        base_tint = QColor(halo_colour)
        strength = max(0.0, float(halo_strength))
        for offset in (-spacing, spacing):
            hx = cx + (offset + lx * 1.4) * scale
            hy = cy + ly * 1.2 * scale
            # Outer soft ambient wash
            outer_r = 26.0 * scale
            outer = QRadialGradient(hx, hy, outer_r)
            c0 = QColor(base_tint)
            c0.setAlpha(max(0, min(255, int(34 * strength))))
            c1 = QColor(base_tint)
            c1.setAlpha(max(0, min(255, int(12 * strength))))
            c2 = QColor(base_tint)
            c2.setAlpha(0)
            outer.setColorAt(0.0, c0)
            outer.setColorAt(0.45, c1)
            outer.setColorAt(1.0, c2)
            painter.setPen(Qt.NoPen)
            painter.setBrush(outer)
            painter.drawEllipse(QRectF(hx - outer_r, hy - outer_r, outer_r * 2, outer_r * 2))

            # Tight luminous core bloom directly behind each pill eye
            inner_r = 12.5 * scale
            inner = QRadialGradient(hx, hy, inner_r)
            ic0 = QColor(base_tint)
            ic0.setAlpha(max(0, min(255, int(56 * strength))))
            ic1 = QColor(base_tint)
            ic1.setAlpha(0)
            inner.setColorAt(0.0, ic0)
            inner.setColorAt(1.0, ic1)
            painter.setBrush(inner)
            painter.drawEllipse(QRectF(hx - inner_r, hy - inner_r, inner_r * 2, inner_r * 2))

        if listening_ring > 0.01:
            ring_c = QColor(halo_colour)
            ring_c.setAlpha(max(0, min(255, int(72 * (1.0 - listening_ring)))))
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(ring_c, 1.4 * scale))
            rx = (spacing + 14.0 + 10.0 * listening_ring) * scale
            ry = (12.0 + 7.0 * listening_ring) * scale
            painter.drawRoundedRect(QRectF(cx - rx, cy - ry, rx * 2, ry * 2), ry, ry)

    c = QColor(colour)
    if dim or mood == 'muted':
        c.setAlpha(95)

    effective_blink = max(0.06, float(blink))
    if mood == 'muted':
        effective_blink *= 0.62
    elif mood == 'error':
        effective_blink *= 0.78
    elif mood == 'booped':
        effective_blink *= 0.85

    w = 7.2 * scale * (1.0 + 0.09 * (1.0 - effective_blink))
    h = 14.5 * scale * effective_blink

    for idx, offset in enumerate((-spacing, spacing)):
        ex = cx + (offset + lx * 2.2) * scale - w / 2.0
        ey = cy + ly * 2.0 * scale - h / 2.0

        if mood == 'dizzy':
            orbit = 1.6 * scale
            phase = (1 if idx == 0 else -1)
            ex += math.cos(lx * 6.0) * orbit * phase
            ey += math.sin(ly * 6.0) * orbit

        if happy or mood == 'happy':
            # Upturned arched smile eyes matching Hey Nixi "Done" reference
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(c, 2.3 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            arc_rect = QRectF(ex - 1.8 * scale, ey + 1.0 * scale, w + 3.6 * scale, h * 0.92)
            painter.drawArc(arc_rect, 22 * 16, 136 * 16)
        else:
            painter.setPen(Qt.NoPen)
            if scale >= 1.25 and not (dim or mood == 'muted'):
                eye_grad = QLinearGradient(ex, ey, ex, ey + h)
                top_c = QColor(c)
                bot_c = QColor(c).darker(108)
                eye_grad.setColorAt(0.0, top_c)
                eye_grad.setColorAt(1.0, bot_c)
                painter.setBrush(eye_grad)
            else:
                painter.setBrush(c)
            radius = min(w / 2.0, h / 2.0)
            painter.drawRoundedRect(QRectF(ex, ey, w, h), radius, radius)

    # Subtle rising thought-bubble dots near the right eye during "working"
    if working_phase is not None and mood == 'working':
        painter.setPen(Qt.NoPen)
        right_eye_x = cx + spacing * scale + 5.5 * scale
        right_eye_y = cy - 4.0 * scale
        for i in range(3):
            pulse = 0.5 + 0.5 * math.sin(working_phase * 4.2 - i * 0.85)
            dot_c = QColor(colour)
            dot_c.setAlpha(int(75 + 150 * pulse))
            painter.setBrush(dot_c)
            d = (2.2 + i * 0.85) * scale
            dx = right_eye_x + i * 4.2 * scale
            dy = right_eye_y - i * 3.0 * scale - 0.8 * pulse * scale
            painter.drawEllipse(QPointF(dx, dy), d / 2.0, d / 2.0)

    painter.restore()