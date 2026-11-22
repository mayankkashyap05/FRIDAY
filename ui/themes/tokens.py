"""Verified Hey Nixi colour roles and WCAG contrast utilities."""
from PySide6.QtGui import QColor

DARK = {'background': '#000000', 'surface': '#141518', 'card': '#1D1F23', 'ink': '#F5F6F8', 'muted': '#8E939C', 'accent': '#3882F6'}
LIGHT = {'background': '#F5F6F8', 'surface': '#FFFFFF', 'card': '#E5E7EB', 'ink': '#141518', 'muted': '#62666D', 'accent': '#3882F6'}


def luminance(colour):
    c = QColor(colour)
    if not c.isValid():
        raise ValueError('invalid colour')
    def linear(x):
        return x/12.92 if x <= .04045 else ((x+.055)/1.055)**2.4
    return .2126*linear(c.redF()) + .7152*linear(c.greenF()) + .0722*linear(c.blueF())


def contrast(a, b):
    x, y = sorted((luminance(a), luminance(b)))
    return (y+.05)/(x+.05)


def readable_eye_colour(colour, background, minimum=4.5):
    if contrast(colour, background) >= minimum:
        return QColor(colour).name()
    return max(('#ffffff', '#141518'), key=lambda candidate: contrast(candidate, background))
