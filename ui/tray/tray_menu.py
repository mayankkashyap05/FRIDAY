"""Native Qt system tray; all callbacks run on the Qt thread."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter, QColor, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


class TrayMenu:
    def __init__(self, ui):
        self.ui = ui
        image = QPixmap(64, 64); image.fill(Qt.transparent)
        p = QPainter(image); p.setRenderHint(QPainter.Antialiasing); p.setPen(Qt.NoPen)
        p.setBrush(QColor('#141518')); p.drawRoundedRect(3, 13, 58, 38, 19, 19)
        p.setBrush(QColor('#ffffff')); p.drawRoundedRect(21, 25, 5, 14, 2.5, 2.5); p.drawRoundedRect(38, 25, 5, 14, 2.5, 2.5); p.end()
        self.icon = QSystemTrayIcon(QIcon(image))
        self.icon.setToolTip('FRIDAY')
        self.menu = QMenu()
        self.menu.setStyleSheet(ui.hud.styleSheet())
        ui.hud.theme_changed.connect(self._theme_changed)
        self.menu.addAction('Show / hide notch', self.toggle_visible)
        self.menu.addAction('Pull out / dock', self.toggle_dock)
        self.menu.addAction('Mute / unmute microphone', ui._toggle_mute)
        self.menu.addAction('Daily briefing', lambda: ui.submit_text("Give me today's daily briefing."))
        self.menu.addAction('Settings', self.open_settings)
        self.menu.addSeparator(); self.menu.addAction('Quit', ui.quit)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        if QSystemTrayIcon.isSystemTrayAvailable(): self.icon.show()

    def _theme_changed(self, _theme):
        self.menu.setStyleSheet(self.ui.hud.styleSheet())

    def _activated(self, reason):
        if reason == QSystemTrayIcon.Trigger: self.toggle_visible()

    def toggle_visible(self):
        if self.ui.visible: self.ui.hide()
        else: self.ui.show()

    def toggle_dock(self):
        self.ui.controller.hud.set_scene('resting' if self.ui.controller.hud.state == 'face_out' else 'face_out')

    def open_settings(self):
        self.ui.show(); self.ui.controller.hud.set_scene('settings')

    def close(self):
        if self.icon is None:
            return
        self.icon.hide()
        self.ui.hud.theme_changed.disconnect(self._theme_changed)
        self.icon.activated.disconnect(self._activated)
        self.icon.setContextMenu(None)
        self.icon.deleteLater()
        self.menu.deleteLater()
        self.icon = self.menu = None
