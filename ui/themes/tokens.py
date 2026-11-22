"""Verified Hey Nixi colour roles and WCAG contrast utilities."""
from PySide6.QtGui import QColor

DARK = {
    'background': '#000000',
    'surface': '#141518',
    'card': '#1D1F24',
    'card_hover': '#262930',
    'mascot_well': '#16171C',
    'border': '#2D3038',
    'ink': '#F5F6F8',
    'muted': '#8E939C',
    'accent': '#3882F6',
    'success': '#34C759',
    'danger': '#FF3B30',
    'warning': '#FF9F0A',
    'capsule_top': '#1E1F26',
    'capsule_mid': '#0E0F13',
    'capsule_bottom': '#050507',
    'rim': (255, 255, 255, 26),
    'capsule_rim': (255, 255, 255, 48),
}

LIGHT = {
    'background': '#F5F6F8',
    'surface': '#FFFFFF',
    'card': '#ECEEF2',
    'card_hover': '#E1E4EB',
    'mascot_well': '#E9ECF1',
    'border': '#D3D6DC',
    'ink': '#141518',
    'muted': '#62666D',
    'accent': '#2563EB',
    'success': '#24A148',
    'danger': '#DC2626',
    'warning': '#D97706',
    'capsule_top': '#FFFFFF',
    'capsule_mid': '#F3F4F6',
    'capsule_bottom': '#E5E7EB',
    'rim': (20, 21, 24, 24),
    'capsule_rim': (20, 21, 24, 38),
}


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