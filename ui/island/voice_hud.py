"""Translucent, top-docked FRIDAY island."""
import time
import math
from PySide6.QtCore import Qt, QRectF, QTimer, Signal, QPoint, QPointF, QEvent
from PySide6.QtGui import QColor, QPainter, QFont, QCursor, QPen, QLinearGradient, QPainterPath
from PySide6.QtWidgets import QWidget, QApplication, QStackedWidget, QScrollArea, QLineEdit, QDateTimeEdit, QComboBox, QGraphicsOpacityEffect
from .geometry import notch_path
from .physics import Spring, Tracked
from .fsm import IslandStateMachine
from . import win32_overlay
from ui.mascot.eyes import Blink, gaze, paint_eyes
from ui.mascot.moods import PokeMood
from ui.mascot.waveform import Waveform
from ui.themes.tokens import DARK, LIGHT, readable_eye_colour


class VoiceHUD(QWidget):
    files_dropped = Signal(list)
    command_requested = Signal(str)
    mode_requested = Signal(str)
    face_out_moved = Signal(str, int, int)
    theme_changed = Signal(str)
    STATES = ('resting', 'hover', 'listening', 'working', 'speaking', 'dictation', 'write', 'success', 'muted', 'error', 'chat', 'media', 'tasks', 'vision', 'shelf', 'settings', 'face_out')

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
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
        self.width_spring = Tracked(240)
        self.height_spring = Tracked(48)
        self.shoulder_spring = Spring(1, .42, .74)
        self.eye_cx_spring = Spring(360, .42, .74)
        self.eye_cy_spring = Spring(24, .42, .74)
        self.eye_scale_spring = Spring(1, .42, .74)
        self.eye_spacing_spring = Spring(13, .42, .74)
        self.content_alpha_spring = Spring(0, .22, .9)
        self.gaze_x_spring = Spring(0, .22, .88)
        self.gaze_y_spring = Spring(0, .22, .88)
        self.tile_hover_springs = [Spring(0, .22, .9) for _ in range(3)]
        self.tile_press_springs = [Spring(0, .18, .82) for _ in range(3)]
        self.halo_spring = Spring(0, .22, .88)
        self.audio_smooth_spring = Spring(0, .22, .88)
        self.boop_scale_spring = Spring(1, .22, .65)
        self.shake_spring = Spring(0, .25, .7)
        self.tab_indicator_spring = Spring(0, .3, .8)
        self.window_x_spring = Spring(0, .42, .92)
        self.window_y_spring = Spring(0, .42, .92)
        self._window_gliding = False
        self._dock_preview = False
        self.waveform = Waveform()
        self.poke_mood = PokeMood()
        self._hover_tile = self._hover_tab = self._pressed_tile = self._pressed_tab = -1
        self._active_tab = -1
        self._drop_active = False
        self._hover_back = False
        self._scene_started = time.monotonic()
        self._last_cursor = QCursor.pos()
        self._cursor_move_time = time.monotonic()
        self._blink = Blink()
        self._epoch = self._last = time.monotonic()
        self._drag_start = self._window_start = None
        self._drag_velocity = (0.0, 0.0)
        self._last_drag_point = None
        self._last_drag_time = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(16)
        self._fullscreen_timer = QTimer(self)
        self._fullscreen_timer.timeout.connect(self._check_fullscreen)
        if win32_overlay.IS_WINDOWS: self._fullscreen_timer.start(1000)
        self._anchor()
        win32_overlay.configure_overlay(self)

    def shutdown(self):
        self._timer.stop()
        self._fullscreen_timer.stop()

    def closeEvent(self, event):
        self.shutdown()
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
        if self.state != state: self._scene_started = time.monotonic()
        self.state = state
        tabs = ('chat', 'media', 'tasks', 'vision', 'shelf', 'settings')
        if state in tabs:
            self._active_tab = tabs.index(state)
            if immediate: self.tab_indicator_spring.set(self._active_tab)
            else: self.tab_indicator_spring.target = self._active_tab
        if state in ('resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'):
            self._activity_scene = state
        sizes = {'resting': (240, 48), 'hover': (640, 160), 'chat': (640, 260), 'media': (640, 220), 'tasks': (640, 220), 'vision': (640, 220), 'shelf': (640, 220), 'settings': (640, 260), 'write': (640, 200), 'face_out': (184, 96)}
        w, h = sizes.get(state, (380, 64))
        for spring, target in ((self.width_spring, w), (self.height_spring, h)):
            if immediate: spring.set(target)
            else: spring.animate_to(target)
        eye_x = (self.width()-w)/2+74 if state in ('hover', 'chat', 'media', 'tasks', 'vision', 'shelf', 'settings', 'write') else (self.width()/2 if state in ('resting', 'face_out') else (self.width()-w)/2+55)
        eye_y = (min(h/2, 76) if state in ('hover', 'chat', 'media', 'tasks', 'vision', 'shelf', 'settings', 'write') else h/2) + (18 if state == 'face_out' else 0)
        for spring, target in ((self.shoulder_spring, 0 if state == 'face_out' else 1),
                               (self.eye_cx_spring, eye_x), (self.eye_cy_spring, eye_y),
                               (self.eye_scale_spring, 2 if state == 'face_out' else (1.3 if w > 480 else 1)),
                               (self.eye_spacing_spring, 14 if state == 'face_out' else 13)):
            if immediate: spring.set(target)
            else: spring.target = target
        if immediate:
            self.content_alpha_spring.set(1 if w > 480 else 0)
        elif w <= 480:
            self.content_alpha_spring.target = 0
        if state == 'error':
            self.shake_spring.value = 4
            self.shake_spring.target = 0
        if state != 'face_out' and not self._window_gliding: self._anchor()
        self._wake_animation()
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
        self._panel_opacity = QGraphicsOpacityEffect(self._panel_stack)
        self._panel_stack.setGraphicsEffect(self._panel_opacity)
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
        alpha = max(0.0, min(1.0, self.content_alpha_spring.value))
        if page is None or width < 480 or height < 100 or alpha <= .15:
            self._panel_stack.hide(); return
        self._panel_opacity.setOpacity(alpha)
        self._panel_stack.setCurrentWidget(page)
        left = (self.width()-width)/2
        self._panel_stack.setGeometry(int(left+145), 43, max(0, int(width-166)), max(0, int(height-64)))
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
        self.theme_changed.emit(theme)
        self.update()

    def current_path(self):
        w = self.width_spring.value; h = self.height_spring.value
        y = 18 * (1 - max(0, min(1, self.shoulder_spring.value)))
        shoulder = max(0, min(1, self.shoulder_spring.value))
        corner = min(18 * shoulder + h/2 * (1-shoulder), h/2)
        return notch_path(QRectF((self.width()-w)/2, y-shoulder, w, h+shoulder), 16, corner, shoulder)

    def _wake_animation(self):
        if self._timer.isActive() and self._timer.interval() != 16:
            self._last = time.monotonic()
            self._timer.setInterval(16)

    def _animate(self):
        now = time.monotonic(); dt = min(.05, now - self._last); self._last = now
        self.fsm.tick()
        cursor = QCursor.pos()
        cursor_moved = cursor != self._last_cursor
        self._last_cursor = cursor
        if cursor_moved: self._cursor_move_time = now
        delta = cursor - self.mapToGlobal(QPoint(int(self.eye_cx_spring.value), int(self.eye_cy_spring.value)))
        gx, gy = gaze(delta.x(), delta.y()) if not self.preview_mode and now-self._cursor_move_time < 3 else (0, 0)
        self.gaze_x_spring.target, self.gaze_y_spring.target = gx, gy
        expanded = self.state in ('hover', 'chat', 'media', 'tasks', 'vision', 'shelf', 'settings', 'write')
        if expanded and self.width_spring.value >= self.width_spring.target * .75:
            self.content_alpha_spring.target = 1
        elif not expanded:
            self.content_alpha_spring.target = 0
        self.audio_smooth_spring.target = max(0, min(1, self.audio_level))
        self.halo_spring.target = self.audio_smooth_spring.target if self.state in ('listening', 'speaking', 'dictation') else 0
        springs = (self.width_spring, self.height_spring, self.shoulder_spring,
                   self.eye_cx_spring, self.eye_cy_spring, self.eye_scale_spring,
                   self.eye_spacing_spring, self.content_alpha_spring,
                   self.gaze_x_spring, self.gaze_y_spring, self.halo_spring,
                   self.audio_smooth_spring, self.boop_scale_spring, self.shake_spring,
                   self.tab_indicator_spring, self.window_x_spring, self.window_y_spring,
                   *self.tile_hover_springs, *self.tile_press_springs)
        moving = False
        for spring in springs:
            if not spring.settled:
                spring.step(dt); moving = True
        if self._window_gliding:
            self.move(round(self.window_x_spring.value), round(self.window_y_spring.value))
            if self.window_x_spring.settled and self.window_y_spring.settled:
                self._window_gliding = False
                if self.state == 'face_out':
                    screen = self.screen()
                    if screen: self.face_out_moved.emit(screen.name(), self.x(), self.y())
        self.waveform.step(self.audio_smooth_spring.value, dt, now - self._epoch)
        blink_before = self._blink.started is not None
        self._blink_value = 1 if self.preview_mode else self._blink.value(now-self._epoch)
        blinking = blink_before or self._blink.started is not None
        active = self.state not in ('resting', 'face_out') or self._drag_start is not None or self.poke_mood.is_dizzy(now-self._epoch)
        self._sync_panels()
        if moving or cursor_moved or blinking or active:
            self.update()
        interval = 16 if moving or cursor_moved or blinking or active else 150
        if self._timer.interval() != interval:
            self._timer.setInterval(interval)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.Antialiasing)
        pal = DARK if self.theme == 'dark' else LIGHT
        background = pal['background'] if self.theme == 'light' and self.notch_colour == '#000000' else self.notch_colour
        path = self.current_path()
        bounds = path.boundingRect()
        p.setPen(Qt.NoPen)
        if self.state == 'face_out' and self.theme == 'dark' and self.notch_colour == '#000000':
            gradient = QLinearGradient(0, bounds.top(), 0, bounds.bottom())
            gradient.setColorAt(0, QColor('#18181D')); gradient.setColorAt(1, QColor('#060608'))
            p.setBrush(gradient)
        else:
            p.setBrush(QColor(background))
        p.drawPath(path)
        p.save(); p.setClipPath(path)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 36 if self.state == 'face_out' else 20), 1.5))
        p.drawPath(path)
        w, h = self.width_spring.value, self.height_spring.value
        left = (self.width()-w)/2
        expanded = self.state in ('hover', 'chat', 'media', 'tasks', 'vision', 'shelf', 'settings', 'write') and w > 480
        cx, cy = self.eye_cx_spring.value + self.shake_spring.value, self.eye_cy_spring.value
        elapsed = time.monotonic()-self._epoch
        look = (0, 0) if self.preview_mode else (self.gaze_x_spring.value, self.gaze_y_spring.value)
        if self.state == 'working': look = (math.sin(elapsed * 1.7) * .45, look[1])
        if self.poke_mood.is_dizzy(elapsed): look = (math.sin(elapsed * 10) * .8, math.cos(elapsed * 10) * .5)
        blink = 1 if self.preview_mode else getattr(self, '_blink_value', 1)
        if self.state in ('working', 'error'): blink *= .8
        scale = self.eye_scale_spring.value * self.boop_scale_spring.value
        if self.state == 'speaking': scale *= 1 + .06 * self.audio_smooth_spring.value
        if not self.preview_mode and self._timer.interval() == 16:
            cy += .4 * math.sin(elapsed * math.pi / 2)
        paint_eyes(p, cx, cy, colour=readable_eye_colour(self.eye_colour, background),
                   scale=scale, spacing=self.eye_spacing_spring.value, blink=blink, look=look,
                   halo=self.state in ('resting', 'listening', 'dictation', 'face_out'),
                   halo_strength=(1 + .4 * math.sin(elapsed * 2) + self.halo_spring.value) if self.state in ('listening', 'dictation') else 1,
                   halo_colour=pal['accent'] if self.state in ('listening', 'dictation') else '#ffffff',
                   dim=self.state == 'muted',
                   happy=self.state == 'success' and time.monotonic()-self._scene_started < .8)
        p.setPen(QColor(pal['ink'])); p.setFont(QFont('Segoe UI', 10))
        if expanded:
            p.save(); p.setOpacity(max(0.0, min(1.0, self.content_alpha_spring.value)))
            self._paint_expanded(p, left, w, h, pal); p.restore()
        elif self.state not in ('resting', 'face_out'):
            labels = {'listening':'Listening', 'working':'Working', 'speaking':'Speaking', 'dictation':'Dictation', 'write':'Write a command', 'success':'Done', 'muted':'Mic muted', 'error':'Something went wrong'}
            p.drawText(QRectF(left+95, 0, w-160, h), Qt.AlignVCenter, labels.get(self.state, self.state.title()))
            if self.state in ('listening', 'speaking', 'dictation'):
                self._paint_waveform(p, left+w-48, h/2, pal)
                if self.state == 'dictation':
                    p.setPen(Qt.NoPen); p.setBrush(QColor(pal['accent']))
                    for i, value in enumerate(self.waveform.values):
                        size = 3 + 3 * min(1, value)
                        p.drawEllipse(QRectF(left+190+i*9, h/2-size/2, size, size))
            elif self.state == 'working':
                p.setPen(Qt.NoPen)
                for i in range(5):
                    alpha = int(80 + 175 * (.5 + .5 * math.sin(elapsed * 4 - i * .6)))
                    p.setBrush(QColor(255, 255, 255, alpha)); p.drawEllipse(QRectF(left+w-58+i*8, h/2-2, 4, 4))
            elif self.state in ('success', 'error', 'muted'):
                colour = '#34C759' if self.state == 'success' else '#FF3B30'
                p.setPen(QPen(QColor(colour), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                x, y = left+w-36, h/2
                if self.state == 'success':
                    p.drawLine(QPointF(x-7,y), QPointF(x-2,y+5)); p.drawLine(QPointF(x-2,y+5), QPointF(x+7,y-6))
                elif self.state == 'error':
                    p.drawLine(QPointF(x,y-7), QPointF(x,y+2)); p.drawPoint(QPointF(x,y+7))
                else:
                    p.drawRoundedRect(QRectF(x-4,y-8,8,13), 4, 4); p.drawLine(QPointF(x-8,y+8), QPointF(x+8,y-8))
        if self._dock_preview:
            p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor(56, 130, 246, 150), 2))
            p.drawPath(path)
        if self._drop_active:
            p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor('#3882F6'), 2, Qt.DashLine))
            p.drawRoundedRect(QRectF(left+8, 5, w-16, h-12), 12, 12)
            p.setPen(QColor('#F5F6F8')); p.drawText(QRectF(left+100, 0, w-110, h), Qt.AlignCenter, 'Drop files to FRIDAY Shelf')
        p.restore(); p.end()

    def _paint_waveform(self, p, x, cy, pal):
        p.setPen(Qt.NoPen); p.setBrush(QColor(pal['accent']))
        levels = self.waveform.values
        if self.preview_mode:
            levels = [self.audio_level * v for v in (.45, .8, 1, .7, .4)]
        for i, level in enumerate(levels):
            height = 3 + min(1, level) * 20
            p.drawRoundedRect(QRectF(x + i*6, cy-height/2, 3, height), 1.5, 1.5)

    def _paint_expanded(self, p, left, w, h, pal):
        if self.state != 'hover' and self._hover_back:
            p.setPen(Qt.NoPen); p.setBrush(QColor(255, 255, 255, 22))
            p.drawRoundedRect(QRectF(left+140, 12, 83, 27), 12, 12)
        p.setFont(QFont('Segoe UI', 9, QFont.DemiBold)); p.setPen(QColor(pal['muted']))
        p.drawText(QRectF(left+145, 15, w-165, 22), Qt.AlignVCenter, ('FRIDAY  /  ' if self.state == 'hover' else '‹  FRIDAY  /  ') + self.state.upper())
        p.setFont(QFont('Segoe UI', 10))
        if self.state == 'hover':
            for i, (rect, label) in enumerate(zip(self._primary_rects(left), ('Command', 'Dictate', 'Write'))):
                hover = max(0, min(1, self.tile_hover_springs[i].value))
                press = max(0, min(1, self.tile_press_springs[i].value))
                p.save(); p.translate(rect.center()); p.scale(1-.04*press, 1-.04*press); p.translate(-rect.center())
                p.setPen(QPen(QColor(255, 255, 255, int(20+26*hover+56*press)), 1))
                p.setBrush(QColor(38+int(12*hover), 39+int(12*hover), 43+int(12*hover)))
                p.drawRoundedRect(rect.adjusted(.5, .5, -.5, -.5), 14, 14)
                self._paint_tile_icon(p, rect.center().x(), rect.top()+19, i, pal['ink'])
                p.setPen(QColor(pal['ink'])); p.drawText(QRectF(rect.left(), rect.top()+34, rect.width(), 25), Qt.AlignCenter, label)
                p.restore()
            if self._active_tab >= 0:
                indicator = QRectF(left+145+self.tab_indicator_spring.value*70, 125, 70, 22)
                p.setPen(Qt.NoPen); p.setBrush(QColor(255, 255, 255, 26)); p.drawRoundedRect(indicator, 10, 10)
            for i, (rect, label) in enumerate(zip(self._tab_rects(left), ('Chat', 'Media', 'Tasks', 'Vision', 'Shelf', 'Settings'))):
                if i == self._hover_tab:
                    p.setPen(Qt.NoPen); p.setBrush(QColor(255, 255, 255, 20)); p.drawRoundedRect(rect, 10, 10)
                p.setPen(QColor(pal['muted'])); p.drawText(rect, Qt.AlignCenter, label)
        else:
            p.setPen(Qt.NoPen); p.setBrush(QColor(pal['surface']))
            p.drawRoundedRect(QRectF(left+145, 42, w-160, max(30, h-55)), 14, 14)

    @staticmethod
    def _paint_tile_icon(p, x, y, index, colour):
        p.save(); p.translate(x, y)
        p.setPen(QPen(QColor(colour), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)); p.setBrush(Qt.NoBrush)
        if index == 0:
            path = QPainterPath(QPointF(-6, -7)); path.lineTo(-6, 6); path.lineTo(-2, 3); path.lineTo(1, 8); path.lineTo(3, 7); path.lineTo(0, 2); path.lineTo(5, 1); path.closeSubpath(); p.drawPath(path)
        elif index == 1:
            p.drawRoundedRect(QRectF(-3, -8, 6, 11), 3, 3)
            p.drawArc(QRectF(-6, -5, 12, 12), 180*16, 180*16)
            p.drawLine(QPointF(0, 7), QPointF(0, 10)); p.drawLine(QPointF(-3, 10), QPointF(3, 10))
        else:
            p.drawLine(QPointF(-5, 6), QPointF(6, -5)); p.drawLine(QPointF(-3, 8), QPointF(8, -3))
            p.drawLine(QPointF(-5, 6), QPointF(-6, 9)); p.drawLine(QPointF(-6, 9), QPointF(-3, 8)); p.drawLine(QPointF(6, -5), QPointF(8, -3))
        p.restore()

    @staticmethod
    def _primary_rects(left):
        return [QRectF(left+145+i*145, 52, 134, 64) for i in range(3)]

    @staticmethod
    def _tab_rects(left):
        return [QRectF(left+145+i*70, 125, 70, 22) for i in range(6)]

    def enterEvent(self, event):
        self._wake_animation()
        if self.state == 'hover' or self.state in ('resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'): self.fsm.mouse_entered()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover_tile = self._hover_tab = -1
        self._hover_back = False
        for spring in self.tile_hover_springs: spring.target = 0
        self._wake_animation()
        if self.state == 'hover' or self.state in ('resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'): self.fsm.mouse_left()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._window_gliding = False
            self.window_x_spring.set(self.x()); self.window_y_spring.set(self.y())
            self._drag_start = event.globalPosition().toPoint(); self._window_start = self.pos(); self._dragged = False
            self._last_drag_point = self._drag_start; self._last_drag_time = time.monotonic()
            self._drag_velocity = (0.0, 0.0)
            self._pressed_tile = self._hit_primary(event.position()) if self.state == 'hover' else -1
            left = (self.width()-self.width_spring.value)/2
            self._pressed_tab = next((i for i, rect in enumerate(self._tab_rects(left)) if rect.contains(event.position())), -1) if self.state == 'hover' else -1
            if self._pressed_tile >= 0:
                self.tile_press_springs[self._pressed_tile].target = 1
                self._wake_animation()
        super().mousePressEvent(event)

    def _hit_primary(self, point):
        left = (self.width()-self.width_spring.value)/2
        return next((i for i, rect in enumerate(self._primary_rects(left)) if rect.contains(point)), -1)

    def mouseMoveEvent(self, event):
        if self.state in self.panels:
            left = (self.width()-self.width_spring.value)/2
            self._hover_back = QRectF(left+140, 12, 83, 27).contains(event.position())
            self.update()
        if self.state == 'hover':
            self._hover_tile = self._hit_primary(event.position())
            left = (self.width()-self.width_spring.value)/2
            self._hover_tab = next((i for i, rect in enumerate(self._tab_rects(left)) if rect.contains(event.position())), -1)
            for i, spring in enumerate(self.tile_hover_springs): spring.target = float(i == self._hover_tile)
            self._wake_animation()
        if self._drag_start is not None and self._pressed_tile < 0 and self._pressed_tab < 0:
            delta = event.globalPosition().toPoint()-self._drag_start
            if delta.manhattanLength() > 3:
                self._dragged = True
                if self.state != 'face_out': self.set_scene('face_out')
                self.move(self._window_start + delta)
                pointer = event.globalPosition().toPoint()
                now = time.monotonic()
                dt = max(.016, now-self._last_drag_time)
                self._drag_velocity = (max(-1500, min(1500, (pointer.x()-self._last_drag_point.x())/dt)),
                                       max(-1500, min(1500, (pointer.y()-self._last_drag_point.y())/dt)))
                self._last_drag_point = pointer; self._last_drag_time = now
                screen = QApplication.screenAt(pointer)
                if screen:
                    bounds = screen.geometry()
                    self._dock_preview = abs(pointer.x()-bounds.center().x()) <= 90 and 0 <= pointer.y()-bounds.top() <= 90
                    self.shoulder_spring.target = .35 if self._dock_preview else 0
                    self._wake_animation()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        for spring in self.tile_press_springs: spring.target = 0
        pressed_tile, pressed_tab = self._pressed_tile, self._pressed_tab
        self._pressed_tile = self._pressed_tab = -1
        self._wake_animation()
        if self._drag_start is not None and self.state == 'face_out' and self._dragged:
            pointer = event.globalPosition().toPoint()
            screen = QApplication.screenAt(pointer) or self.screen()
            if screen is not None:
                bounds = screen.geometry()
                dock = abs(pointer.x() - bounds.center().x()) <= 90 and 0 <= pointer.y() - bounds.top() <= 90
                self._dock_preview = False
                self.window_x_spring.set(self.x()); self.window_y_spring.set(self.y())
                self._window_gliding = True
                if dock:
                    self.window_x_spring.target = bounds.x() + (bounds.width()-self.width())//2
                    self.window_y_spring.target = bounds.y()
                    self.set_scene('resting')
                else:
                    # Clamp the visible capsule, not its transparent 720px window.
                    available = screen.availableGeometry()
                    local = self.current_path().boundingRect()
                    vx, vy = self._drag_velocity if time.monotonic()-self._last_drag_time < .12 else (0, 0)
                    # Reserve room for the final 184 x 96 capsule during morph.
                    local_left = min(local.left(), self.width()/2-92)
                    local_right = max(local.right(), self.width()/2+92)
                    local_top = min(local.top(), 18)
                    local_bottom = max(local.bottom(), 114)
                    x = max(available.left()-local_left, min(self.x()+vx*.10, available.right()+1-local_right))
                    y = max(available.top()-local_top, min(self.y()+vy*.10, available.bottom()+1-local_bottom))
                    self.window_x_spring.target = x; self.window_y_spring.target = y
                    self.shoulder_spring.target = 0
                    self._wake_animation()
        elif self._drag_start is not None and not self._dragged and self.state == 'face_out':
            eye_area = QRectF(self.eye_cx_spring.value-45, self.eye_cy_spring.value-28, 90, 56)
            if eye_area.contains(event.position()):
                self.poke_mood.poke(time.monotonic()-self._epoch)
                self.boop_scale_spring.value = .8; self.boop_scale_spring.target = 1
        elif self._drag_start is not None and not self._dragged and self.state in self.panels and event.position().y() < 42:
            self.set_scene('hover')
            self.fsm.force_home()
        elif self._drag_start is not None and not self._dragged and self.state in ('resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'):
            eye_area = QRectF(self.eye_cx_spring.value-36, self.eye_cy_spring.value-22, 72, 44)
            if eye_area.contains(event.position()):
                self.poke_mood.poke(time.monotonic()-self._epoch)
                self.boop_scale_spring.value = .8
                self.boop_scale_spring.target = 1
            else:
                self.fsm.mouse_entered()
                self.fsm.click()
        elif self._drag_start is not None and not self._dragged and self.state == 'hover':
            point = event.position()
            left = (self.width()-self.width_spring.value)/2
            for index, rect in enumerate(self._primary_rects(left)):
                if index == pressed_tile and rect.contains(point):
                    target = ('listening', 'dictation', 'write')[index]
                    self.mode_requested.emit(target)
                    self.set_scene(target)
                    break
            else:
                for index, rect in enumerate(self._tab_rects(left)):
                    if index == pressed_tab and rect.contains(point):
                        self.fsm.user_interacted()
                        self.set_scene(('chat', 'media', 'tasks', 'vision', 'shelf', 'settings')[index])
                        break
        self._drag_start = self._window_start = None
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            self._drop_active = True
            self.set_scene('hover')
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._drop_active = False
        self.set_scene(self._activity_scene)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._drop_active = False
        files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if files:
            self.files_dropped.emit(files)
            self.set_scene('shelf' if 'shelf' in self.panels else self._activity_scene)
            event.acceptProposedAction()

    def nativeEvent(self, event_type, message):
        result = win32_overlay.hit_test(self, message)
        if result is not None: return True, result
        return super().nativeEvent(event_type, message)
