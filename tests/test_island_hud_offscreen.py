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
    local = hud._tab_rects(left)[0].center()
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
    assert not any(rect.contains(QPointF(left + 43, 22)) for rect in primary)
    hud.close()


def test_detached_face_has_reference_capsule_proportions():
    app = QApplication.instance() or QApplication([])
    hud = VoiceHUD()
    hud.set_scene('face_out', immediate=True)
    bounds = hud.current_path().boundingRect()
    assert bounds.width() == 184 and bounds.height() == 96
    assert not hud.current_path().contains(bounds.topLeft())
    hud.shutdown(); hud.close(); app.processEvents()


def test_theme_selectors_and_font_warning_free(qt_app):
    from pathlib import Path
    from PySide6.QtCore import qInstallMessageHandler
    messages = []
    old = qInstallMessageHandler(lambda _type, _context, text: messages.append(text))
    try:
        hud = VoiceHUD()
        for theme in ('dark', 'light'):
            text = (Path('ui/themes') / (theme + '.qss')).read_text()
            assert text.lstrip().startswith('* {') and 'font-size: 9pt' in text
            hud.set_theme(theme)
            qt_app.processEvents()
        hud.shutdown(); hud.close()
    finally:
        qInstallMessageHandler(old)
    assert not [msg for msg in messages if 'QFont::setPointSize' in msg]


def test_idle_timer_sleeps_and_wakes(qt_app):
    hud = VoiceHUD()
    hud.preview_mode = True
    hud.set_scene('resting', immediate=True)
    hud._last_cursor = __import__('PySide6.QtGui', fromlist=['QCursor']).QCursor.pos()
    hud._animate()
    assert hud._timer.interval() == 150
    hud.set_scene('hover')
    assert hud._timer.interval() == 16
    hud.shutdown(); hud.close()


def test_tile_hover_press_and_tab_hit_feedback(qt_app):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    hud = VoiceHUD(); hud.set_scene('hover', immediate=True)
    tile = hud._primary_rects((hud.width()-640)/2)[1].center()
    move = QMouseEvent(QEvent.MouseMove, tile, tile, Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    hud.mouseMoveEvent(move)
    assert hud._hover_tile == 1 and hud.tile_hover_springs[1].target == 1
    press = QMouseEvent(QEvent.MouseButtonPress, tile, tile, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    hud.mousePressEvent(press)
    assert hud.tile_press_springs[1].target == 1
    release = QMouseEvent(QEvent.MouseButtonRelease, tile, tile, Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    hud.mouseReleaseEvent(release)
    assert hud.tile_press_springs[1].target == 0
    assert hud.state == 'dictation'
    hud.shutdown(); hud.close()


def test_panel_remains_inset_and_hidden_on_collapse(headless_ui):
    hud = headless_ui.hud
    hud.set_scene('chat', immediate=True)
    assert not hud._panel_stack.isHidden()
    assert hud._panel_stack.geometry().right() < hud.current_path().boundingRect().right() - 5
    hud.set_scene('resting')
    assert hud._panel_stack.isHidden()
    assert hud.content_alpha_spring.target == 0


def test_mismatched_tile_press_release_does_not_activate(qt_app):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    hud = VoiceHUD(); hud.set_scene('hover', immediate=True)
    a, b = [r.center() for r in hud._primary_rects((hud.width()-640)/2)[:2]]
    emitted = []; hud.mode_requested.connect(emitted.append)
    hud.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, a, a, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    hud.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, b, b, Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    assert hud.state == 'hover' and emitted == []
    hud.shutdown(); hud.close()
