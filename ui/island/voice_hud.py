"""Translucent, top-docked FRIDAY island with 1:1 Hey Nixi visuals, motion, and sound."""
import math
import time
from pathlib import Path
from PySide6.QtCore import Qt, QRectF, QTimer, Signal, QPoint, QPointF, QEvent
from PySide6.QtGui import (
    QColor, QPainter, QFont, QCursor, QPen, QLinearGradient,
    QPainterPath, QRegion, QRadialGradient,
)
from PySide6.QtWidgets import (
    QWidget, QApplication, QStackedWidget, QScrollArea,
    QLineEdit, QDateTimeEdit, QComboBox, QGraphicsOpacityEffect,
)
from .geometry import notch_path
from .physics import Spring, Tracked
from .fsm import IslandStateMachine
from . import win32_overlay
from .audio import UiSoundEngine
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
    STATES = (
        'resting', 'hover', 'listening', 'working', 'speaking',
        'dictation', 'write', 'success', 'muted', 'error',
        'chat', 'media', 'tasks', 'vision', 'shelf', 'settings', 'face_out',
    )
    _TABS = ('chat', 'media', 'tasks', 'vision', 'shelf', 'settings')
    _EXPANDED_STATES = ('hover', 'chat', 'media', 'tasks', 'vision', 'shelf', 'settings', 'write')
    _COMPACT_STATES = ('resting', 'listening', 'working', 'speaking', 'dictation', 'success', 'muted', 'error')

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
        self.sound_enabled = True
        self._user_visible = True
        self._auto_hidden = False
        self.face_out_positions = {}
        self.chat_lines = []
        self.panels = {}
        self._panel_stack = None
        self.preview_mode = False

        self.audio = UiSoundEngine(enabled=True)
        self.fsm = IslandStateMachine(on_transition=self._fsm_transition)
        self.fsm.open_on_hover = True

        # Core geometry & layout springs
        self.width_spring = Tracked(240)
        self.height_spring = Tracked(48)
        self.shoulder_spring = Spring(1, 0.40, 0.78)
        self.eye_cx_spring = Spring(360, 0.40, 0.78)
        self.eye_cy_spring = Spring(24, 0.40, 0.78)
        self.eye_scale_spring = Spring(1, 0.40, 0.78)
        self.eye_spacing_spring = Spring(14, 0.40, 0.78)
        self.content_alpha_spring = Spring(0, 0.20, 0.92)
        self.gaze_x_spring = Spring(0, 0.22, 0.88)
        self.gaze_y_spring = Spring(0, 0.22, 0.88)

        # Interaction springs
        self.tile_hover_springs = [Spring(0, 0.20, 0.90) for _ in range(3)]
        self.tile_press_springs = [Spring(0, 0.16, 0.82) for _ in range(3)]
        self.back_hover_spring = Spring(0, 0.20, 0.90)
        self.drop_glow_spring = Spring(0, 0.24, 0.88)
        self.dock_preview_spring = Spring(0, 0.22, 0.88)
        self.halo_spring = Spring(0, 0.22, 0.88)
        self.audio_smooth_spring = Spring(0, 0.20, 0.88)
        self.boop_scale_spring = Spring(1, 0.22, 0.62)
        self.shake_spring = Spring(0, 0.22, 0.58)
        self.tab_indicator_spring = Spring(0, 0.28, 0.82)
        self.window_x_spring = Spring(0, 0.38, 0.92)
        self.window_y_spring = Spring(0, 0.38, 0.92)

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
        self._blink_value = 1.0
        self._epoch = self._last = time.monotonic()
        self._last_breath_px = 0.0

        self._drag_start = self._window_start = None
        self._dragged = False
        self._drag_velocity = (0.0, 0.0)
        self._last_drag_point = None
        self._last_drag_time = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(16)

        self._fullscreen_timer = QTimer(self)
        self._fullscreen_timer.timeout.connect(self._check_fullscreen)
        if win32_overlay.IS_WINDOWS:
            self._fullscreen_timer.start(1000)

        self._anchor()
        win32_overlay.configure_overlay(self)

    def set_sound_enabled(self, enabled: bool):
        self.sound_enabled = bool(enabled)
        self.audio.enabled = self.sound_enabled

    def _play_cue(self, cue: str):
        if not self.preview_mode and self.sound_enabled:
            self.audio.play(cue)

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
            self._auto_hidden = True
            self.hide()
        elif not fullscreen and self._auto_hidden:
            self._auto_hidden = False
            if self._user_visible:
                self.show()

    def _fsm_transition(self, old, new):
        if self.state == 'hover' or self.state in (
            'resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'
        ):
            self.set_scene('hover' if new == 'home' else self._activity_scene)

    def _anchor(self):
        screen = self.screen() or (self.windowHandle().screen() if self.windowHandle() else None)
        if screen:
            g = screen.geometry()
            self.move(g.x() + (g.width() - self.width()) // 2, g.y())

    def set_scene(self, state, immediate=False):
        if state not in self.STATES:
            raise ValueError(state)
        if not self.animations_enabled:
            immediate = True

        prev_state = self.state
        if state == 'face_out' and prev_state != 'face_out' and self._drag_start is None:
            screen = self.screen()
            if screen and screen.name() in self.face_out_positions:
                from .geometry import clamp_to_screen
                pos = self.face_out_positions[screen.name()]
                bounds = QRectF(screen.availableGeometry())
                rect = clamp_to_screen(QRectF(pos[0], pos[1], self.width(), self.height()), bounds)
                self.move(int(rect.x()), int(rect.y()))

        if prev_state != state:
            self._scene_started = time.monotonic()
            if not immediate:
                self._emit_transition_sound(prev_state, state)

        self.state = state
        if state in self._TABS:
            self._active_tab = self._TABS.index(state)
            if immediate:
                self.tab_indicator_spring.set(self._active_tab)
            else:
                self.tab_indicator_spring.target = self._active_tab

        if state in ('resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'):
            self._activity_scene = state

        sizes = {
            'resting':  (240, 48),
            'hover':    (640, 160),
            'chat':     (640, 260),
            'media':    (640, 220),
            'tasks':    (640, 220),
            'vision':   (640, 220),
            'shelf':    (640, 220),
            'settings': (640, 260),
            'write':    (640, 200),
            'face_out': (184, 96),
        }
        w, h = sizes.get(state, (380, 64))
        for spring, target in ((self.width_spring, w), (self.height_spring, h)):
            if immediate:
                spring.set(target)
            else:
                spring.animate_to(target)

        if state in self._EXPANDED_STATES:
            eye_x = (self.width() - w) / 2.0 + 74.0
            eye_y = min(h / 2.0, 82.0)
        elif state in ('resting', 'face_out'):
          eye_x = self.width() / 2.0
          eye_y = h / 2.0 + (18.0 if state == 'face_out' else 0.0)
        else:
            eye_x = (self.width() - w) / 2.0 + 52.0
            eye_y = h / 2.0

        target_scale = 2.05 if state == 'face_out' else (1.28 if w > 480 else 1.0)
        target_spacing = 15.0 if state == 'face_out' else 14.0
        target_shoulder = 0.0 if state == 'face_out' else 1.0

        for spring, target in (
            (self.shoulder_spring, target_shoulder),
            (self.eye_cx_spring, eye_x),
            (self.eye_cy_spring, eye_y),
            (self.eye_scale_spring, target_scale),
            (self.eye_spacing_spring, target_spacing),
        ):
            if immediate:
                spring.set(target)
            else:
                spring.target = target

        if immediate:
            self.content_alpha_spring.set(1.0 if w > 480 else 0.0)
        elif w <= 480:
            self.content_alpha_spring.target = 0.0

        if state == 'error':
            self.shake_spring.value = 6.0
            self.shake_spring.velocity = -40.0
            self.shake_spring.target = 0.0

        if state != 'face_out' and not self._window_gliding:
            self._anchor()

        self._wake_animation()
        self._sync_panels()
        if state == 'tasks' and 'tasks' in self.panels:
            self.panels['tasks'].refresh()
        self.update()

    def _emit_transition_sound(self, old: str, new: str):
        if new == 'face_out' and old != 'face_out':
            self._play_cue('detach')
        elif old == 'face_out' and new == 'resting':
            self._play_cue('dock')
        elif new == 'listening':
            self._play_cue('listen_start')
        elif new == 'dictation':
            self._play_cue('dictate_start')
        elif old == 'dictation' and new != 'dictation':
            self._play_cue('dictate_stop')
        elif new == 'success':
            self._play_cue('complete')
        elif new == 'muted':
            self._play_cue('mute')
        elif new == 'error':
            self._play_cue('error')
        elif new in self._EXPANDED_STATES and old not in self._EXPANDED_STATES:
            self._play_cue('expand')
        elif old in self._EXPANDED_STATES and new not in self._EXPANDED_STATES:
            self._play_cue('collapse')

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
        constructors = {
            'write': lambda: CommandBar(services),
            'chat': lambda: ChatPanel(services),
            'media': lambda: MediaPanel(services),
            'tasks': lambda: TasksPanel(services),
            'vision': lambda: VisionPanel(services),
            'shelf': lambda: ShelfPanel(services),
            'settings': lambda: SettingsPanel(services, store),
        }
        self._pages = {}
        for name, constructor in constructors.items():
            panel = constructor()
            self.panels[name] = panel
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(panel)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._panel_stack.addWidget(scroll)
            self._pages[name] = scroll
            for child in panel.findChildren(QWidget):
                if isinstance(child, (QLineEdit, QDateTimeEdit, QComboBox)):
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
        if page is None or width < 480 or height < 115 or alpha <= 0.08:
            self._panel_stack.hide()
            return
        self._panel_opacity.setOpacity(alpha)
        self._panel_stack.setCurrentWidget(page)
        left = (self.width() - width) / 2.0
        gx = int(left + 148)
        gy = 44
        gw = max(0, int(width - 170))
        gh = max(0, int(height - 62))
        self._panel_stack.setGeometry(gx, gy, gw, gh)

        # Clip child panel strictly inside the morphing curved notch contour.
        notch_local = self.current_path().translated(-gx, -gy)
        inner = QPainterPath()
        inner.addRoundedRect(QRectF(0, 0, gw, gh), 12.0, 12.0)
        clip = notch_local.intersected(inner)
        poly = clip.toFillPolygon().toPolygon()
        if not poly.isEmpty():
            self._panel_stack.setMask(QRegion(poly))
        if not self._panel_stack.isVisible():
            self._panel_stack.show()

    def append_log(self, entry):
        self.chat_lines.append(str(getattr(entry, 'text', entry)))
        del self.chat_lines[:-100]
        if 'chat' in self.panels:
            self.panels['chat'].append_log(entry)
        self.update()

    def set_theme(self, theme):
        if theme not in ('dark', 'light'):
            raise ValueError(theme)
        self.theme = theme
        qss_path = Path(__file__).resolve().parent.parent / 'themes' / f'{theme}.qss'
        self.setStyleSheet(qss_path.read_text(encoding='utf-8'))
        self.theme_changed.emit(theme)
        self.update()

    def current_path(self):
        w = self.width_spring.value
        h = self.height_spring.value
        shoulder = max(0.0, min(1.0, self.shoulder_spring.value))
        # Extend 2 logical px past top when docked so Windows fractional DPI
        # scaling (125% / 150% / 175%) never exposes a top-edge hairline gap.
        top_bleed = 2.0 * shoulder
        y = 18.0 * (1.0 - shoulder)
        corner = min(18.0 * shoulder + (h / 2.0) * (1.0 - shoulder), h / 2.0)
        return notch_path(
            QRectF((self.width() - w) / 2.0, y - top_bleed, w, h + top_bleed),
            16.0,
            corner,
            shoulder,
        )

    def _wake_animation(self):
        if self._timer.isActive() and self._timer.interval() != 16:
            self._last = time.monotonic()
            self._timer.setInterval(16)

    def _animate(self):
        now = time.monotonic()
        dt = min(0.05, max(0.0, now - self._last))
        self._last = now
        self.fsm.tick()

        cursor = QCursor.pos()
        cursor_moved = cursor != self._last_cursor
        self._last_cursor = cursor
        if cursor_moved:
            self._cursor_move_time = now

        gaze_active = (not self.preview_mode) and (now - self._cursor_move_time < 3.0)
        if gaze_active:
            delta = cursor - self.mapToGlobal(
                QPoint(int(self.eye_cx_spring.value), int(self.eye_cy_spring.value))
            )
            gx, gy = gaze(delta.x(), delta.y())
        else:
            gx, gy = 0.0, 0.0
        self.gaze_x_spring.target, self.gaze_y_spring.target = gx, gy

        expanded = self.state in self._EXPANDED_STATES
        if expanded and self.width_spring.value >= self.width_spring.target * 0.72:
            self.content_alpha_spring.target = 1.0
        elif not expanded:
            self.content_alpha_spring.target = 0.0

        self.audio_smooth_spring.target = max(0.0, min(1.0, self.audio_level))
        self.halo_spring.target = (
            self.audio_smooth_spring.target
            if self.state in ('listening', 'speaking', 'dictation')
            else 0.0
        )
        self.back_hover_spring.target = 1.0 if self._hover_back else 0.0
        self.drop_glow_spring.target = 1.0 if self._drop_active else 0.0
        self.dock_preview_spring.target = 1.0 if self._dock_preview else 0.0

        springs = (
            self.width_spring, self.height_spring, self.shoulder_spring,
            self.eye_cx_spring, self.eye_cy_spring, self.eye_scale_spring,
            self.eye_spacing_spring, self.content_alpha_spring,
            self.gaze_x_spring, self.gaze_y_spring, self.halo_spring,
            self.audio_smooth_spring, self.boop_scale_spring, self.shake_spring,
            self.tab_indicator_spring, self.window_x_spring, self.window_y_spring,
            self.back_hover_spring, self.drop_glow_spring, self.dock_preview_spring,
            *self.tile_hover_springs, *self.tile_press_springs,
        )
        moving = False
        for spring in springs:
            if not spring.settled:
                spring.step(dt)
                moving = True

        if self._window_gliding:
            self.move(round(self.window_x_spring.value), round(self.window_y_spring.value))
            if self.window_x_spring.settled and self.window_y_spring.settled:
                self._window_gliding = False
                if self.state == 'face_out':
                    screen = self.screen()
                    if screen:
                        self.face_out_moved.emit(screen.name(), self.x(), self.y())

        elapsed = now - self._epoch
        active_floor = 0.16 if self.state in ('listening', 'dictation') else (
            0.10 if self.state == 'speaking' else 0.0
        )
        self.waveform.step(self.audio_smooth_spring.value, dt, elapsed, active_floor=active_floor)

        blink_before = self._blink.started is not None
        self._blink_value = 1.0 if self.preview_mode else self._blink.value(elapsed)
        blinking = blink_before or (self._blink.started is not None)

        state_animating = (
            self.state not in ('resting', 'face_out')
            or self._drag_start is not None
            or self._drop_active
            or self._dock_preview
            or self.poke_mood.is_dizzy(elapsed)
            or self.poke_mood.is_booped(elapsed)
        )

        # Quantized subtle idle breathing so resting stays alive at near-zero CPU.
        breath_px = round(0.45 * math.sin(elapsed * math.pi / 2.2), 2) if not self.preview_mode else 0.0
        breath_changed = abs(breath_px - self._last_breath_px) >= 0.12
        if breath_changed:
            self._last_breath_px = breath_px

        self._sync_panels()
        if moving or cursor_moved or blinking or state_animating or breath_changed:
            self.update()

        next_interval = 16 if (moving or cursor_moved or blinking or state_animating) else 150
        if self._timer.interval() != next_interval:
            self._timer.setInterval(next_interval)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.Antialiasing)

        pal = DARK if self.theme == 'dark' else LIGHT
        is_default_black = (self.theme == 'dark' and self.notch_colour == '#000000')
        background = pal['background'] if (self.theme == 'light' and self.notch_colour == '#000000') else self.notch_colour

        path = self.current_path()
        bounds = path.boundingRect()
        shoulder = max(0.0, min(1.0, self.shoulder_spring.value))
        w, h = self.width_spring.value, self.height_spring.value
        left = (self.width() - w) / 2.0

        # 1. Subtle soft ambient drop shadow when detached as a floating capsule
        if shoulder < 0.6:
            shadow_alpha = int(52 * (1.0 - shoulder))
            p.save()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, shadow_alpha))
            p.translate(0, 4.0 * (1.0 - shoulder))
            p.drawPath(path)
            p.restore()

        # 2. Notch / floating capsule body fill
        p.setPen(Qt.NoPen)
        if self.state == 'face_out' and (is_default_black or self.theme == 'light'):
            grad = QLinearGradient(0, bounds.top(), 0, bounds.bottom())
            grad.setColorAt(0.0, QColor(pal['capsule_top']))
            grad.setColorAt(0.45, QColor(pal['capsule_mid']))
            grad.setColorAt(1.0, QColor(pal['capsule_bottom']))
            p.setBrush(grad)
        elif is_default_black:
            # Matte-black resting/expanded notch with ultra-subtle bottom depth
            grad = QLinearGradient(0, bounds.top(), 0, bounds.bottom())
            grad.setColorAt(0.0, QColor('#000000'))
            grad.setColorAt(0.85, QColor('#020204'))
            grad.setColorAt(1.0, QColor('#0A0A0E'))
            p.setBrush(grad)
        else:
            p.setBrush(QColor(background))
        p.drawPath(path)

        p.save()
        p.setClipPath(path)

        # 3. Subtle top vertical surface sheen on floating capsule
        if shoulder < 0.5:
            sheen_alpha = int((1.0 - shoulder * 2.0) * (24 if self.theme == 'dark' else 48))
            sheen = QLinearGradient(0, bounds.top(), 0, bounds.top() + bounds.height() * 0.52)
            sheen.setColorAt(0.0, QColor(255, 255, 255, sheen_alpha))
            sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(sheen)
            p.drawRect(bounds)

        # 4. Crisp outer rim (full perimeter on floating capsule; bottom/sides only when docked)
        rim_rgba = pal['capsule_rim'] if self.state == 'face_out' else pal['rim']
        p.save()
        if shoulder > 0.05:
            # Exclude the flush top bezel edge so no white hairline appears at y=0
            p.setClipRect(QRectF(0, 1.5, self.width(), self.height()))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(*rim_rgba), 1.4 if self.state == 'face_out' else 1.15))
        p.drawPath(path)
        p.restore()

        expanded = self.state in self._EXPANDED_STATES and w > 480
        elapsed = time.monotonic() - self._epoch
        scene_age = max(0.0, time.monotonic() - self._scene_started)

        # 5. Subtle mascot eye capsule well in expanded view (matches Hey Nixi panels)
        if expanded:
            well_alpha = max(0.0, min(1.0, self.content_alpha_spring.value))
            p.save()
            p.setOpacity(well_alpha)
            well_rect = QRectF(left + 26, self.eye_cy_spring.value - 30, 96, 60)
            p.setBrush(QColor(pal['mascot_well']))
            p.setPen(QPen(QColor(*pal['rim']), 1.0))
            p.drawRoundedRect(well_rect, 28, 28)
            p.restore()

        # 6. Expressive pill eyes
        cx = self.eye_cx_spring.value + self.shake_spring.value
        cy = self.eye_cy_spring.value + (0.0 if self.preview_mode else self._last_breath_px)

        look = (0.0, 0.0) if self.preview_mode else (self.gaze_x_spring.value, self.gaze_y_spring.value)
        mood = 'normal'
        working_phase = None
        listening_ring = 0.0

        if self.state == 'working':
            mood = 'working'
            working_phase = 1.6 if self.preview_mode else elapsed
            look = (0.35 if self.preview_mode else math.sin(elapsed * 1.7) * 0.45, -0.18)
        elif self.state == 'muted':
            mood = 'muted'
        elif self.state == 'error':
            mood = 'error'
        elif self.state == 'success':
            mood = 'happy'

        if self.poke_mood.is_dizzy(elapsed):
            mood = 'dizzy'
            look = (math.sin(elapsed * 10.0) * 0.85, math.cos(elapsed * 10.0) * 0.55)
        elif self.poke_mood.is_booped(elapsed) and mood == 'normal':
            mood = 'booped'

        blink = 1.0 if self.preview_mode else self._blink_value
        scale = self.eye_scale_spring.value * self.boop_scale_spring.value
        if self.state == 'speaking':
            scale *= 1.0 + 0.065 * self.audio_smooth_spring.value

        if self.state in ('listening', 'dictation'):
            listening_ring = 0.42 if self.preview_mode else ((elapsed * 0.65) % 1.0)

        halo_on = self.state in ('resting', 'listening', 'dictation', 'face_out', 'speaking', 'working', 'success') or expanded
        if self.state in ('listening', 'dictation'):
            halo_strength = 1.15 + 0.35 * (0.5 if self.preview_mode else math.sin(elapsed * 2.6)) + self.halo_spring.value * 0.6
            halo_colour = pal['accent']
        elif self.state == 'face_out':
            halo_strength = 1.25
            halo_colour = '#ffffff'
        elif self.state == 'success':
            halo_strength = 1.0
            halo_colour = '#ffffff'
        else:
            halo_strength = 0.95
            halo_colour = '#ffffff'

        paint_eyes(
            p, cx, cy,
            colour=readable_eye_colour(self.eye_colour, background),
            scale=scale,
            spacing=self.eye_spacing_spring.value,
            blink=blink,
            look=look,
            halo=halo_on,
            halo_strength=halo_strength,
            halo_colour=halo_colour,
            dim=(self.state == 'muted'),
            happy=(self.state == 'success' and (self.preview_mode or scene_age < 1.8)),
            mood=mood,
            working_phase=working_phase,
            listening_ring=listening_ring,
        )

        # 7. State content (expanded or compact live status)
        p.setPen(QColor(pal['ink']))
        p.setFont(QFont('Segoe UI', 10))
        if expanded:
            p.save()
            p.setOpacity(max(0.0, min(1.0, self.content_alpha_spring.value)))
            self._paint_expanded(p, left, w, h, pal)
            p.restore()
        elif self.state not in ('resting', 'face_out'):
            self._paint_compact_status(p, left, w, h, pal, elapsed, scene_age)

        # 8. Magnetic top-center dock preview indicator
        dock_t = max(0.0, min(1.0, self.dock_preview_spring.value))
        if dock_t > 0.02:
            p.save()
            dock_c = QColor(pal['accent'])
            dock_c.setAlpha(int(190 * dock_t))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(dock_c, 2.2))
            p.drawPath(path)
            pill_w = 54.0 * (0.85 + 0.15 * dock_t)
            p.setPen(Qt.NoPen)
            dock_c.setAlpha(int(210 * dock_t))
            p.setBrush(dock_c)
            p.drawRoundedRect(QRectF(self.width() / 2.0 - pill_w / 2.0, bounds.top() + 5.0, pill_w, 3.5), 1.75, 1.75)
            p.restore()

        # 9. Glowing file drop-zone overlay
        drop_t = max(0.0, min(1.0, self.drop_glow_spring.value))
        if drop_t > 0.02:
            self._paint_drop_zone(p, left, w, h, pal, drop_t, elapsed)

        p.restore()
        p.end()

    def _paint_compact_status(self, p, left, w, h, pal, elapsed, scene_age):
        labels = {
            'listening': self.live_transcript.strip() or 'Listening',
            'working':   'Working on it',
            'speaking':  self.live_transcript.strip() or 'Speaking',
            'dictation': self.live_transcript.strip() or 'Dictating',
            'write':     'Write a command',
            'success':   'Done',
            'muted':     'Mic muted',
            'error':     'Something went wrong',
        }
        text = labels.get(self.state, self.state.title())
        font = QFont('Segoe UI', 10, QFont.Medium)
        p.setFont(font)
        fm = p.fontMetrics()
        text_rect = QRectF(left + 96, 0, w - 164, h)
        elided = fm.elidedText(text, Qt.ElideRight, int(text_rect.width()))
        p.setPen(QColor(pal['ink']))
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided)

        if self.state in ('listening', 'speaking', 'dictation'):
            self._paint_waveform(p, left + w - 50, h / 2.0, pal)
            if self.state == 'dictation':
                p.setPen(Qt.NoPen)
                for i in range(3):
                    pulse = 0.5 + 0.5 * math.sin((1.2 if self.preview_mode else elapsed * 4.5) - i * 0.7)
                    dot_c = QColor(pal['accent'])
                    dot_c.setAlpha(int(90 + 165 * pulse))
                    p.setBrush(dot_c)
                    r = 2.0 + 1.2 * pulse
                    p.drawEllipse(QPointF(left + w - 82 + i * 8.5, h / 2.0), r, r)
        elif self.state == 'working':
            p.setPen(Qt.NoPen)
            for i in range(3):
                t = (1.4 if self.preview_mode else elapsed * 4.4) - i * 0.75
                wave = 0.5 + 0.5 * math.sin(t)
                dot_c = QColor(pal['ink'])
                dot_c.setAlpha(int(75 + 180 * wave))
                p.setBrush(dot_c)
                dy = -2.2 * wave
                p.drawEllipse(QPointF(left + w - 46 + i * 9.0, h / 2.0 + dy), 2.3, 2.3)
        elif self.state == 'success':
            self._paint_success_check(p, left + w - 34, h / 2.0, pal['success'], 1.0 if self.preview_mode else min(1.0, scene_age / 0.36))
        elif self.state == 'muted':
            self._paint_muted_mic(p, left + w - 34, h / 2.0, pal['danger'], 1.0 if self.preview_mode else min(1.0, scene_age / 0.28))
        elif self.state == 'error':
            self._paint_error_badge(p, left + w - 34, h / 2.0, pal['danger'], elapsed)

    def _paint_success_check(self, p, cx, cy, colour_hex, progress):
        p.save()
        c = QColor(colour_hex)
        # Soft green circular badge glow
        bg = QColor(c)
        bg.setAlpha(int(42 * progress))
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        r = 12.0 * (0.85 + 0.15 * progress)
        p.drawEllipse(QPointF(cx, cy), r, r)

        # Animated progressive checkmark stroke
        p.setPen(QPen(c, 2.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p0 = QPointF(cx - 5.5, cy + 0.2)
        p1 = QPointF(cx - 1.5, cy + 4.4)
        p2 = QPointF(cx + 6.0, cy - 4.2)
        if progress <= 0.42:
            t = progress / 0.42
            mid = QPointF(p0.x() + (p1.x() - p0.x()) * t, p0.y() + (p1.y() - p0.y()) * t)
            p.drawLine(p0, mid)
        else:
            t = min(1.0, (progress - 0.42) / 0.58)
            end = QPointF(p1.x() + (p2.x() - p1.x()) * t, p1.y() + (p2.y() - p1.y()) * t)
            p.drawLine(p0, p1)
            p.drawLine(p1, end)
        p.restore()

    def _paint_muted_mic(self, p, cx, cy, colour_hex, progress):
        p.save()
        c = QColor(colour_hex)
        bg = QColor(c)
        bg.setAlpha(38)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawEllipse(QPointF(cx, cy), 12.0, 12.0)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(c, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawRoundedRect(QRectF(cx - 3.0, cy - 6.8, 6.0, 9.2), 3.0, 3.0)
        p.drawArc(QRectF(cx - 5.8, cy - 4.0, 11.6, 10.5), 200 * 16, 140 * 16)
        p.drawLine(QPointF(cx, cy + 6.5), QPointF(cx, cy + 8.6))

        # Animated red diagonal slash
        s0 = QPointF(cx - 7.5, cy + 7.2)
        s1 = QPointF(cx + 7.5, cy - 7.2)
        end = QPointF(s0.x() + (s1.x() - s0.x()) * progress, s0.y() + (s1.y() - s0.y()) * progress)
        p.setPen(QPen(c, 2.1, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(s0, end)
        p.restore()

    def _paint_error_badge(self, p, cx, cy, colour_hex, elapsed):
        p.save()
        c = QColor(colour_hex)
        pulse = 0.5 + 0.5 * math.sin(0.0 if self.preview_mode else elapsed * 5.0)
        glow = QColor(c)
        glow.setAlpha(int(36 + 32 * pulse))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), 13.0 + 1.5 * pulse, 13.0 + 1.5 * pulse)

        p.setBrush(c)
        p.drawEllipse(QPointF(cx, cy), 9.5, 9.5)
        p.setPen(QPen(QColor('#FFFFFF'), 2.0, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx, cy - 4.4), QPointF(cx, cy + 1.0))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#FFFFFF'))
        p.drawEllipse(QPointF(cx, cy + 4.3), 1.3, 1.3)
        p.restore()

    def _paint_drop_zone(self, p, left, w, h, pal, drop_t, elapsed):
        p.save()
        p.setOpacity(drop_t)
        accent = QColor(pal['accent'])
        fill = QColor(accent)
        fill.setAlpha(28)
        zone = QRectF(left + 12, 8, w - 24, h - 18)
        p.setBrush(fill)
        pen = QPen(accent, 2.0, Qt.DashLine)
        pen.setDashOffset(0.0 if self.preview_mode else -(elapsed * 18.0))
        p.setPen(pen)
        p.drawRoundedRect(zone, 14, 14)

        # Centered drop icon + prompt
        icx = zone.center().x()
        icy = zone.center().y() - 10
        p.setPen(QPen(QColor('#FFFFFF' if self.theme == 'dark' else pal['ink']), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(icx, icy - 8), QPointF(icx, icy + 5))
        p.drawLine(QPointF(icx - 5, icy), QPointF(icx, icy + 5))
        p.drawLine(QPointF(icx + 5, icy), QPointF(icx, icy + 5))
        p.drawRoundedRect(QRectF(icx - 10, icy + 6, 20, 6), 2, 2)

        p.setFont(QFont('Segoe UI', 10, QFont.DemiBold))
        p.setPen(QColor('#F5F6F8' if self.theme == 'dark' else pal['ink']))
        p.drawText(QRectF(zone.left(), icy + 16, zone.width(), 24), Qt.AlignCenter, 'Drop files to FRIDAY Shelf')
        p.restore()

    def _paint_waveform(self, p, x, cy, pal):
        p.setPen(Qt.NoPen)
        bar_colour = QColor('#FFFFFF' if self.theme == 'dark' and self.state == 'speaking' else pal['accent'])
        p.setBrush(bar_colour)
        levels = self.waveform.values
        if self.preview_mode:
            levels = [self.audio_level * v for v in (0.45, 0.82, 1.0, 0.78, 0.42)]
        for i, level in enumerate(levels):
            height = 4.0 + min(1.0, level) * 20.0
            p.drawRoundedRect(QRectF(x + i * 6.0, cy - height / 2.0, 3.0, height), 1.5, 1.5)

    def _paint_expanded(self, p, left, w, h, pal):
        back_t = max(0.0, min(1.0, self.back_hover_spring.value))
        if self.state != 'hover' and back_t > 0.02:
            p.setPen(Qt.NoPen)
            pill_c = QColor(255, 255, 255, int(26 * back_t)) if self.theme == 'dark' else QColor(20, 21, 24, int(18 * back_t))
            p.setBrush(pill_c)
            p.drawRoundedRect(QRectF(left + 140, 11, 98, 26), 13, 13)

        p.setFont(QFont('Segoe UI', 9, QFont.DemiBold))
        header_c = QColor(pal['ink'] if (self.state != 'hover' and back_t > 0.4) else pal['muted'])
        p.setPen(header_c)
        prefix = 'FRIDAY  /  ' if self.state == 'hover' else f"{'‹ ' if back_t < 0.5 else '‹  '}FRIDAY  /  "
        p.drawText(QRectF(left + 148, 13, w - 168, 22), Qt.AlignVCenter, prefix + self.state.upper())
        p.setFont(QFont('Segoe UI', 10))

        if self.state == 'hover':
            for i, (rect, label) in enumerate(zip(self._primary_rects(left), ('Command', 'Dictate', 'Write'))):
                hover = max(0.0, min(1.0, self.tile_hover_springs[i].value))
                press = max(0.0, min(1.0, self.tile_press_springs[i].value))
                p.save()
                p.translate(rect.center())
                s = 1.0 + 0.015 * hover - 0.045 * press
                p.scale(s, s)
                p.translate(-rect.center())

                base = QColor(pal['card'])
                hov = QColor(pal['card_hover'])
                fill = QColor(
                    int(base.red() + (hov.red() - base.red()) * hover),
                    int(base.green() + (hov.green() - base.green()) * hover),
                    int(base.blue() + (hov.blue() - base.blue()) * hover),
                )
                if press > 0.05:
                    accent = QColor(pal['accent'])
                    accent.setAlpha(int(48 * press))
                    p.setBrush(fill)
                    p.setPen(QPen(QColor(pal['accent']), 1.3))
                else:
                    border_alpha = int(24 + 42 * hover)
                    border_c = QColor(255, 255, 255, border_alpha) if self.theme == 'dark' else QColor(20, 21, 24, border_alpha)
                    p.setBrush(fill)
                    p.setPen(QPen(border_c, 1.0))

                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)

                # Circular icon badge inside tile
                badge_c = QColor(255, 255, 255, int(18 + 22 * hover)) if self.theme == 'dark' else QColor(20, 21, 24, int(14 + 14 * hover))
                p.setPen(Qt.NoPen)
                p.setBrush(badge_c)
                p.drawEllipse(QPointF(rect.center().x(), rect.top() + 22), 13, 13)

                icon_col = pal['accent'] if press > 0.3 else pal['ink']
                self._paint_tile_icon(p, rect.center().x(), rect.top() + 22, i, icon_col)
                p.setFont(QFont('Segoe UI', 9.5, QFont.Medium))
                p.setPen(QColor(pal['ink']))
                p.drawText(QRectF(rect.left(), rect.top() + 38, rect.width(), 22), Qt.AlignCenter, label)
                p.restore()

            # Smooth active-tab indicator pill across all 6 tabs
            if self._active_tab >= 0:
                ind_x = left + 145 + self.tab_indicator_spring.value * 72
                indicator = QRectF(ind_x, 124, 70, 24)
                p.setPen(Qt.NoPen)
                ind_c = QColor(255, 255, 255, 32) if self.theme == 'dark' else QColor(20, 21, 24, 24)
                p.setBrush(ind_c)
                p.drawRoundedRect(indicator, 11, 11)

            for i, (rect, label) in enumerate(zip(self._tab_rects(left), ('Chat', 'Media', 'Tasks', 'Vision', 'Shelf', 'Settings'))):
                if i == self._hover_tab and i != self._active_tab:
                    p.setPen(Qt.NoPen)
                    hov_c = QColor(255, 255, 255, 18) if self.theme == 'dark' else QColor(20, 21, 24, 14)
                    p.setBrush(hov_c)
                    p.drawRoundedRect(rect, 11, 11)
                is_active = (i == self._active_tab) or (i == self._hover_tab)
                p.setFont(QFont('Segoe UI', 9, QFont.DemiBold if i == self._active_tab else QFont.Normal))
                p.setPen(QColor(pal['ink'] if is_active else pal['muted']))
                p.drawText(rect, Qt.AlignCenter, label)
        else:
            p.setPen(QPen(QColor(*pal['rim']), 1.0))
            p.setBrush(QColor(pal['surface']))
            p.drawRoundedRect(QRectF(left + 145, 42, w - 162, max(30, h - 56)), 14, 14)

    @staticmethod
    def _paint_tile_icon(p, x, y, index, colour):
        p.save()
        p.translate(x, y)
        p.setPen(QPen(QColor(colour), 1.55, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        if index == 0:
            # Cursor / Command arrow
            path = QPainterPath(QPointF(-4.5, -6.0))
            path.lineTo(-4.5, 5.0)
            path.lineTo(-1.2, 2.2)
            path.lineTo(1.5, 6.5)
            path.lineTo(3.2, 5.5)
            path.lineTo(0.8, 1.4)
            path.lineTo(4.8, 1.0)
            path.closeSubpath()
            p.drawPath(path)
        elif index == 1:
            # Microphone / Dictate
            p.drawRoundedRect(QRectF(-2.6, -6.5, 5.2, 8.8), 2.6, 2.6)
            p.drawArc(QRectF(-5.5, -3.5, 11.0, 9.8), 180 * 16, 180 * 16)
            p.drawLine(QPointF(0.0, 6.2), QPointF(0.0, 8.2))
            p.drawLine(QPointF(-2.8, 8.2), QPointF(2.8, 8.2))
        else:
            # Pencil / Write
            p.drawLine(QPointF(-5.0, 4.5), QPointF(3.5, -4.0))
            p.drawLine(QPointF(-2.5, 7.0), QPointF(6.0, -1.5))
            p.drawLine(QPointF(3.5, -4.0), QPointF(6.0, -1.5))
            p.drawLine(QPointF(-5.0, 4.5), QPointF(-6.0, 7.5))
            p.drawLine(QPointF(-6.0, 7.5), QPointF(-2.5, 7.0))
        p.restore()

    @staticmethod
    def _primary_rects(left):
        return [QRectF(left + 145 + i * 145, 50, 134, 66) for i in range(3)]

    @staticmethod
    def _tab_rects(left):
        return [QRectF(left + 145 + i * 72, 124, 70, 24) for i in range(6)]

    def enterEvent(self, event):
        self._wake_animation()
        if self.state == 'hover' or self.state in (
            'resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'
        ):
            self.fsm.mouse_entered()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover_tile = self._hover_tab = -1
        self._hover_back = False
        for spring in self.tile_hover_springs:
            spring.target = 0.0
        self._wake_animation()
        if self.state == 'hover' or self.state in (
            'resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'
        ):
            self.fsm.mouse_left()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._window_gliding = False
            self.window_x_spring.set(self.x())
            self.window_y_spring.set(self.y())
            self._drag_start = event.globalPosition().toPoint()
            self._window_start = self.pos()
            self._dragged = False
            self._last_drag_point = self._drag_start
            self._last_drag_time = time.monotonic()
            self._drag_velocity = (0.0, 0.0)
            self._pressed_tile = self._hit_primary(event.position()) if self.state == 'hover' else -1
            left = (self.width() - self.width_spring.value) / 2.0
            self._pressed_tab = (
                next((i for i, rect in enumerate(self._tab_rects(left)) if rect.contains(event.position())), -1)
                if self.state == 'hover'
                else -1
            )
            if self._pressed_tile >= 0:
                self.tile_press_springs[self._pressed_tile].target = 1.0
                self._wake_animation()
        super().mousePressEvent(event)

    def _hit_primary(self, point):
        left = (self.width() - self.width_spring.value) / 2.0
        return next((i for i, rect in enumerate(self._primary_rects(left)) if rect.contains(point)), -1)

    def mouseMoveEvent(self, event):
        if self.state in self.panels:
            left = (self.width() - self.width_spring.value) / 2.0
            was_back = self._hover_back
            self._hover_back = QRectF(left + 140, 11, 98, 26).contains(event.position())
            if self._hover_back and not was_back:
                self._play_cue('hover')
            self._wake_animation()

        if self.state == 'hover':
            prev_tile, prev_tab = self._hover_tile, self._hover_tab
            self._hover_tile = self._hit_primary(event.position())
            left = (self.width() - self.width_spring.value) / 2.0
            self._hover_tab = next(
                (i for i, rect in enumerate(self._tab_rects(left)) if rect.contains(event.position())),
                -1,
            )
            if (self._hover_tile != prev_tile and self._hover_tile >= 0) or (
                self._hover_tab != prev_tab and self._hover_tab >= 0
            ):
                self._play_cue('hover')
            for i, spring in enumerate(self.tile_hover_springs):
                spring.target = float(i == self._hover_tile)
            self._wake_animation()

        if self._drag_start is not None and self._pressed_tile < 0 and self._pressed_tab < 0:
            delta = event.globalPosition().toPoint() - self._drag_start
            if delta.manhattanLength() > 3:
                self._dragged = True
                if self.state != 'face_out':
                    # Keep the visible center locked under the cursor on detach (zero jump).
                    old_cy = self.eye_cy_spring.value
                    self.set_scene('face_out')
                    y_comp = int(round(old_cy - 66.0))
                    self._window_start = QPoint(self._window_start.x(), self._window_start.y() + y_comp)
                self.move(self._window_start + delta)
                pointer = event.globalPosition().toPoint()
                now = time.monotonic()
                dt = max(0.016, now - self._last_drag_time)
                self._drag_velocity = (
                    max(-1500.0, min(1500.0, (pointer.x() - self._last_drag_point.x()) / dt)),
                    max(-1500.0, min(1500.0, (pointer.y() - self._last_drag_point.y()) / dt)),
                )
                self._last_drag_point = pointer
                self._last_drag_time = now
                screen = QApplication.screenAt(pointer)
                if screen:
                    bounds = screen.geometry()
                    was_preview = self._dock_preview
                    self._dock_preview = (
                        abs(pointer.x() - bounds.center().x()) <= 95
                        and 0 <= pointer.y() - bounds.top() <= 95
                    )
                    if self._dock_preview and not was_preview:
                        self._play_cue('hover')
                    self.shoulder_spring.target = 0.42 if self._dock_preview else 0.0
                    self._wake_animation()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        for spring in self.tile_press_springs:
            spring.target = 0.0
        pressed_tile, pressed_tab = self._pressed_tile, self._pressed_tab
        self._pressed_tile = self._pressed_tab = -1
        self._wake_animation()

        if self._drag_start is not None and self.state == 'face_out' and self._dragged:
            pointer = event.globalPosition().toPoint()
            screen = QApplication.screenAt(pointer) or self.screen()
            if screen is not None:
                bounds = screen.geometry()
                dock = (
                    abs(pointer.x() - bounds.center().x()) <= 95
                    and 0 <= pointer.y() - bounds.top() <= 95
                )
                self._dock_preview = False
                self.window_x_spring.set(self.x())
                self.window_y_spring.set(self.y())
                self._window_gliding = True
                if dock:
                    self.window_x_spring.target = bounds.x() + (bounds.width() - self.width()) // 2
                    self.window_y_spring.target = bounds.y()
                    self.set_scene('resting')
                else:
                    available = screen.availableGeometry()
                    local = self.current_path().boundingRect()
                    vx, vy = self._drag_velocity if time.monotonic() - self._last_drag_time < 0.12 else (0.0, 0.0)
                    local_left = min(local.left(), self.width() / 2.0 - 92.0)
                    local_right = max(local.right(), self.width() / 2.0 + 92.0)
                    local_top = min(local.top(), 18.0)
                    local_bottom = max(local.bottom(), 114.0)
                    x = max(available.left() - local_left, min(self.x() + vx * 0.09, available.right() + 1 - local_right))
                    y = max(available.top() - local_top, min(self.y() + vy * 0.09, available.bottom() + 1 - local_bottom))
                    self.window_x_spring.target = x
                    self.window_y_spring.target = y
                    self.shoulder_spring.target = 0.0
                    self._wake_animation()
        elif self._drag_start is not None and not self._dragged and self.state == 'face_out':
            eye_area = QRectF(self.eye_cx_spring.value - 45, self.eye_cy_spring.value - 28, 90, 56)
            if eye_area.contains(event.position()):
                self._trigger_eye_poke()
        elif self._drag_start is not None and not self._dragged and self.state in self.panels and event.position().y() < 42:
            self._play_cue('click')
            self.set_scene('hover')
            self.fsm.force_home()
        elif self._drag_start is not None and not self._dragged and self.state in (
            'resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'
        ):
            eye_area = QRectF(self.eye_cx_spring.value - 36, self.eye_cy_spring.value - 22, 72, 44)
            if eye_area.contains(event.position()):
                self._trigger_eye_poke()
            else:
                self.fsm.mouse_entered()
                self.fsm.click()
        elif self._drag_start is not None and not self._dragged and self.state == 'hover':
            point = event.position()
            left = (self.width() - self.width_spring.value) / 2.0
            for index, rect in enumerate(self._primary_rects(left)):
                if index == pressed_tile and rect.contains(point):
                    target = ('listening', 'dictation', 'write')[index]
                    self._play_cue('click')
                    self.mode_requested.emit(target)
                    self.set_scene(target)
                    break
            else:
                for index, rect in enumerate(self._tab_rects(left)):
                    if index == pressed_tab and rect.contains(point):
                        self._play_cue('click')
                        self.fsm.user_interacted()
                        self.set_scene(self._TABS[index])
                        break

        self._drag_start = self._window_start = None
        super().mouseReleaseEvent(event)

    def _trigger_eye_poke(self):
        elapsed = time.monotonic() - self._epoch
        self.poke_mood.poke(elapsed)
        self._blink.trigger(elapsed)
        self.boop_scale_spring.value = 0.78
        self.boop_scale_spring.target = 1.0
        self._play_cue('poke')
        self._wake_animation()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            self._drop_active = True
            self.set_scene('hover')
            self._wake_animation()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._drop_active = False
        self.set_scene(self._activity_scene)
        self._wake_animation()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._drop_active = False
        files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if files:
            self._play_cue('complete')
            self.files_dropped.emit(files)
            self.set_scene('shelf' if 'shelf' in self.panels else self._activity_scene)
            event.acceptProposedAction()

    def nativeEvent(self, event_type, message):
        result = win32_overlay.hit_test(self, message)
        if result is not None:
            return True, result
        return super().nativeEvent(event_type, message)