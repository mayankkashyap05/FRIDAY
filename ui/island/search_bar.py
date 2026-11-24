"""Compact, activatable Search or Ask pill shown from the resting island."""
from PySide6.QtCore import Qt, Signal, QEvent, QPropertyAnimation, QEasingCurve, QRectF, QPoint
from PySide6.QtGui import QColor, QPainter, QPen, QLinearGradient, QGuiApplication
from PySide6.QtWidgets import QWidget, QLineEdit, QToolButton, QHBoxLayout


PAD_X = 24
PAD_TOP = 16
PAD_BOTTOM = 28
PILL_WIDTH = 460
PILL_HEIGHT = 56


class _MicrophoneButton(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('microphoneButton')
        self.setAccessibleName('Start voice listening')
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('QToolButton { border: 0; background: transparent; padding: 5px; } QToolButton:hover { background: rgba(255,255,255,0.10); border-radius: 17px; }')

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor('#c9ccd3'), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawRoundedRect(14, 5, 7, 13, 3.5, 3.5)
        p.drawArc(10, 10, 15, 15, 180 * 16, 180 * 16)
        p.drawLine(17, 25, 17, 29); p.drawLine(13, 29, 21, 29)
        p.end()


class SearchBarPopup(QWidget):
    submitted = Signal(str)
    microphone_requested = Signal()
    dismissed = Signal()

    def __init__(self, hud=None):
        self._offscreen = QGuiApplication.platformName() == 'offscreen'
        flags = Qt.FramelessWindowHint
        if self._offscreen:
            # Avoid offscreen plugin stacking/raise requests for preview and test windows.
            flags |= Qt.Window
        else:
            flags |= Qt.Tool | Qt.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.hud = hud
        self.theme = 'dark'
        self._closing = False
        self.setObjectName('searchBarPopup')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(PAD_X * 2 + PILL_WIDTH, PAD_TOP + PILL_HEIGHT + PAD_BOTTOM)
        self.input = QLineEdit(self)
        self.input.setObjectName('searchInput')
        self.input.installEventFilter(self)
        self.input.setPlaceholderText('Search or Ask')
        self.input.setClearButtonEnabled(False)
        self._base_input_style = 'QLineEdit { border: 0; background: transparent; padding: 0 4px; font-family: Segoe UI; font-size: 15px; font-weight: 500; selection-background-color: #6588ff; } QLineEdit:focus { border-bottom: 1px solid #8da8ff; }'
        self.input.setStyleSheet(self._base_input_style)
        self.microphone = _MicrophoneButton(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(PAD_X + 32, PAD_TOP + 7, PAD_X + 16, PAD_BOTTOM + 7)
        layout.setSpacing(8)
        layout.addWidget(self.input, 1); layout.addWidget(self.microphone)
        self.input.returnPressed.connect(self._submit)
        self.microphone.clicked.connect(self._microphone)
        self._geometry_animation = QPropertyAnimation(self, b'geometry', self)
        self._geometry_animation.setDuration(190)
        self._geometry_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.set_theme('dark')

    def set_theme(self, theme):
        self.theme = theme if theme in ('dark', 'light') else 'dark'
        if self.theme == 'dark':
            self._top, self._bottom, self._rim, self._text = '#181A20', '#2E333E', QColor(255,255,255,41), '#F4F5F7'
        else:
            self._top, self._bottom, self._rim, self._text = '#FFFFFF', '#EEF1F6', QColor(20,21,24,36), '#141518'
        self.input.setStyleSheet(self._base_input_style + f' QLineEdit {{ color: {self._text}; }}')
        self.update()

    def open_below(self, hud=None, immediate=False):
        if hud is not None: self.hud = hud
        owner = self.hud
        if owner:
            center = owner.mapToGlobal(QPoint(owner.width() // 2, 0))
            notch_height = int(round(getattr(getattr(owner, 'height_spring', None), 'value', 48)))
            # Keep the visible pill 14 px below the island; transparent padding
            # belongs outside that visual gap and remains inside this layered window.
            visible_top = center.y() + notch_height + 14
            self.move(center.x() - self.width() // 2, visible_top - PAD_TOP)
        target = self.geometry()
        self._closing = False
        offscreen = self._offscreen
        if offscreen:
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.show()
        if not offscreen:
            self.raise_()
            self.activateWindow()
            self.input.setFocus(Qt.PopupFocusReason)
        self.input.selectAll()
        self._geometry_animation.stop()
        if immediate or offscreen:
            self.setGeometry(target)
        else:
            self._geometry_animation.setStartValue(target.translated(0, -8))
            self._geometry_animation.setEndValue(target)
            self._geometry_animation.start()

    def _submit(self):
        text = self.input.text().strip()
        if text:
            self.submitted.emit(text)
            self.input.clear()
        self.dismiss()

    def _microphone(self):
        self.microphone_requested.emit()
        self.dismiss()

    def dismiss(self):
        if not self.isVisible() or self._closing: return
        self._closing = True
        self.hide(); self._closing = False
        self.dismissed.emit()

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.dismiss(); event.accept(); return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.dismiss(); event.accept(); return
        super().keyPressEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowDeactivate and self.isVisible() and not self._closing:
            self.dismiss()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # Every shadow pixel is painted by this window, clipped to its bounds.
        # No graphics effect may expand the layered-window dirty rectangle.
        p.setClipRect(self.rect())
        pill = QRectF(PAD_X, PAD_TOP, PILL_WIDTH, PILL_HEIGHT)
        for spread in range(14, 0, -1):
            shadow = pill.adjusted(-spread, -spread * 0.65, spread, spread + 5)
            alpha = int(1 + 25 * (1.0 - spread / 15.0) ** 2)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawRoundedRect(shadow, 28 + spread, 28 + spread)

        inner = pill.adjusted(0.5, 0.5, -0.5, -0.5)
        grad = QLinearGradient(0, pill.top(), 0, pill.bottom())
        grad.setColorAt(0.0, QColor(self._top))
        grad.setColorAt(0.58, QColor(self._bottom))
        grad.setColorAt(1.0, QColor(self._bottom))
        p.setBrush(grad)
        p.setPen(QPen(self._rim, 1))
        p.drawRoundedRect(inner, PILL_HEIGHT / 2, PILL_HEIGHT / 2)

        sheen = QLinearGradient(0, pill.top() + 1, 0, pill.top() + 22)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 28 if self.theme == 'dark' else 70))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(sheen)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(pill.adjusted(2, 1, -2, -PILL_HEIGHT + 23), 25, 25)
        p.end()
