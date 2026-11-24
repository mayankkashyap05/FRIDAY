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


def test_click_opens_search_and_drag_detaches_without_launcher():
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from ui.bridge.controller import UiController
    app = QApplication.instance() or QApplication([])
    hud = VoiceHUD(); ctrl = UiController(hud); hud.set_scene('resting', immediate=True)
    pt = QPointF(360, 20)
    hud.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, pt, pt, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    hud.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, pt, pt, Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    assert ctrl.search_popup.isVisible() and ctrl.search_popup.input.placeholderText() == 'Search or Ask'
    assert not hasattr(hud, '_panel_stack') and hud.state == 'resting'
    ctrl.search_popup.hide()
    a, b = QPointF(360, 20), QPointF(390, 70)
    hud.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, a, a, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    hud.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, b, b, Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    assert hud.state == 'face_out'
    hud.set_scene('face_out', immediate=True)
    assert hud.current_path().boundingRect().top() == 18
    ctrl.shutdown(); hud.close()


def test_hover_never_expands_notch_or_displays_launcher(qt_app):
    hud = VoiceHUD(); hud.set_scene('hover', immediate=True)
    assert hud.state == 'resting'
    assert hud.width_spring.value == 240 and hud.height_spring.value == 48
    assert not hasattr(hud, '_panel_stack')
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
    assert hud.width_spring.target == 240
    hud._animate()
    assert hud._timer.interval() == 150
    hud.shutdown(); hud.close()


def test_enter_submits_search_and_escape_dismisses(qt_app):
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    from ui.bridge.controller import UiController
    hud = VoiceHUD(); controller = UiController(hud)
    sent = []; controller.on_text_command = sent.append
    popup = controller.search_popup; popup.open_below(hud); popup.input.setText('find the answer')
    popup.input.returnPressed.emit()
    assert not popup.isVisible()
    # PanelServices invokes the user callback on a worker thread.
    import time
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and not sent: qt_app.processEvents(); time.sleep(.005)
    assert sent == ['find the answer']
    popup.open_below(hud); QTest.keyClick(popup.input, Qt.Key_Escape)
    assert not popup.isVisible()
    controller.shutdown(); hud.close()


def test_legacy_scene_names_are_compact_compatibility_aliases(qt_app):
    hud = VoiceHUD()
    for scene in ('hover', 'chat', 'media', 'tasks', 'vision', 'shelf', 'settings', 'write'):
        hud.set_scene(scene, immediate=True)
        assert hud.state == 'resting' and hud.width_spring.value == 240
    hud.shutdown(); hud.close()


def test_search_popup_uses_in_bounds_shadow_and_safe_offscreen_focus(qt_app):
    from PySide6.QtCore import QPoint, qInstallMessageHandler
    from ui.bridge.controller import UiController
    from ui.island.search_bar import PAD_X, PAD_TOP, PAD_BOTTOM, PILL_WIDTH, PILL_HEIGHT
    hud = VoiceHUD(); controller = UiController(hud)
    popup = controller.search_popup
    assert popup.graphicsEffect() is None
    assert popup.size().width() == PAD_X * 2 + PILL_WIDTH
    assert popup.size().height() == PAD_TOP + PILL_HEIGHT + PAD_BOTTOM
    messages = []
    previous = qInstallMessageHandler(lambda _type, _context, message: messages.append(message))
    try:
        popup.set_theme('dark'); popup.open_below(hud); qt_app.processEvents()
        visible_top_center = popup.mapToGlobal(QPoint(PAD_X + PILL_WIDTH // 2, PAD_TOP))
        expected = hud.mapToGlobal(QPoint(hud.width() // 2, int(round(hud.height_spring.value)) + 14))
        assert visible_top_center == expected
        assert not popup.grab().isNull()
        popup.set_theme('light'); qt_app.processEvents()
        assert not popup.grab().isNull()
        controller.show_settings('General'); qt_app.processEvents()
        controller.search_popup.dismiss(); controller.settings_window.hide()
    finally:
        qInstallMessageHandler(previous)
    assert not [message for message in messages if 'does not support raise' in message]
    controller.shutdown(); hud.close()
