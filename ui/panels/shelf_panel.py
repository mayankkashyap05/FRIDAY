from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QLineEdit
from .common import Panel

class ShelfPanel(Panel):
    def __init__(self, services):
        super().__init__(services, 'Shelf · drop files onto the island')
        self.files = QListWidget(); self.layout.addWidget(self.files)
        self.question = QLineEdit(); self.question.setPlaceholderText('Question about selected file'); self.layout.addWidget(self.question)
        self.buttons([('Preview', self.preview), ('Ask', self.ask), ('Remove', self.remove)])
        self.layout.addWidget(self.result)
    def add_files(self, paths):
        existing = {self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())}
        for path in paths:
            p = Path(path)
            if not p.is_file() or str(p) in existing: continue
            try: size = p.stat().st_size
            except OSError: continue
            item = QListWidgetItem(f'{p.name} · {size:,} bytes'); item.setData(Qt.UserRole, str(p)); self.files.addItem(item); existing.add(str(p))
            self.files.setCurrentItem(item)
    def _selected(self):
        item = self.files.currentItem()
        if not item: self.result.setText('Select a file first.'); return None
        return item.data(Qt.UserRole)
    def remove(self):
        row = self.files.currentRow()
        if row >= 0: self.files.takeItem(row)
    def preview(self):
        path = self._selected()
        if not path: return
        pdf = path.lower().endswith('.pdf')
        self.services.call_action('actions.pdf_reader' if pdf else 'actions.document_qa', 'read_pdf' if pdf else 'read_document', {'path': path, 'source': path}, self.finish)
    def ask(self):
        path = self._selected(); question = self.question.text().strip()
        if not path: return
        if not question: self.result.setText('Enter a question first.'); return
        pdf = path.lower().endswith('.pdf')
        self.services.call_action('actions.pdf_reader' if pdf else 'actions.document_qa', 'pdf_qa' if pdf else 'document_qa', {'path': path, 'source': path, 'question': question}, self.finish)
