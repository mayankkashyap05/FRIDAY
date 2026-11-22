from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLineEdit
from .common import Panel

class CommandBar(Panel):
    submitted = Signal(str)
    mode_requested = Signal(str)
    def __init__(self, services):
        super().__init__(services, 'Command')
        self.input = QLineEdit(); self.input.setPlaceholderText('Ask FRIDAY...')
        self.input.returnPressed.connect(self.submit); self.layout.addWidget(self.input)
        self.buttons([('Send', self.submit), ('Listen', lambda: self.mode_requested.emit('listening')),
                      ('Dictate', lambda: self.mode_requested.emit('dictation'))])
        self.layout.addWidget(self.result)
    def submit(self):
        text = self.input.text().strip()
        if text:
            self.input.clear(); self.submitted.emit(text)
