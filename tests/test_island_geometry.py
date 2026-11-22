import pytest
pytest.importorskip('PySide6.QtGui', reason='Qt system libraries are required')
from PySide6.QtCore import QRectF
from ui.island.geometry import notch_path, contains, clamp_to_screen, docked_origin


def test_outward_shoulders_and_hit_test():
    path = notch_path(QRectF(50, 0, 200, 48), 16, 14)
    bounds = path.boundingRect()
    assert bounds.left() == 34 and bounds.right() == 266
    assert contains(path, 150, 20) and not contains(path, 10, 30)
    assert not contains(path, 50, 47)


def test_capsule_and_monitor_clamp():
    path = notch_path(QRectF(50, 0, 200, 48), 16, 24, 0)
    assert path.boundingRect() == QRectF(50, 0, 200, 48)
    screen = QRectF(-1920, 0, 1920, 1080)
    assert docked_origin(screen, 720).x() == -1320
    assert clamp_to_screen(QRectF(-3000, -100, 720, 320), screen) == QRectF(-1920, 0, 720, 320)


def test_morph_is_continuous_at_capsule_endpoint():
    body = QRectF(50, 0, 200, 48)
    capsule = notch_path(body, 16, 20, 0)
    almost = notch_path(body, 16, 20, .00001)
    assert capsule.elementCount() == almost.elementCount()
    for i in range(capsule.elementCount()):
        a, b = capsule.elementAt(i), almost.elementAt(i)
        assert abs(a.x-b.x) < .001 and abs(a.y-b.y) < .001
