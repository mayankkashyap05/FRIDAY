"""Render polished offscreen previews of the compact island and Settings Hub."""
import os
import sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
if sys.platform == 'win32':
    os.environ.setdefault('QT_QPA_FONTDIR', os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts'))
from pathlib import Path
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QImage, QPainter, QLinearGradient
from PySide6.QtWidgets import QApplication
from .voice_hud import VoiceHUD
from .search_bar import SearchBarPopup
from ui.bridge.controller import UiController

SCENES = [
    ('01_resting_notch', 'resting', 'dark', {}),
    ('02_notch_search_popup', 'resting', 'dark', {'search': True}),
    ('03_slim_listening', 'listening', 'dark', {'transcript': 'Remind me to call Mom at six'}),
    ('04_dictation', 'dictation', 'dark', {'transcript': 'Dictating a note'}),
    ('05_working', 'working', 'dark', {}),
    ('06_speaking', 'speaking', 'dark', {'transcript': 'Calling Mom at six, noted.'}),
    ('07_success', 'success', 'dark', {}),
    ('08_muted', 'muted', 'dark', {}),
    ('09_error', 'error', 'dark', {}),
    ('10_face_out_capsule', 'face_out', 'dark', {}),
    ('11_light_mode_search', 'resting', 'light', {'search': True}),
    ('12_drop_zone', 'resting', 'dark', {'drop': True}),
    ('13_settings_hub_general', 'resting', 'dark', {'hub': 'General'}),
    ('14_settings_hub_chat', 'resting', 'dark', {'hub': 'Chat'}),
    ('15_settings_hub_tasks', 'resting', 'dark', {'hub': 'Tasks'}),
]


def main():
    app = QApplication.instance() or QApplication([])
    out = Path('artifacts/ui_previews'); out.mkdir(parents=True, exist_ok=True)
    hud = VoiceHUD(); hud.preview_mode = True; controller = UiController(hud); hud._timer.stop(); hud.audio_level = .65
    popup = controller.search_popup
    for name, state, theme, opts in SCENES:
        hud.live_transcript = opts.get('transcript', '')
        hud._drop_active = bool(opts.get('drop', False)); hud.drop_glow_spring.set(float(hud._drop_active))
        hud.set_theme(theme); popup.set_theme(theme); hud.set_scene(state, immediate=True)
        if opts.get('hub'):
            controller.settings_window.select_tab(opts['hub']); controller.settings_window.set_theme(theme)
            widget = controller.settings_window; widget.ensurePolished(); widget.show(); app.processEvents()
            image = widget.grab().toImage(); widget.hide()
        else:
            image = QImage(1366, 768, QImage.Format_ARGB32_Premultiplied); image.fill(Qt.transparent)
            p = QPainter(image)
            grad = QLinearGradient(0, 0, 1366, 768); grad.setColorAt(0, QColor('#211091')); grad.setColorAt(.55, QColor('#441077')); grad.setColorAt(1, QColor('#090018'))
            p.fillRect(image.rect(), grad)
            app.processEvents(); p.drawImage(QPoint((1366-hud.width())//2, 0), hud.grab().toImage())
            if opts.get('search'):
                popup.open_below(hud, immediate=True); popup.input.setText(''); app.processEvents(); p.drawImage(popup.pos(), popup.grab().toImage()); popup.hide()
            p.end()
        path = out / f'{name}.png'
        if not image.save(str(path)): raise RuntimeError(f'Cannot save {path}')
        print(path)
    controller.shutdown(); hud.close()

if __name__ == '__main__': main()
