"""Apple macOS Liquid-Glass 'Search or Ask' floating pill shown below the island."""
import math
import time
from PySide6.QtCore import (
    Qt,
    Signal,
    QEvent,
    QPropertyAnimation,
    QEasingCurve,
    QRectF,
    QPoint,
    QPointF,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QLinearGradient,
    QRadialGradient,
    QPalette,
    QRegion,
    QGuiApplication,
)
from PySide6.QtWidgets import (
    QWidget,
    QLineEdit,
    QToolButton,
    QHBoxLayout,
    QProxyStyle,
    QStyle,
    QStyleOption,
)


PAD_X = 24
PAD_TOP = 16
PAD_BOTTOM = 28
PILL_WIDTH = 460
PILL_HEIGHT = 56


class _ZeroCursorStyle(QProxyStyle):
    """Suppress Qt's native blinking text cursor so only the glowing caret renders."""

    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PixelMetric.PM_TextCursorWidth:
            return 0
        opt = QStyleOption() if option is None else option
        if widget is None:
            return super().pixelMetric(metric, opt)
        return super().pixelMetric(metric, opt, widget)


class _GlowLineEdit(QLineEdit):
    """Borderless transparent QLineEdit with a warm Apple-style luminous glowing caret."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._offscreen = QGuiApplication.platformName() == 'offscreen'
        self.theme = 'dark'
        self._pulse_epoch = time.monotonic()
        self._caret_color = QColor('#FFFFFF')

        self.setObjectName('searchInput')
        self.setPlaceholderText('Search or Ask')
        self.setClearButtonEnabled(False)
        self.setFrame(False)
        self.setFixedHeight(PILL_HEIGHT - 12)

        self._cursor_style = _ZeroCursorStyle()
        self._cursor_style.setParent(self)
        self.setStyle(self._cursor_style)

        self._base_style = (
            'QLineEdit, QLineEdit:focus {'
            ' border: 0;'
            ' outline: none;'
            ' background: transparent;'
            ' padding: 0px 4px;'
            ' font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;'
            ' font-size: 16px;'
            ' font-weight: 450;'
            '}'
        )

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(24)
        self._pulse_timer.timeout.connect(self._on_pulse_tick)

        self.cursorPositionChanged.connect(self._schedule_glow_repaint)
        self.textChanged.connect(self._schedule_glow_repaint)
        self.selectionChanged.connect(self._schedule_glow_repaint)

        self.set_theme('dark')

    def _is_static_mode(self) -> bool:
        if self._offscreen:
            return True
        popup = self.parentWidget()
        hud = getattr(popup, 'hud', None)
        return bool(getattr(hud, 'preview_mode', False))

    def start_pulse_if_needed(self):
        if self.isVisible() and not self._is_static_mode():
            if not self._pulse_timer.isActive():
                self._pulse_epoch = time.monotonic()
                self._pulse_timer.start()
        else:
            self._pulse_timer.stop()

    def stop_pulse(self):
        if self._pulse_timer.isActive():
            self._pulse_timer.stop()

    def _on_pulse_tick(self):
        if not self.isVisible() or self._is_static_mode():
            self.stop_pulse()
            return
        self._schedule_glow_repaint()

    def _schedule_glow_repaint(self, *_args):
        self.update()
        parent = self.parentWidget()
        if parent is not None:
            parent.update()

    def set_theme(self, theme: str):
        self.theme = 'light' if theme == 'light' else 'dark'
        if self.theme == 'dark':
            text_color = QColor('#F5F7FA')
            placeholder_color = QColor('#A9B1BC')
            sel_bg = 'rgba(110, 148, 255, 118)'
            sel_fg = '#FFFFFF'
            self._caret_color = QColor('#FFFFFF')
        else:
            text_color = QColor('#141518')
            placeholder_color = QColor('#727A86')
            sel_bg = 'rgba(37, 99, 235, 64)'
            sel_fg = '#141518'
            self._caret_color = QColor('#1C1E24')

        # Omit `color:` in QSS so Qt preserves explicit QPalette.PlaceholderText & Text roles.
        self.setStyleSheet(
            f'{self._base_style}'
            f' QLineEdit {{ selection-background-color: {sel_bg}; selection-color: {sel_fg}; }}'
        )

        pal = self.palette()
        transparent = QColor(0, 0, 0, 0)
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            pal.setColor(group, QPalette.ColorRole.Text, text_color)
            pal.setColor(group, QPalette.ColorRole.WindowText, text_color)
            pal.setColor(group, QPalette.ColorRole.PlaceholderText, placeholder_color)
            pal.setColor(group, QPalette.ColorRole.Base, transparent)
            pal.setColor(group, QPalette.ColorRole.Window, transparent)
        self.setPalette(pal)
        self._schedule_glow_repaint()

    def should_paint_caret(self) -> bool:
        return self.isEnabled() and not self.isReadOnly() and not self.hasSelectedText()

    def caret_metrics(self):
        """Return (cx, cy, caret_height) in local _GlowLineEdit coordinates."""
        caret_h = 22.0
        cy = self.height() / 2.0
        rect = self.cursorRect()
        if not rect.isNull() and rect.height() > 0:
            # Qt's QLineEdit::cursorRect() pads symmetrically around integer cursor X
            # with an odd width (e.g. w=9 -> center().x() == x + 4).
            cx = float(rect.center().x())
            if not self.text() or self.cursorPosition() == 0:
                cx -= 0.5
            if not self.text() and cx < 1.5:
                cx = float(self.contentsRect().left() + self.textMargins().left() + 4.5)
        else:
            cx = float(self.contentsRect().left() + self.textMargins().left() + 4.5)
        max_x = max(2.0, float(self.width()) - 2.0)
        cx = max(2.0, min(cx, max_x))
        return cx, cy, caret_h

    def _pulse_factors(self):
        if self._is_static_mode() or not self._pulse_timer.isActive():
            return 1.0, 1.0
        phase = (time.monotonic() - self._pulse_epoch) * (2.0 * math.pi / 2.2)
        wave = 0.5 + 0.5 * math.sin(phase)
        return 0.84 + 0.16 * wave, 0.94 + 0.08 * wave

    def paint_caret_bloom(
        self,
        painter: QPainter,
        offset: QPointF = QPointF(0.0, 0.0),
        *,
        alpha_scale: float = 1.0,
        include_bar: bool = False,
    ):
        if not self.should_paint_caret():
            return
        cx, cy, caret_h = self.caret_metrics()
        gx = cx + offset.x()
        gy = cy + offset.y()
        alpha_mult, radius_mult = self._pulse_factors()
        am = alpha_mult * alpha_scale

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        # 1. Outer warm-amber halo in dark mode; soft sky-pearl halo in light mode
        outer_r = 19.0 * radius_mult
        outer_cy = gy + (1.8 if self.theme == 'dark' else 0.0)
        outer = QRadialGradient(QPointF(gx, outer_cy), outer_r)
        if self.theme == 'dark':
            outer.setColorAt(0.00, QColor(255, 188, 120, min(255, int(round(95 * am)))))
            outer.setColorAt(0.46, QColor(255, 162, 90, min(255, int(round(28 * am)))))
            outer.setColorAt(1.00, QColor(255, 152, 82, 0))
        else:
            outer.setColorAt(0.00, QColor(96, 152, 255, min(255, int(round(64 * am)))))
            outer.setColorAt(0.48, QColor(135, 180, 255, min(255, int(round(22 * am)))))
            outer.setColorAt(1.00, QColor(135, 180, 255, 0))
        painter.setBrush(outer)
        painter.drawEllipse(QPointF(gx, outer_cy), outer_r, outer_r)

        # 2. Vertical luminous wrap along the full 22 px caret shaft
        painter.save()
        painter.translate(gx, gy)
        rx_wrap = 5.0 * radius_mult
        ry_wrap = (caret_h * 0.62) * radius_mult
        painter.scale(rx_wrap, ry_wrap)
        wrap = QRadialGradient(QPointF(0.0, 0.0), 1.0)
        if self.theme == 'dark':
            wrap.setColorAt(0.0, QColor(255, 245, 228, min(255, int(round(88 * am)))))
            wrap.setColorAt(0.55, QColor(255, 214, 170, min(255, int(round(30 * am)))))
            wrap.setColorAt(1.0, QColor(255, 200, 150, 0))
        else:
            wrap.setColorAt(0.0, QColor(255, 255, 255, min(255, int(round(165 * am)))))
            wrap.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(wrap)
        painter.drawEllipse(QPointF(0.0, 0.0), 1.0, 1.0)
        painter.restore()

        # 3. Inner white-gold core (radius ~8.5 px)
        inner_r = 8.5 * radius_mult
        inner_cy = gy + (0.8 if self.theme == 'dark' else 0.0)
        inner = QRadialGradient(QPointF(gx, inner_cy), inner_r)
        if self.theme == 'dark':
            inner.setColorAt(0.00, QColor(255, 248, 232, min(255, int(round(185 * am)))))
            inner.setColorAt(0.48, QColor(255, 222, 180, min(255, int(round(68 * am)))))
            inner.setColorAt(1.00, QColor(255, 205, 155, 0))
        else:
            inner.setColorAt(0.00, QColor(255, 255, 255, min(255, int(round(210 * am)))))
            inner.setColorAt(0.52, QColor(112, 166, 255, min(255, int(round(44 * am)))))
            inner.setColorAt(1.00, QColor(112, 166, 255, 0))
        painter.setBrush(inner)
        painter.drawEllipse(QPointF(gx, inner_cy), inner_r, inner_r)

        # 4. Crisp 2.0 px × ~22 px vertical caret bar
        if include_bar:
            bar_w = 2.0
            bar_rect = QRectF(gx - bar_w / 2.0, gy - caret_h / 2.0, bar_w, caret_h)
            painter.setBrush(self._caret_color)
            painter.drawRoundedRect(bar_rect, 1.0, 1.0)

        painter.restore()

    def paint_caret_bar_overlay(self, painter: QPainter):
        """Draw tight sub-4px inner kiss + crisp 2.0 px caret bar cleanly over glyphs."""
        if not self.should_paint_caret():
            return
        cx, cy, caret_h = self.caret_metrics()
        alpha_mult, _ = self._pulse_factors()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        kiss_r = 4.0
        kiss = QRadialGradient(QPointF(cx, cy), kiss_r)
        if self.theme == 'dark':
            kiss.setColorAt(0.0, QColor(255, 248, 234, min(255, int(round(58 * alpha_mult)))))
            kiss.setColorAt(1.0, QColor(255, 232, 200, 0))
        else:
            kiss.setColorAt(0.0, QColor(255, 255, 255, min(255, int(round(75 * alpha_mult)))))
            kiss.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(kiss)
        painter.drawEllipse(QPointF(cx, cy), kiss_r, kiss_r)

        bar_w = 2.0
        bar_rect = QRectF(cx - bar_w / 2.0, cy - caret_h / 2.0, bar_w, caret_h)
        painter.setBrush(self._caret_color)
        painter.drawRoundedRect(bar_rect, 1.0, 1.0)
        painter.restore()

    def showEvent(self, event):
        super().showEvent(event)
        self.start_pulse_if_needed()
        self._schedule_glow_repaint()

    def hideEvent(self, event):
        self.stop_pulse()
        super().hideEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._schedule_glow_repaint()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._schedule_glow_repaint()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self._schedule_glow_repaint()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._schedule_glow_repaint()

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        self._schedule_glow_repaint()

    def paintEvent(self, event):
        if self.should_paint_caret():
            p = QPainter(self)
            # Full dual-layer radial glow behind text (matches parent overflow 1:1)
            self.paint_caret_bloom(p, alpha_scale=1.0, include_bar=False)
            p.end()

        super().paintEvent(event)

        if self.should_paint_caret():
            p = QPainter(self)
            self.paint_caret_bar_overlay(p)
            p.end()


class _MicrophoneButton(QToolButton):
    """34×34 circular button with an Apple SF-Symbols-inspired outline microphone glyph."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme = 'dark'
        self._hovered = False
        self.setObjectName('microphoneButton')
        self.setAccessibleName('Start voice listening')
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setStyleSheet('QToolButton { border: 0; background: transparent; padding: 0px; }')
        self.set_theme('dark')

    def set_theme(self, theme: str):
        self.theme = 'light' if theme == 'light' else 'dark'
        if self.theme == 'dark':
            self._rest_color = QColor('#A7AFBA')
            self._hover_color = QColor('#F5F7FA')
            self._hover_bg = QColor(255, 255, 255, 28)   # ~0.11
            self._press_bg = QColor(255, 255, 255, 42)
        else:
            self._rest_color = QColor('#5C6470')
            self._hover_color = QColor('#141518')
            self._hover_bg = QColor(20, 24, 32, 20)      # ~0.08
            self._press_bg = QColor(20, 24, 32, 32)
        self.update()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        hovered = self._hovered or self.underMouse()
        if self.isDown():
            p.setPen(Qt.NoPen)
            p.setBrush(self._press_bg)
            p.drawEllipse(QRectF(0.5, 0.5, 33.0, 33.0))
        elif hovered:
            p.setPen(Qt.NoPen)
            p.setBrush(self._hover_bg)
            p.drawEllipse(QRectF(0.5, 0.5, 33.0, 33.0))

        glyph_color = self._hover_color if (hovered or self.isDown()) else self._rest_color
        pen = QPen(glyph_color, 1.65, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)

        # Centered at (17.0, 17.0): total height y=8.5..25.5 (midpoint 17.0)
        # 1. Slim rounded capsule microphone head
        p.drawRoundedRect(QRectF(14.2, 8.5, 5.6, 10.2), 2.8, 2.8)

        # 2. Balanced U-shaped pickup cradle with open vertical sides
        cradle = QPainterPath()
        cradle.moveTo(11.7, 14.3)
        cradle.lineTo(11.7, 16.1)
        cradle.arcTo(QRectF(11.7, 10.8, 10.6, 10.6), 180.0, 180.0)
        cradle.lineTo(22.3, 14.3)
        p.drawPath(cradle)

        # 3. Clean vertical stem and subtle rounded horizontal base
        p.drawLine(QPointF(17.0, 21.4), QPointF(17.0, 25.5))
        p.drawLine(QPointF(14.2, 25.5), QPointF(19.8, 25.5))
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

        self.input = _GlowLineEdit(self)
        self.input.installEventFilter(self)
        self.microphone = _MicrophoneButton(self)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(PAD_X + 24, PAD_TOP + 6, PAD_X + 14, PAD_BOTTOM + 6)
        layout.setSpacing(8)
        layout.addWidget(self.input, 1, Qt.AlignVCenter)
        layout.addWidget(self.microphone, 0, Qt.AlignVCenter)

        self.input.returnPressed.connect(self._submit)
        self.microphone.clicked.connect(self._microphone)

        self._geometry_animation = QPropertyAnimation(self, b'geometry', self)
        self._geometry_animation.setDuration(190)
        self._geometry_animation.setEasingCurve(QEasingCurve.OutCubic)

        self._opacity_animation = QPropertyAnimation(self, b'windowOpacity', self)
        self._opacity_animation.setDuration(190)
        self._opacity_animation.setEasingCurve(QEasingCurve.OutCubic)

        self.set_theme('dark')

    def set_theme(self, theme):
        self.theme = theme if theme in ('dark', 'light') else 'dark'
        self.input.set_theme(self.theme)
        self.microphone.set_theme(self.theme)
        self.update()

    def open_below(self, hud=None, immediate=False):
        if hud is not None:
            self.hud = hud
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
        self._geometry_animation.stop()
        self._opacity_animation.stop()

        animate = not (immediate or offscreen)
        if offscreen:
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        elif animate:
            self.setWindowOpacity(0.0)
            self.setGeometry(target.translated(0, -8))
        else:
            self.setWindowOpacity(1.0)
            self.setGeometry(target)

        self.show()
        if not offscreen:
            self.raise_()
            self.activateWindow()
            self.input.setFocus(Qt.PopupFocusReason)
        self.input.selectAll()
        self.input.start_pulse_if_needed()

        if not animate:
            self.setGeometry(target)
        else:
            self._geometry_animation.setStartValue(target.translated(0, -8))
            self._geometry_animation.setEndValue(target)
            self._opacity_animation.setStartValue(0.0)
            self._opacity_animation.setEndValue(1.0)
            self._geometry_animation.start()
            self._opacity_animation.start()

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
        self.input.stop_pulse()
        self._geometry_animation.stop()
        self._opacity_animation.stop()
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self.hide()
        self._closing = False
        self.dismissed.emit()

    def hideEvent(self, event):
        self.input.stop_pulse()
        self._geometry_animation.stop()
        self._opacity_animation.stop()
        super().hideEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.dismiss()
            event.accept()
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.dismiss()
            event.accept()
            return
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
        radius = PILL_HEIGHT / 2.0
        pill_path = QPainterPath()
        pill_path.addRoundedRect(pill, radius, radius)

        # 1. Soft multi-pass ambient drop shadow shifted slightly downward (+4..+7 px),
        #    with the pill interior subtracted so glass translucency stays pure.
        p.save()
        shadow_cutout = QPainterPath()
        shadow_cutout.addRect(QRectF(self.rect()))
        inset_path = QPainterPath()
        inset_pill = pill.adjusted(1.5, 1.5, -1.5, -1.5)
        inset_path.addRoundedRect(inset_pill, inset_pill.height() / 2.0, inset_pill.height() / 2.0)
        p.setClipPath(shadow_cutout.subtracted(inset_path), Qt.IntersectClip)
        p.setPen(Qt.NoPen)

        steps = 16
        peak_alpha = 13.0 if self.theme == 'dark' else 8.5
        shadow_rgb = (0, 0, 0) if self.theme == 'dark' else (14, 18, 28)
        for step in range(steps, 0, -1):
            t = step / float(steps)
            sx = 19.5 * t
            sy_top = 9.5 * t
            sy_bot = 15.5 * t
            y_shift = 3.5 + 3.5 * t  # +3.5..+7.0 px downward shift
            s_rect = pill.adjusted(-sx, y_shift - sy_top, sx, y_shift + sy_bot)
            alpha = max(1, int(round(peak_alpha * ((1.0 - t * 0.94) ** 2.15))))
            p.setBrush(QColor(shadow_rgb[0], shadow_rgb[1], shadow_rgb[2], alpha))
            s_rad = s_rect.height() / 2.0
            p.drawRoundedRect(s_rect, s_rad, s_rad)
        p.restore()

        # 2. Smoked / Frosted Liquid-Glass Interior (strictly clipped to stadium capsule)
        p.save()
        p.setClipPath(pill_path, Qt.IntersectClip)
        p.setPen(Qt.NoPen)

        body_grad = QLinearGradient(0.0, pill.top(), 0.0, pill.bottom())
        if self.theme == 'dark':
            body_grad.setColorAt(0.00, QColor(6, 7, 10, 246))      # #06070A
            body_grad.setColorAt(0.38, QColor(13, 16, 21, 240))    # #0D1015
            body_grad.setColorAt(0.78, QColor(34, 40, 49, 224))    # #222831
            body_grad.setColorAt(1.00, QColor(58, 67, 77, 212))    # #3A434D
        else:
            body_grad.setColorAt(0.00, QColor(252, 253, 255, 245))
            body_grad.setColorAt(0.55, QColor(244, 247, 252, 239))
            body_grad.setColorAt(1.00, QColor(235, 240, 247, 232))
        p.fillRect(pill, body_grad)

        # 3. Subtle radial bottom-edge ambient light diffusion wash
        p.save()
        p.translate(pill.center().x(), pill.bottom() + 4.0)
        p.scale(PILL_WIDTH * 0.50, PILL_HEIGHT * 0.62)
        bottom_wash = QRadialGradient(QPointF(0.0, 0.0), 1.0)
        if self.theme == 'dark':
            bottom_wash.setColorAt(0.00, QColor(214, 224, 236, 32))
            bottom_wash.setColorAt(0.55, QColor(196, 208, 222, 14))
            bottom_wash.setColorAt(1.00, QColor(196, 208, 222, 0))
        else:
            bottom_wash.setColorAt(0.00, QColor(255, 255, 255, 72))
            bottom_wash.setColorAt(0.60, QColor(255, 255, 255, 24))
            bottom_wash.setColorAt(1.00, QColor(255, 255, 255, 0))
        p.setBrush(bottom_wash)
        p.drawEllipse(QPointF(0.0, 0.0), 1.0, 1.0)
        p.restore()

        # 4. Subtle top inner vignette / depth reflection over top 8 px (no harsh glossy bar)
        top_depth = QLinearGradient(0.0, pill.top(), 0.0, pill.top() + 8.0)
        if self.theme == 'dark':
            top_depth.setColorAt(0.0, QColor(255, 255, 255, 16))
            top_depth.setColorAt(1.0, QColor(255, 255, 255, 0))
        else:
            top_depth.setColorAt(0.0, QColor(255, 255, 255, 84))
            top_depth.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(QRectF(pill.left(), pill.top(), pill.width(), 8.0), top_depth)

        # 5. Seamless caret radial halo overflow outside self.input's child rect
        #    so the left half of the 19 px glow never clips against self.input.x().
        if self.input.should_paint_caret():
            p.save()
            outside_input = QRegion(self.rect()) - QRegion(self.input.geometry())
            p.setClipRegion(outside_input, Qt.IntersectClip)
            self.input.paint_caret_bloom(
                p,
                QPointF(self.input.pos()),
                alpha_scale=1.0,
                include_bar=False,
            )
            p.restore()

        p.restore()

        # 6. Crisp 1.1 px outer specular glass rim
        rim_rect = pill.adjusted(0.55, 0.55, -0.55, -0.55)
        rim_grad = QLinearGradient(0.0, pill.top(), 0.0, pill.bottom())
        if self.theme == 'dark':
            rim_grad.setColorAt(0.00, QColor(255, 255, 255, 26))
            rim_grad.setColorAt(0.50, QColor(255, 255, 255, 42))
            rim_grad.setColorAt(1.00, QColor(255, 255, 255, 78))
        else:
            rim_grad.setColorAt(0.00, QColor(20, 24, 32, 26))
            rim_grad.setColorAt(0.50, QColor(20, 24, 32, 34))
            rim_grad.setColorAt(1.00, QColor(20, 24, 32, 42))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(rim_grad, 1.1))
        rim_radius = rim_rect.height() / 2.0
        p.drawRoundedRect(rim_rect, rim_radius, rim_radius)
        p.end()
