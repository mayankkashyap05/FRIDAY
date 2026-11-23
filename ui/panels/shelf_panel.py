from pathlib import Path
from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QColor, QPen, QPainterPath, QPainter
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QLineEdit, QLabel, QHBoxLayout, QStyledItemDelegate, QStyle, QListView
from .common import Panel


def human_size(size):
    if size < 1024: return f'{size} B'
    if size < 1024 * 1024: return f'{size/1024:.1f} KB'
    return f'{size/(1024*1024):.1f} MB'


class FileCard(QStyledItemDelegate):
    def sizeHint(self, option, index): return QSize(124, 96)

    def paint(self, painter, option, index):
        painter.save(); painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect).adjusted(3, 3, -3, -3)
        painter.setPen(QPen(QColor('#3882F6'), 1) if option.state & QStyle.State_Selected else Qt.NoPen)
        painter.setBrush(QColor('#1D1F24')); painter.drawRoundedRect(rect, 12, 12)
        x, y = rect.left()+10, rect.top()+9
        page = QPainterPath(); page.moveTo(x, y); page.lineTo(x+20, y); page.lineTo(x+27, y+7); page.lineTo(x+27, y+33); page.lineTo(x, y+33); page.closeSubpath()
        painter.setPen(Qt.NoPen); painter.setBrush(QColor('#F5F6F8')); painter.drawPath(page)
        painter.setPen(QColor('#62666D')); painter.drawLine(int(x+20), int(y), int(x+20), int(y+7)); painter.drawLine(int(x+20), int(y+7), int(x+27), int(y+7))
        font = painter.font(); font.setPointSize(6); font.setBold(True); painter.setFont(font); painter.setPen(QColor('#141518'))
        painter.drawText(QRectF(x, y+16, 27, 12), Qt.AlignCenter, str(index.data(Qt.UserRole+2))[:5])
        font.setPointSize(9); painter.setFont(font); painter.setPen(QColor('#F5F6F8'))
        painter.drawText(QRectF(x, y+39, rect.width()-20, 18), Qt.AlignLeft | Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(str(index.data()), Qt.ElideRight, int(rect.width()-20)))
        font.setPointSize(8); font.setBold(False); painter.setFont(font); painter.setPen(QColor('#8E939C'))
        painter.drawText(QRectF(x, y+59, rect.width()-20, 16), Qt.AlignLeft | Qt.AlignVCenter, str(index.data(Qt.UserRole+1)))
        painter.restore()


class ShelfPanel(Panel):
    def __init__(self, services):
        super().__init__(services, 'Shelf')
        row = QHBoxLayout(); self.layout.addLayout(row)
        self.files = QListWidget(); self.files.setViewMode(QListView.IconMode); self.files.setFlow(QListView.LeftToRight)
        self.files.setWrapping(False); self.files.setResizeMode(QListView.Adjust); self.files.setMovement(QListView.Static)
        self.files.setItemDelegate(FileCard(self.files)); self.files.setMinimumHeight(100); row.addWidget(self.files, 1)
        drop = QLabel('Drop a\nfile'); drop.setAlignment(Qt.AlignCenter); drop.setFixedSize(76, 88)
        drop.setStyleSheet('border: 1px dashed #8E939C; border-radius: 12px; color: #8E939C;')
        row.addWidget(drop)
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
            item = QListWidgetItem(p.name); item.setToolTip(str(p)); item.setData(Qt.UserRole, str(p))
            item.setData(Qt.UserRole+1, human_size(size)); item.setData(Qt.UserRole+2, p.suffix[1:].upper() or 'FILE')
            self.files.addItem(item); existing.add(str(p)); self.files.setCurrentItem(item)

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
