from PySide6.QtCore import Qt, QRectF, QPointF, QSize
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPen, QColor, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QFrame, QProgressBar, QPushButton
from .common import Panel


def transport_icon(action, colour='#F5F6F8'):
    pixmap = QPixmap(24, 24); pixmap.fill(Qt.transparent)
    p = QPainter(pixmap); p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(colour), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)); p.setBrush(Qt.NoBrush)
    if action in ('prev', 'next', 'toggle'):
        if action == 'toggle':
            path = QPainterPath(QPointF(8, 5)); path.lineTo(18, 12); path.lineTo(8, 19); path.closeSubpath(); p.drawPath(path)
        else:
            flip = action == 'prev'; p.save()
            if flip: p.translate(24, 0); p.scale(-1, 1)
            path = QPainterPath(QPointF(5, 6)); path.lineTo(14, 12); path.lineTo(5, 18); path.closeSubpath(); p.drawPath(path)
            p.drawLine(18, 6, 18, 18); p.restore()
    else:
        path = QPainterPath(QPointF(4, 9)); path.lineTo(8, 9); path.lineTo(12, 5); path.lineTo(12, 19); path.lineTo(8, 15); path.lineTo(4, 15); path.closeSubpath(); p.drawPath(path)
        if action == 'mute': p.drawLine(15, 8, 20, 16); p.drawLine(20, 8, 15, 16)
        else:
            p.drawLine(15, 12, 21, 12)
            if action == 'volume_up': p.drawLine(18, 9, 18, 15)
    p.end(); return QIcon(pixmap)


class MediaPanel(Panel):
    def __init__(self, services):
        super().__init__(services, 'Media')
        split = QHBoxLayout(); split.setSpacing(10); self.layout.addLayout(split)
        art = QLabel('♫'); art.setAlignment(Qt.AlignCenter); art.setFixedSize(62, 62)
        art.setStyleSheet('background: #1D1F24; border-radius: 12px; color: #8E939C; font-size: 20pt;')
        split.addWidget(art)
        main = QVBoxLayout(); split.addLayout(main, 1)
        title = QLabel('Media controls'); title.setObjectName('panelTitle'); main.addWidget(title)
        subtitle = QLabel('Track details unavailable'); subtitle.setStyleSheet('color: #8E939C;'); main.addWidget(subtitle)
        self.progress = QProgressBar(); self.progress.setRange(0, 1); self.progress.setValue(0); self.progress.setTextVisible(False); self.progress.setFixedHeight(5)
        self.progress.setToolTip('Playback position is not provided by the media backend'); main.addWidget(self.progress)
        times = QLabel('—:—                                  —:—'); times.setStyleSheet('color: #8E939C;'); main.addWidget(times)
        divider = QFrame(); divider.setFrameShape(QFrame.VLine); divider.setStyleSheet('color: #303238;'); split.addWidget(divider)
        context = QVBoxLayout(); split.addLayout(context)
        label = QLabel('Playback'); label.setObjectName('panelTitle'); context.addWidget(label)
        self.result.setText('Use system media keys'); context.addWidget(self.result); context.addStretch()
        controls = QHBoxLayout(); self.layout.addLayout(controls)
        self._controls = []
        for label, action in (('Previous', 'prev'), ('Play / Pause', 'toggle'), ('Next', 'next'), ('Volume −', 'volume_down'), ('Volume +', 'volume_up'), ('Mute', 'mute')):
            button = QPushButton(); button.setIcon(transport_icon(action)); button.setIconSize(QSize(20, 20)); button.setToolTip(label); button.setAccessibleName(label)
            button.clicked.connect(lambda checked=False, action=action: self.control(action)); controls.addWidget(button)
            self._controls.append((button, action))

    def set_theme(self, theme):
        colour = '#141518' if theme == 'light' else '#F5F6F8'
        for button, action in self._controls:
            button.setIcon(transport_icon(action, colour))

    def control(self, action):
        self.services.call_action('actions.media_control', 'media_control_action', {'action': action}, self.finish)
