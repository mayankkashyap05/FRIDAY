"""Translucent, top-docked FRIDAY island with 1:1 Hey Nixi visuals, motion, and sound."""
import math
import time
from pathlib import Path
from PySide6.QtCore import Qt, QRectF, QTimer, Signal, QPoint, QPointF
from PySide6.QtGui import (
    QColor, QPainter, QFont, QCursor, QPen, QLinearGradient,
)
from PySide6.QtWidgets import QWidget, QApplication
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
    search_requested = Signal()
    context_requested = Signal(object)
    command_requested = Signal(str)
    mode_requested = Signal(str)
    face_out_moved = Signal(str, int, int)
    theme_changed = Signal(str)
    STATES = ('resting', 'listening', 'working', 'speaking', 'dictation', 'success', 'muted', 'error', 'face_out')
    _LEGACY_COMPACT_ALIASES = ('hover', 'write', 'chat', 'media', 'tasks', 'vision', 'shelf', 'settings')
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
        self.preview_mode = False

        self.audio = UiSoundEngine(enabled=True)
        self.fsm = IslandStateMachine(on_transition=self._fsm_transition)
        self.fsm.open_on_hover = False

        # Core geometry & layout springs
        self.width_spring = Tracked(240)
        self.height_spring = Tracked(48)
        self.shoulder_spring = Spring(1, 0.40, 0.78)
        self.eye_cx_spring = Spring(360, 0.40, 0.78)
        self.eye_cy_spring = Spring(24, 0.40, 0.78)
        self.eye_scale_spring = Spring(1, 0.40, 0.78)
        self.eye_spacing_spring = Spring(14, 0.40, 0.78)
        self.gaze_x_spring = Spring(0, 0.22, 0.88)
        self.gaze_y_spring = Spring(0, 0.22, 0.88)

        # Interaction springs
        self.drop_glow_spring = Spring(0, 0.24, 0.88)
        self.dock_preview_spring = Spring(0, 0.22, 0.88)
        self.halo_spring = Spring(0, 0.22, 0.88)
        self.audio_smooth_spring = Spring(0, 0.20, 0.88)
        self.boop_scale_spring = Spring(1, 0.22, 0.62)
        self.shake_spring = Spring(0, 0.22, 0.58)
        self.window_x_spring = Spring(0, 0.38, 0.92)
        self.window_y_spring = Spring(0, 0.38, 0.92)

        self._window_gliding = False
        self._dock_preview = False
        self.waveform = Waveform()
        self.poke_mood = PokeMood()
        self._drop_active = False
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
        # The FSM remains available for compatibility, but its hover/home states
        # never expand the native island.
        return

    def _anchor(self):
        screen = self.screen() or (self.windowHandle().screen() if self.windowHandle() else None)
        if screen:
            g = screen.geometry()
            self.move(g.x() + (g.width() - self.width()) // 2, g.y())

    def set_scene(self, state, immediate=False):
        # Legacy panel/launcher names resolve to the compact home state.
        if state in self._LEGACY_COMPACT_ALIASES:
            state = 'resting'
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
        if state in ('resting', 'listening', 'working', 'speaking', 'muted', 'error', 'success'):
            self._activity_scene = state

        sizes = {
            'resting':  (240, 48),
            'face_out': (184, 96),
        }
        w, h = sizes.get(state, (380, 64))
        for spring, target in ((self.width_spring, w), (self.height_spring, h)):
            if immediate:
                spring.set(target)
            else:
                spring.animate_to(target)

        if state in ('resting', 'face_out'):
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

        if state == 'error':
            self.shake_spring.value = 6.0
            self.shake_spring.velocity = -40.0
            self.shake_spring.target = 0.0

        if state != 'face_out' and not self._window_gliding:
            self._anchor()

        self._wake_animation()
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

    def attach_panels(self, services, store):
        """Create the native panel widgets; SettingsWindow owns their layout."""
        if self.panels:
            return
        from ui.panels.command_bar import CommandBar
        from ui.panels.chat_panel import ChatPanel
        from ui.panels.media_panel import MediaPanel
        from ui.panels.tasks_panel import TasksPanel
        from ui.panels.vision_panel import VisionPanel
        from ui.panels.shelf_panel import ShelfPanel
        from ui.panels.settings_panel import SettingsPanel
        self.panels = {
            'write': CommandBar(services),
            'chat': ChatPanel(services),
            'media': MediaPanel(services),
            'tasks': TasksPanel(services),
            'vision': VisionPanel(services),
            'shelf': ShelfPanel(services),
            'settings': SettingsPanel(services, store),
        }
        self.panels['write'].submitted.connect(self.command_requested)
        self.panels['write'].mode_requested.connect(self.mode_requested)
        self.panels['chat'].submitted.connect(self.command_requested)

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

        self.audio_smooth_spring.target = max(0.0, min(1.0, self.audio_level))
        self.halo_spring.target = (
            self.audio_smooth_spring.target
            if self.state in ('listening', 'speaking', 'dictation')
            else 0.0
        )
        self.drop_glow_spring.target = 1.0 if self._drop_active else 0.0
        self.dock_preview_spring.target = 1.0 if self._dock_preview else 0.0

        springs = (
            self.width_spring, self.height_spring, self.shoulder_spring,
            self.eye_cx_spring, self.eye_cy_spring, self.eye_scale_spring,
            self.eye_spacing_spring,
            self.gaze_x_spring, self.gaze_y_spring, self.halo_spring,
            self.audio_smooth_spring, self.boop_scale_spring, self.shake_spring,
            self.window_x_spring, self.window_y_spring,
            self.drop_glow_spring, self.dock_preview_spring,
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
            # Matte-black resting notch with ultra-subtle bottom depth
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

        elapsed = time.monotonic() - self._epoch
        scene_age = max(0.0, time.monotonic() - self._scene_started)

        # 5. Expressive pill eyes
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

        halo_on = self.state in ('resting', 'listening', 'dictation', 'face_out', 'speaking', 'working', 'success')
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

        # 6. Compact, live status content only; no panel or launcher UI.
        p.setPen(QColor(pal['ink']))
        p.setFont(QFont('Segoe UI', 10))
        if self.state not in ('resting', 'face_out'):
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

    def enterEvent(self, event):
        self._wake_animation()
        self.fsm.mouse_entered()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._wake_animation()
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
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
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
                    self._dock_preview = (abs(pointer.x() - bounds.center().x()) <= 95 and 0 <= pointer.y() - bounds.top() <= 95)
                    if self._dock_preview and not was_preview:
                        self._play_cue('hover')
                    self.shoulder_spring.target = 0.42 if self._dock_preview else 0.0
                    self._wake_animation()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
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
        elif self._drag_start is not None and not self._dragged and self.state != 'face_out':
            self._play_cue('click')
            self.search_requested.emit()

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

    def contextMenuEvent(self, event):
        self.context_requested.emit(event.globalPos())
        event.accept()

    def nativeEvent(self, event_type, message):
        result = win32_overlay.hit_test(self, message)
        if result is not None:
            return True, result
        return super().nativeEvent(event_type, message)