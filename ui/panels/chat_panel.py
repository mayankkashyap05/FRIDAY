from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPlainTextEdit, QLineEdit, QLabel, QWidget, QHBoxLayout, QPushButton
from .common import Panel

class ChatPanel(Panel):
    submitted = Signal(str)
    def __init__(self, services):
        super().__init__(services, 'Conversation')
        self.feed = QPlainTextEdit(); self.feed.setReadOnly(True); self.feed.setMaximumBlockCount(200); self.layout.addWidget(self.feed)
        self.transcript = QLabel(''); self.transcript.setWordWrap(True); self.layout.addWidget(self.transcript)
        self.input = QLineEdit(); self.input.setPlaceholderText('Message FRIDAY'); self.input.returnPressed.connect(self.submit); self.layout.addWidget(self.input)
        self.buttons([('Send', self.submit)])
        self._approval_callback = None
        self.approval = QWidget(); row = QHBoxLayout(self.approval); row.setContentsMargins(0, 0, 0, 0)
        self.approval_description = QPlainTextEdit(); self.approval_description.setReadOnly(True); self.approval_description.setMaximumHeight(85); row.addWidget(self.approval_description, 1)
        self.approve = QPushButton('Approve'); self.deny = QPushButton('Deny')
        self.approve.clicked.connect(lambda: self.answer(True)); self.deny.clicked.connect(lambda: self.answer(False))
        row.addWidget(self.approve); row.addWidget(self.deny); self.layout.addWidget(self.approval); self.approval.hide()
        self.layout.addWidget(self.result)
    def append_log(self, entry):
        self.feed.appendPlainText(str(getattr(entry, 'text', entry)))
    def submit(self):
        text = self.input.text().strip()
        if text: self.input.clear(); self.submitted.emit(text)

    def request_confirmation(self, description, callback):
        self._approval_callback = callback
        self.approval_description.setPlainText(str(description))
        self.approval.show()

    def answer(self, approved):
        callback, self._approval_callback = self._approval_callback, None
        self.approval.hide()
        if callback is not None:
            self.finish(callback(bool(approved)), None)
