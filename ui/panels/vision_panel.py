from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLineEdit, QLabel
from .common import Panel

class VisionPanel(Panel):
    def __init__(self, services):
        super().__init__(services, 'Vision')
        self.question = QLineEdit(); self.question.setPlaceholderText('What would you like to know?'); self.layout.addWidget(self.question)
        self.buttons([('Ask about screen', lambda: self.ask('screen')), ('Ask about camera', lambda: self.ask('camera'))])
        self.preview = QLabel(''); self.preview.setAlignment(Qt.AlignCenter); self.preview.setMaximumHeight(90)
        self.layout.addWidget(self.preview)
        self.layout.addWidget(self.result); self.layout.addStretch()
    def ask(self, angle):
        question = self.question.text().strip()
        if not question: self.result.setText('Enter a question first.'); return
        self.services.call_action('actions.screen_processor', 'screen_process', {'angle': angle, 'text': question}, self.finish)

    def set_frame(self, frame):
        try:
            height, width = frame.shape[:2]
            if len(frame.shape) != 3 or frame.shape[2] != 3:
                raise ValueError('Expected a three-channel BGR image')
            image = QImage(frame.data, width, height, frame.strides[0], QImage.Format_BGR888).copy()
            self.preview.setPixmap(QPixmap.fromImage(image).scaled(280, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except (AttributeError, TypeError, ValueError, BufferError) as exc:
            self.result.setText(f'Cannot display camera frame: {exc}')
