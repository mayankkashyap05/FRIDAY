"""Translucent, top-docked FRIDAY island."""
import time
from PySide6.QtCore import Qt, QRectF, QTimer, Signal, QPoint, QEvent
from PySide6.QtGui import QColor, QPainter, QFont, QCursor
from PySide6.QtWidgets import QWidget, QApplication, QStackedWidget, QScrollArea, QLineEdit, QDateTimeEdit, QComboBox
from .geometry import notch_path
from .physics import Spring
from .fsm import IslandStateMachine
from . import win32_overlay
from ui.mascot.eyes import Blink, gaze, paint_eyes
from ui.themes.tokens import DARK, LIGHT, readable_eye_colour


class VoiceHUD(QWidget):
    files_dropped = Signal(list)
    command_requested = Signal(str)
    mode_requested = Signal(str)
    face_out_moved = Signal(str, int, int)
    STATES = ('resting', 'hover', 'listening', 'working', 'speaking', 'dictation', 'write', 'success', 'muted', 'error', 'chat', 'media', 'tasks', 'vision', 'shelf', 'settings', 'face_out')

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.resize(720, 320)
        self.state = 'resting'
        self._activity_scene = 'resting'
        self.live_transcript = ''
        self.theme = 'dark'
        self.audio_level = 0.0
        self.notch_colour = '#000000'
        self.eye_colour = '#ffffff'
        self.animations_enabled = True
        self.hide_on_fullscreen = True
        self._user_visible = True
        self._auto_hidden = False
        self.face_out_positions = {}
        self.chat_lines = []
        self.panels = {}
        self._panel_stack = None
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
        self._fullscreen_timer = QTimer(self)
        self._fullscreen_timer.timeout.connect(self._check_fullscreen)
        if win32_overlay.IS_WINDOWS: self._fullscreen_timer.start(1000)
        self._anchor()
        win32_overlay.configure_overlay(self)

    def closeEvent(self, event):
        self._timer.stop()
        self._fullscreen_timer.stop()
        super().closeEvent(event)

    def set_user_visible(self, visible):
        self._user_visible = bool(visible)
        if visible:
            self.show()
        else:
            self._auto_hidden = False
            self.hide()

    def _check_fullscreen(self):
        fullscreen = self.hide_on_fullscreen and win32_overlay.foreground_is_fullscreen(self)
        if fullscreen and self._user_visible and self.isVisible():
            self._auto_hidden = True; self.hide()
        elif not fullscreen and self._auto_hidden:
            self._auto_hidden = False
            if self._user_visible: self.show()

    def _fsm_transition(self, old, new):
        if self.state == 'hover' or self.state in ('resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'):
            self.set_scene('hover' if new == 'home' else self._activity_scene)

    def _anchor(self):
        screen = self.screen() or self.windowHandle().screen()
        if screen:
            g = screen.geometry()
            self.move(g.x() + (g.width()-self.width())//2, g.y())

    def set_scene(self, state, immediate=False):
        if state not in self.STATES:
            raise ValueError(state)
        if not self.animations_enabled: immediate = True
        if state == 'face_out' and self.state != 'face_out' and self._drag_start is None:
            screen = self.screen()
            if screen and screen.name() in self.face_out_positions:
                from .geometry import clamp_to_screen
                pos = self.face_out_positions[screen.name()]
                bounds = QRectF(screen.availableGeometry())
                rect = clamp_to_screen(QRectF(pos[0], pos[1], self.width(), self.height()), bounds)
                self.move(int(rect.x()), int(rect.y()))
        self.state = state
        if state in ('resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'):
            self._activity_scene = state
        sizes = {'resting': (240, 48), 'hover': (640, 160), 'chat': (640, 260), 'media': (640, 220), 'tasks': (640, 220), 'vision': (640, 220), 'shelf': (640, 220), 'settings': (640, 260), 'write': (640, 200), 'face_out': (184, 48)}
        w, h = sizes.get(state, (380, 64))
        for spring, target in ((self.width_spring, w), (self.height_spring, h), (self.shoulder_spring, 0 if state == 'face_out' else 1)):
            if immediate: spring.set(target)
            else: spring.target = target
        if state != 'face_out': self._anchor()
        self._sync_panels()
        if state == 'tasks' and 'tasks' in self.panels: self.panels['tasks'].refresh()
        self.update()

    def attach_panels(self, services, store):
        from ui.panels.command_bar import CommandBar
        from ui.panels.chat_panel import ChatPanel
        from ui.panels.media_panel import MediaPanel
        from ui.panels.tasks_panel import TasksPanel
        from ui.panels.vision_panel import VisionPanel
        from ui.panels.shelf_panel import ShelfPanel
        from ui.panels.settings_panel import SettingsPanel
        if self._panel_stack is not None:
            return
        self._panel_stack = QStackedWidget(self)
        self._panel_stack.setStyleSheet('QStackedWidget { background: transparent; }')
        constructors = {'write': lambda: CommandBar(services), 'chat': lambda: ChatPanel(services),
                        'media': lambda: MediaPanel(services), 'tasks': lambda: TasksPanel(services),
                        'vision': lambda: VisionPanel(services), 'shelf': lambda: ShelfPanel(services),
                        'settings': lambda: SettingsPanel(services, store)}
        self._pages = {}
        for name, constructor in constructors.items():
            panel = constructor(); self.panels[name] = panel
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(panel)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._panel_stack.addWidget(scroll); self._pages[name] = scroll
            for child in panel.findChildren(QWidget):
                if not isinstance(child, (QLineEdit, QDateTimeEdit, QComboBox)): continue
                child.installEventFilter(self)
        self.panels['write'].submitted.connect(self.command_requested)
        self.panels['write'].mode_requested.connect(self.mode_requested)
        self.panels['chat'].submitted.connect(self.command_requested)
        self.files_dropped.connect(self.panels['shelf'].add_files)
        self._sync_panels()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress and isinstance(obj, (QLineEdit, QDateTimeEdit, QComboBox)):
            win32_overlay.set_accepts_focus(self, True)
            self.setAttribute(Qt.WA_ShowWithoutActivating, False)
            self.activateWindow()
        elif event.type() == QEvent.FocusOut and isinstance(obj, (QLineEdit, QDateTimeEdit, QComboBox)):
            win32_overlay.set_accepts_focus(self, False)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        return super().eventFilter(obj, event)

    def _sync_panels(self):
        if self._panel_stack is None:
            return
        page = self._pages.get(self.state)
        width, height = self.width_spring.value, self.height_spring.value
        if page is None or width < 480 or height < 100:
            self._panel_stack.hide(); return
        self._panel_stack.setCurrentWidget(page)
        left = (self.width()-width)/2
        self._panel_stack.setGeometry(int(left+145), 43, int(width-160), int(height-55))
        self._panel_stack.show()

    def append_log(self, entry):
        self.chat_lines.append(str(getattr(entry, 'text', entry)))
        del self.chat_lines[:-100]
        if 'chat' in self.panels: self.panels['chat'].append_log(entry)
        self.update()

    def set_theme(self, theme):
        if theme not in ('dark', 'light'): raise ValueError(theme)
        from pathlib import Path
        self.theme = theme
        self.setStyleSheet((Path(__file__).resolve().parent.parent / 'themes' / f'{theme}.qss').read_text(encoding='utf-8'))
        self.update()

    def current_path(self):
        w = self.width_spring.value; h = self.height_spring.value
        y = 18 * (1 - max(0, min(1, self.shoulder_spring.value)))
        return notch_path(QRectF((self.width()-w)/2, y, w, h), 16, min(18, h/2), self.shoulder_spring.value)

    def _animate(self):
        now = time.monotonic(); dt = now - self._last; self._last = now
        self.fsm.tick()
        for spring in (self.width_spring, self.height_spring, self.shoulder_spring):
            if not spring.settled: spring.step(dt)
        self._sync_panels()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.Antialiasing)
        pal = DARK if self.theme == 'dark' else LIGHT
        background = pal['background'] if self.theme == 'light' and self.notch_colour == '#000000' else self.notch_colour
        p.setPen(Qt.NoPen); p.setBrush(QColor(background)); p.drawPath(self.current_path())
        w, h = self.width_spring.value, self.height_spring.value
        left = (self.width()-w)/2
        expanded = self.state in ('hover', 'chat', 'media', 'tasks', 'vision', 'shelf', 'settings', 'write') and w > 480
        cx = left+74 if expanded else (self.width()/2 if self.state in ('resting', 'face_out') else left+55)
        cy = (min(h/2, 76) if expanded else h/2) + 18 * (1 - max(0, min(1, self.shoulder_spring.value)))
        delta = QCursor.pos() - self.mapToGlobal(QPoint(int(cx), int(cy)))
        look = (0, 0) if self.preview_mode else gaze(delta.x(), delta.y())
        blink = 1 if self.preview_mode else self._blink.value(time.monotonic()-self._epoch)
        paint_eyes(p, cx, cy, colour=readable_eye_colour(self.eye_colour, background), scale=1.0 if not expanded else 1.3, blink=blink, look=look, halo=self.state in ('resting', 'listening', 'dictation'), dim=self.state == 'muted')
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
        p.drawText(QRectF(left+145, 15, w-165, 22), Qt.AlignVCenter, ('FRIDAY  /  ' if self.state == 'hover' else '‹  FRIDAY  /  ') + self.state.upper())
        p.setFont(QFont('Segoe UI', 10))
        if self.state == 'hover':
            for rect, label in zip(self._primary_rects(left), ('Command', 'Dictate', 'Write')):
                p.setPen(Qt.NoPen); p.setBrush(QColor(pal['card'])); p.drawRoundedRect(rect, 14, 14)
                p.setPen(QColor(pal['ink'])); p.drawText(rect, Qt.AlignCenter, label)
            p.setPen(QColor(pal['muted']))
            for rect, label in zip(self._tab_rects(left), ('Chat', 'Media', 'Tasks', 'Vision', 'Shelf', 'Settings')):
                p.drawText(rect, Qt.AlignCenter, label)
        else:
            p.setPen(Qt.NoPen); p.setBrush(QColor(pal['surface']))
            p.drawRoundedRect(QRectF(left+145, 42, w-160, max(30, h-55)), 14, 14)

    @staticmethod
    def _primary_rects(left):
        return [QRectF(left+145+i*145, 52, 134, 64) for i in range(3)]

    @staticmethod
    def _tab_rects(left):
        return [QRectF(left+145+i*70, 125, 70, 22) for i in range(6)]

    def enterEvent(self, event):
        if self.state == 'hover' or self.state in ('resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'): self.fsm.mouse_entered()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.state == 'hover' or self.state in ('resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'): self.fsm.mouse_left()
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
        elif self._drag_start is not None and not self._dragged and self.state in self.panels and event.position().y() < 42:
            self.set_scene('hover')
            self.fsm.force_home()
        elif self._drag_start is not None and not self._dragged and self.state in ('resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'):
            self.fsm.mouse_entered()
            self.fsm.click()
        elif self._drag_start is not None and not self._dragged and self.state == 'hover':
            point = event.position()
            left = (self.width()-self.width_spring.value)/2
            for index, rect in enumerate(self._primary_rects(left)):
                if rect.contains(point):
                    target = ('listening', 'dictation', 'write')[index]
                    self.mode_requested.emit(target)
                    self.set_scene(target)
                    break
            else:
                for index, rect in enumerate(self._tab_rects(left)):
                    if rect.contains(point):
                        self.fsm.user_interacted()
                        self.set_scene(('chat', 'media', 'tasks', 'vision', 'shelf', 'settings')[index])
                        break
        if self.state == 'face_out' and self._drag_start is not None and self._dragged:
            screen = self.screen()
            if screen: self.face_out_moved.emit(screen.name(), self.x(), self.y())
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
