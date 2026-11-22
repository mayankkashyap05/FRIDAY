"""Minimal eye rig; no Mochi body."""
import math
import random
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QRadialGradient


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
        t = (now-self.started)/.18
        if t >= 1:
            self.started = None
            self.next_blink = now + (.22 if self.rng.random() < .12 else self.rng.uniform(2.5, 6))
            return 1.0
        return max(.08, abs(2*t-1))


def paint_eyes(painter, cx, cy, *, colour='#ffffff', scale=1.0, blink=1.0, look=(0, 0), halo=False, dim=False, spacing=13):
    if halo:
        grad = QRadialGradient(cx, cy, 40*scale)
        grad.setColorAt(0, QColor(255,255,255,32)); grad.setColorAt(1, QColor(255,255,255,0))
        painter.setPen(QColor(0, 0, 0, 0)); painter.setBrush(grad)
        painter.drawEllipse(QRectF(cx-40*scale, cy-40*scale, 80*scale, 80*scale))
    c = QColor(colour)
    if dim: c.setAlpha(100)
    painter.setPen(QColor(0, 0, 0, 0)); painter.setBrush(c)
    w, h = 7*scale, 14*scale*blink
    for offset in (-spacing, spacing):
        x = cx + (offset + look[0]*2)*scale - w/2
        y = cy + look[1]*2*scale - h/2
        painter.drawRoundedRect(QRectF(x, y, w, h), w/2, w/2)
