from PySide6.QtCore import QDateTime, Qt, QRectF, QSize
from PySide6.QtGui import QColor, QPen, QPainter
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QDateTimeEdit, QLineEdit, QStyledItemDelegate, QStyle
from .common import Panel


class TaskRow(QStyledItemDelegate):
    """Read-only task status; the backend does not expose manual completion."""
    def sizeHint(self, option, index):
        return QSize(220, 32)

    def paint(self, painter, option, index):
        painter.save(); painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect).adjusted(2, 1, -2, -1)
        done = index.data(Qt.UserRole + 1) == 'completed'
        if done or option.state & QStyle.State_Selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(52, 199, 89, 36) if done else QColor(56, 130, 246, 45))
            painter.drawRoundedRect(rect, 9, 9)
        painter.setPen(QPen(QColor('#34C759' if done else '#8E939C'), 1.5))
        painter.setBrush(QColor('#34C759') if done else Qt.NoBrush)
        painter.drawEllipse(QRectF(rect.left()+8, rect.center().y()-6, 12, 12))
        if done:
            painter.setPen(QPen(QColor('#141518'), 1.4)); x, y = rect.left()+11, rect.center().y()
            painter.drawLine(int(x), int(y), int(x+2), int(y+2)); painter.drawLine(int(x+2), int(y+2), int(x+6), int(y-3))
        font = painter.font(); font.setStrikeOut(done); painter.setFont(font)
        painter.setPen(option.palette.color(option.palette.ColorRole.Text))
        painter.drawText(rect.adjusted(27, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft,
                         painter.fontMetrics().elidedText(str(index.data()), Qt.ElideRight, max(0, int(rect.width()-34))))
        painter.restore()


class TasksPanel(Panel):
    def __init__(self, services):
        super().__init__(services, 'Tasks & reminders')
        self.progress_text = '0/0'
        self.tasks = QListWidget(); self.tasks.setItemDelegate(TaskRow(self.tasks)); self.layout.addWidget(self.tasks)
        self.buttons([('Refresh', self.refresh), ('Cancel selected', self.cancel)])
        self.when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600)); self.when.setDisplayFormat('yyyy-MM-dd HH:mm'); self.layout.addWidget(self.when)
        self.message = QLineEdit(); self.message.setPlaceholderText('Reminder message'); self.layout.addWidget(self.message)
        self.buttons([('Set reminder', self.remind)]); self.layout.addWidget(self.result)

    def refresh(self):
        def task():
            from agent.task_queue import get_queue
            return get_queue().get_all_statuses()
        def done(result, error):
            if error: self.finish(result, error); return
            self.tasks.clear()
            for item in result:
                row = QListWidgetItem(f"{item['goal']}")
                row.setToolTip(str(item['status']))
                row.setData(Qt.UserRole, item['task_id']); row.setData(Qt.UserRole + 1, item['status'])
                self.tasks.addItem(row)
            self.progress_text = f"{sum(item['status'] == 'completed' for item in result)}/{len(result)}"
            self.result.setText(f'{len(result)} tasks')
            self.window().update()
        self.services.run(task, done)

    def cancel(self):
        item = self.tasks.currentItem()
        if not item: self.result.setText('Select a task first.'); return
        task_id = item.data(Qt.UserRole)
        def task():
            from agent.task_queue import get_queue
            return 'Task cancelled.' if get_queue().cancel(task_id) else 'Task could not be cancelled.'
        self.services.run(task, self.finish)

    def remind(self):
        msg = self.message.text().strip()
        if not msg: self.result.setText('Enter a reminder message.'); return
        dt = self.when.dateTime()
        params = {'date': dt.toString('yyyy-MM-dd'), 'time': dt.toString('HH:mm'), 'message': msg}
        self.services.call_action('actions.reminder', 'reminder', params, self.finish)
