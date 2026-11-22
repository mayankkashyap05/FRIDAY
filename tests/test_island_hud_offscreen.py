import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6.QtGui', reason='Qt system libraries are required')
from PySide6.QtWidgets import QApplication
from ui.island.voice_hud import VoiceHUD


def test_scenes_and_theme():
    app = QApplication.instance() or QApplication([])
    hud = VoiceHUD()
    for scene in hud.STATES:
        hud.set_scene(scene, immediate=True)
        assert not hud.current_path().isEmpty()
    hud.set_theme('light')
    assert hud.theme == 'light'
    hud.close()


def test_transparent_pixels_drop_and_background_bridge():
    import threading
    from PySide6.QtCore import QPoint, QPointF, QMimeData, QUrl, Qt
    from PySide6.QtGui import QImage, QPainter, QDropEvent, QDragEnterEvent
    from ui.bridge.controller import UiController
    app = QApplication.instance() or QApplication([])
    hud = VoiceHUD(); hud.set_scene('resting', immediate=True)
    img = QImage(hud.size(), QImage.Format_ARGB32_Premultiplied); img.fill(Qt.transparent)
    p = QPainter(img); hud.render(p, QPoint(0, 0)); p.end()
    assert img.pixelColor(0, 100).alpha() == 0
    assert img.pixelColor(360, 10).alpha() > 0
    received = []; hud.files_dropped.connect(received.append)
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile('/tmp/example.txt')])
    # Qt 6.8 only exposes the QPoint overload; newer releases add QPointF.
    try:
        enter = QDragEnterEvent(QPointF(360, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    except TypeError:
        enter = QDragEnterEvent(QPoint(360, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    hud.dragEnterEvent(enter); assert enter.isAccepted()
    drop = QDropEvent(QPointF(360, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    hud.dropEvent(drop); assert drop.isAccepted() and received == [['/tmp/example.txt']]
    controller = UiController(hud)
    def producer():
        controller.bridge.set_state('LISTENING')
        controller.bridge.setAmplitude(.6)
    worker = threading.Thread(target=producer); worker.start(); worker.join()
    assert hud.state == 'resting'
    for _ in range(5): app.processEvents()
    assert hud.state == 'listening' and hud.audio_level == .6
    hud.close()


def test_bridge_log_appears_in_chat():
    from ui.bridge.controller import UiController
    app = QApplication.instance() or QApplication([])
    hud = VoiceHUD(); controller = UiController(hud)
    controller.bridge.ingest('You: hello')
    controller.bridge.ingest('Friday: hello')
    for _ in range(5): app.processEvents()
    hud.set_scene('chat', immediate=True)
    assert hud.chat_lines == ['You: hello', 'Friday: hello']
    hud.close()


def test_resting_eye_centres_and_shoulders():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QImage, QPainter
    app = QApplication.instance() or QApplication([])
    hud = VoiceHUD(); hud.preview_mode = True; hud.set_scene('resting', immediate=True)
    img = QImage(hud.size(), QImage.Format_ARGB32_Premultiplied); img.fill(Qt.transparent)
    p = QPainter(img); hud.render(p, QPoint()); p.end()
    eye_x = [x for x in range(300, 420) if img.pixelColor(x, 24).red() > 240 and img.pixelColor(x, 24).alpha() > 240]
    assert eye_x and abs((min(eye_x) + max(eye_x))/2 - 360) < 1
    assert hud.current_path().contains(QPoint(238, 1))  # body starts at x=240
    assert not hud.current_path().contains(QPoint(238, 30))
    hud.close()


def test_hover_tabs_and_face_out_drag():
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    app = QApplication.instance() or QApplication([])
    hud = VoiceHUD(); hud.set_scene('hover', immediate=True)
    left = (hud.width()-hud.width_spring.value)/2
    local = QPointF(left+155, 135)
    press = QMouseEvent(QEvent.MouseButtonPress, local, local, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    release = QMouseEvent(QEvent.MouseButtonRelease, local, local, Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    hud.mousePressEvent(press); hud.mouseReleaseEvent(release)
    assert hud.state == 'chat'
    hud.set_scene('resting', immediate=True)
    a, b = QPointF(360, 20), QPointF(390, 70)
    press = QMouseEvent(QEvent.MouseButtonPress, a, a, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    hud.mousePressEvent(press)
    move = QMouseEvent(QEvent.MouseMove, b, b, Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    hud.mouseMoveEvent(move)
    assert hud.state == 'face_out'
    hud.set_scene('face_out', immediate=True)
    assert hud.current_path().boundingRect().top() == 18
    hud.close()


def test_hover_hit_areas_match_painted_tiles():
    from PySide6.QtCore import QPointF
    app = QApplication.instance() or QApplication([])
    hud = VoiceHUD(); hud.set_scene('hover', immediate=True)
    left = (hud.width() - hud.width_spring.value) / 2
    primary = hud._primary_rects(left); tabs = hud._tab_rects(left)
    assert all(not a.intersects(b) for i, a in enumerate(primary) for b in primary[i+1:])
    assert all(not a.intersects(b) for i, a in enumerate(tabs) for b in tabs[i+1:])
    assert not any(rect.contains(QPointF(left + 283, 80)) for rect in primary)
    hud.close()


def test_detached_face_has_reference_capsule_proportions():
    app = QApplication.instance() or QApplication([])
    hud = VoiceHUD()
    hud.set_scene('face_out', immediate=True)
    bounds = hud.current_path().boundingRect()
    assert bounds.width() == 184 and bounds.height() == 96
    assert not hud.current_path().contains(bounds.topLeft())
    hud.shutdown(); hud.close(); app.processEvents()
