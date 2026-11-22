"""Render native island scenes with the actual panel widgets offscreen."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QImage, QPainter, QLinearGradient
from PySide6.QtWidgets import QApplication
from .voice_hud import VoiceHUD
from ui.bridge.controller import UiController

SCENES = [('01_resting_notch','resting','dark'), ('02_hover_expanded','hover','dark'), ('03_slim_listening','listening','dark'), ('04_dictation','dictation','dark'), ('05_write_scene','write','dark'), ('06_island_open_chat','chat','dark'), ('07_island_open_media','media','dark'), ('08_island_open_tasks','tasks','dark'), ('09_island_open_settings','settings','dark'), ('10_face_out_capsule','face_out','dark'), ('11_light_mode','resting','light')]


def main():
    app = QApplication.instance() or QApplication([])
    out = Path('artifacts/ui_previews'); out.mkdir(parents=True, exist_ok=True)
    hud = VoiceHUD(); controller = UiController(hud); hud._timer.stop(); hud.preview_mode = True; hud.audio_level = .65
    for name, state, theme in SCENES:
        hud.set_theme(theme); hud.set_scene(state, immediate=True)
        image = QImage(2732, 1536, QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(2)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        gradient = QLinearGradient(0, 0, 1366, 768)
        gradient.setColorAt(0, QColor('#211091')); gradient.setColorAt(.55, QColor('#441077')); gradient.setColorAt(1, QColor('#090018'))
        painter.fillRect(0, 0, 1366, 768, gradient)
        # Grab the widget at the offscreen platform's native DPR. Direct
        # QWidget.render into an image misplaces opacity-effect children.
        app.processEvents()
        layer = hud.grab().toImage()
        painter.drawImage(QPoint((1366-hud.width())//2, 0), layer)
        painter.end()
        path = out / f'{name}.png'
        if not image.save(str(path)): raise RuntimeError(f'Cannot save {path}')
        print(path)
    controller.shutdown(); hud.close()

if __name__ == '__main__': main()
