import pytest
pytest.importorskip('PySide6.QtGui', reason='Qt system libraries are required')
from ui.mascot.eyes import Blink, gaze
from ui.themes.tokens import readable_eye_colour, contrast


def test_gaze_bounded():
    x, y = gaze(1e6, -1e6)
    assert -1 <= x <= 1 and -1 <= y <= 1


def test_blink_closes_and_reopens():
    blink = Blink(); blink.next_blink = 1
    assert blink.value(1) == 1
    assert blink.value(1.09) < .1
    assert blink.value(1.2) == 1


def test_eye_contrast():
    for bg in ('#000000', '#F5F6F8'):
        assert contrast(readable_eye_colour('#888888', bg), bg) >= 4.5
