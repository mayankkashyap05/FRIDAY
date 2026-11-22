"""Standalone island visual prototype. Not yet connected to FRIDAY's backend."""
import time
from PySide6.QtCore import Qt, QRectF, QTimer, Signal, QPoint
from PySide6.QtGui import QColor, QPainter, QFont, QCursor
from PySide6.QtWidgets import QWidget, QApplication
from .geometry import notch_path
from .physics import Spring
from .fsm import IslandStateMachine
from . import win32_overlay
from ui.mascot.eyes import Blink, gaze, paint_eyes
from ui.themes.tokens import DARK, LIGHT, readable_eye_colour


class VoiceHUD(QWidget):
    files_dropped = Signal(list)
    STATES = ('resting', 'hover', 'listening', 'working', 'speaking', 'dictation', 'write', 'success', 'muted', 'error', 'chat', 'media', 'tasks', 'vision', 'shelf', 'settings', 'face_out')

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.resize(720, 320)
        self.state = 'resting'
        self.theme = 'dark'
        self.audio_level = 0.0
        self.chat_lines = []
        self.preview_mode = False
        self.fsm = IslandStateMachine(on_transition=self._fsm_transition)
        # Hey Nixi interaction requests hover-open; Coucou's preference defaults off.
        self.fsm.open_on_hover = True
        self.width_spring = Spring(240)
        self.height_spring = Spring(48)
        self.shoulder_spring = Spring(1)
        self._blink = Blink()
        self._epoch = self._last = time.monotonic()
        self._drag_start = self._window_start = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(16)
        self._anchor()
        win32_overlay.configure_overlay(self)

    def _fsm_transition(self, old, new):
        if self.state in ('resting', 'hover'):
            self.set_scene('hover' if new == 'home' else 'resting')

    def _anchor(self):
        screen = self.screen() or self.windowHandle().screen()
        if screen:
            g = screen.geometry()
            self.move(g.x() + (g.width()-self.width())//2, g.y())

    def set_scene(self, state, immediate=False):
        if state not in self.STATES:
            raise ValueError(state)
        self.state = state
        sizes = {'resting': (240, 48), 'hover': (640, 160), 'chat': (640, 260), 'media': (640, 220), 'tasks': (640, 220), 'vision': (640, 220), 'shelf': (640, 220), 'settings': (640, 260), 'face_out': (184, 48)}
        w, h = sizes.get(state, (380, 64))
        for spring, target in ((self.width_spring, w), (self.height_spring, h), (self.shoulder_spring, 0 if state == 'face_out' else 1)):
            if immediate: spring.set(target)
            else: spring.target = target
        if state != 'face_out': self._anchor()
        self.update()

    def append_log(self, entry):
        self.chat_lines.append(str(getattr(entry, 'text', entry)))
        del self.chat_lines[:-100]
        self.update()

    def set_theme(self, theme):
        if theme not in ('dark', 'light'): raise ValueError(theme)
        self.theme = theme; self.update()

    def current_path(self):
        w = self.width_spring.value; h = self.height_spring.value
        y = 18 * (1 - max(0, min(1, self.shoulder_spring.value)))
        return notch_path(QRectF((self.width()-w)/2, y, w, h), 16, min(18, h/2), self.shoulder_spring.value)

    def _animate(self):
        now = time.monotonic(); dt = now - self._last; self._last = now
        self.fsm.tick()
        for spring in (self.width_spring, self.height_spring, self.shoulder_spring):
            if not spring.settled: spring.step(dt)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        pal = DARK if self.theme == 'dark' else LIGHT
        p.setPen(Qt.NoPen); p.setBrush(QColor(pal['background'])); p.drawPath(self.current_path())
        w, h = self.width_spring.value, self.height_spring.value
        left = (self.width()-w)/2
        expanded = self.state in ('hover', 'chat', 'media', 'tasks', 'vision', 'shelf', 'settings') and w > 480
        cx = left+74 if expanded else (self.width()/2 if self.state in ('resting', 'face_out') else left+55)
        cy = (min(h/2, 76) if expanded else h/2) + 18 * (1 - max(0, min(1, self.shoulder_spring.value)))
        delta = QCursor.pos() - self.mapToGlobal(QPoint(int(cx), int(cy)))
        look = (0, 0) if self.preview_mode else gaze(delta.x(), delta.y())
        blink = 1 if self.preview_mode else self._blink.value(time.monotonic()-self._epoch)
        paint_eyes(p, cx, cy, colour=readable_eye_colour(pal['ink'], pal['background']), scale=1.0 if not expanded else 1.3, blink=blink, look=look, halo=self.state in ('listening', 'dictation'), dim=self.state == 'muted')
        p.setPen(QColor(pal['ink'])); p.setFont(QFont('Segoe UI', 10))
        if expanded:
            self._paint_expanded(p, left, w, h, pal)
        elif self.state not in ('resting', 'face_out'):
            labels = {'listening':'Listening', 'working':'Working  • • •', 'speaking':'Speaking  ▮ ▮ ▮', 'dictation':'Dictation  • • •', 'write':'Write a command', 'success':'✓  Done', 'muted':'Mic muted', 'error':'!  Something went wrong'}
            p.drawText(QRectF(left+95, 0, w-160, h), Qt.AlignVCenter, labels.get(self.state, self.state.title()))
            if self.state in ('listening', 'speaking', 'dictation'):
                self._paint_waveform(p, left+w-48, h/2, pal)
        p.end()

    def _paint_waveform(self, p, x, cy, pal):
        # The signal comes from the backend bridge; no fabricated voice input.
        level = max(0.0, min(1.0, self.audio_level))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal['accent']))
        for i, factor in enumerate((.45, .8, 1, .7, .4)):
            height = 3 + level * factor * 20
            p.drawRoundedRect(QRectF(x + i*6, cy-height/2, 3, height), 1.5, 1.5)

    def _paint_expanded(self, p, left, w, h, pal):
        p.setFont(QFont('Segoe UI', 9, QFont.DemiBold)); p.setPen(QColor(pal['muted']))
        p.drawText(QRectF(left+145, 15, w-165, 22), Qt.AlignVCenter, 'FRIDAY  /  ' + self.state.upper())
        p.setFont(QFont('Segoe UI', 10))
        if self.state == 'hover':
            for i, label in enumerate(('Command', 'Dictate', 'Write')):
                rect = QRectF(left+145+i*145, 52, 134, 64)
                p.setPen(Qt.NoPen); p.setBrush(QColor(pal['card'])); p.drawRoundedRect(rect, 14, 14)
                p.setPen(QColor(pal['ink'])); p.drawText(rect, Qt.AlignCenter, label)
            p.setPen(QColor(pal['muted']))
            for i, label in enumerate(('Chat', 'Media', 'Tasks', 'Vision', 'Shelf', 'Settings')):
                p.drawText(QRectF(left+145+i*70, 125, 70, 22), Qt.AlignVCenter, label)
        else:
            p.setPen(Qt.NoPen); p.setBrush(QColor(pal['surface']))
            p.drawRoundedRect(QRectF(left+145, 48, w-165, max(30, h-65)), 14, 14)
            p.setPen(QColor(pal['muted']))
            if self.state == 'chat' and self.chat_lines:
                text = '\n'.join(self.chat_lines[-6:])
            else:
                text = 'Prototype only — this panel is not connected to the FRIDAY backend.'
            p.drawText(QRectF(left+160, 55, w-195, h-80), Qt.AlignTop | Qt.TextWordWrap, text)

    def enterEvent(self, event):
        if self.state in ('resting', 'hover'): self.fsm.mouse_entered()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.state in ('resting', 'hover'): self.fsm.mouse_left()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start = event.globalPosition().toPoint(); self._window_start = self.pos(); self._dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            delta = event.globalPosition().toPoint()-self._drag_start
            if delta.manhattanLength() > 3:
                self._dragged = True
                if self.state != 'face_out': self.set_scene('face_out')
                self.move(self._window_start + delta)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_start is not None and self.state == 'face_out':
            pointer = event.globalPosition().toPoint()
            screen = QApplication.screenAt(pointer)
            if screen is not None:
                bounds = screen.geometry()
                if abs(pointer.x() - bounds.center().x()) <= 80 and 0 <= pointer.y() - bounds.top() <= 80:
                    self.set_scene('resting')
        elif self._drag_start is not None and not self._dragged and self.state == 'hover':
            point = event.position()
            left = (self.width()-self.width_spring.value)/2
            if 125 <= point.y() <= 147:
                index = int((point.x() - left - 145) // 70)
                if 0 <= index < 6 and point.x() >= left+145:
                    self.fsm.user_interacted()
                    self.set_scene(('chat', 'media', 'tasks', 'vision', 'shelf', 'settings')[index])
        self._drag_start = self._window_start = None
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()): event.acceptProposedAction()

    def dropEvent(self, event):
        files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if files: self.files_dropped.emit(files); event.acceptProposedAction()

    def nativeEvent(self, event_type, message):
        result = win32_overlay.hit_test(self, message)
        if result is not None: return True, result
        return super().nativeEvent(event_type, message)
