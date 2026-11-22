"""Minimal eye rig; no Mochi body."""
import math
import random
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QRadialGradient, QPen


def gaze(dx, dy):
    return math.tanh(dx/260), math.tanh(dy/200)


class Blink:
    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.next_blink = self.rng.uniform(2.5, 6.0)
        self.started = None

    def value(self, now):
        if self.started is None and now >= self.next_blink:
            self.started = now
        if self.started is None:
            return 1.0
        elapsed = max(0.0, now-self.started)
        if elapsed >= .18:
            self.started = None
            self.next_blink = now + (.22 if self.rng.random() < .12 else self.rng.uniform(2.5, 6))
            return 1.0
        if elapsed < .06:
            t = elapsed / .06
            return 1.0 - .92 * (t * t * (3 - 2*t))
        t = (elapsed - .06) / .12
        return .08 + .92 * (t * t * (3 - 2*t))


def paint_eyes(painter, cx, cy, *, colour='#ffffff', scale=1.0, blink=1.0, look=(0, 0), halo=False, dim=False, spacing=13, halo_strength=1.0, halo_colour='#ffffff', happy=False):
    if halo:
        tint = QColor(halo_colour)
        for offset in (-spacing, spacing):
            hx = cx + offset * scale
            radius = 25 * scale
            grad = QRadialGradient(hx, cy, radius)
            tint.setAlpha(max(0, min(255, int(30 * halo_strength))))
            grad.setColorAt(0, tint)
            tint.setAlpha(0); grad.setColorAt(1, tint)
            painter.setPen(QColor(0, 0, 0, 0)); painter.setBrush(grad)
            painter.drawEllipse(QRectF(hx-radius, cy-radius, radius*2, radius*2))
    c = QColor(colour)
    if dim: c.setAlpha(100)
    painter.setPen(QColor(0, 0, 0, 0)); painter.setBrush(c)
    w, h = 7*scale * (1 - .08 * (1 - blink)), 14*scale*blink
    for offset in (-spacing, spacing):
        x = cx + (offset + look[0]*2)*scale - w/2
        y = cy + look[1]*2*scale - h/2
        if happy:
            painter.setBrush(QColor(0, 0, 0, 0)); painter.setPen(QPen(c, 2.0 * scale))
            painter.drawArc(QRectF(x-2*scale, y, w+4*scale, h), 20*16, 140*16)
        else:
            painter.drawRoundedRect(QRectF(x, y, w, h), w/2, w/2)
