"""Standalone prototype: arrow keys cycle scenes, T toggles theme, Esc quits."""
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QComboBox, QSlider
from .voice_hud import VoiceHUD
from ui.bridge.controller import UiController


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    hud = VoiceHUD(); hud.show()
    controller = UiController(hud)
    toolbar = QWidget(); toolbar.setWindowTitle('FRIDAY island prototype')
    layout = QVBoxLayout(toolbar)
    layout.addWidget(QLabel('Prototype only — no backend connection. Drag the island to detach.'))
    scenes = QComboBox(); scenes.addItems(VoiceHUD.STATES); scenes.currentTextChanged.connect(hud.set_scene); layout.addWidget(scenes)
    theme = QPushButton('Toggle theme'); theme.clicked.connect(lambda: hud.set_theme('light' if hud.theme == 'dark' else 'dark')); layout.addWidget(theme)
    for key, callback in [('Escape', app.quit), ('T', theme.click), ('Right', lambda: scenes.setCurrentIndex((scenes.currentIndex()+1)%scenes.count())), ('Left', lambda: scenes.setCurrentIndex((scenes.currentIndex()-1)%scenes.count()))]:
        shortcut = QShortcut(QKeySequence(key), toolbar); shortcut.activated.connect(callback)
    layout.addWidget(QLabel('Simulated audio level (for preview only)'))
    level = QSlider(Qt.Horizontal); level.setRange(0, 100)
    level.valueChanged.connect(lambda value: controller.bridge.setAmplitude(value/100))
    layout.addWidget(level)
    toolbar.show()
    return app.exec()

if __name__ == '__main__':
    sys.exit(main())
