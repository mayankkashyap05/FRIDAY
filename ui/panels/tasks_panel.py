from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QListWidget, QDateTimeEdit, QLineEdit
from .common import Panel

class TasksPanel(Panel):
    def __init__(self, services):
        super().__init__(services, 'Tasks & reminders')
        self.tasks = QListWidget(); self.layout.addWidget(self.tasks)
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
                from PySide6.QtWidgets import QListWidgetItem
                from PySide6.QtCore import Qt
                row = QListWidgetItem(f"{item['status']} · {item['goal']}")
                row.setData(Qt.UserRole, item['task_id']); self.tasks.addItem(row)
            self.result.setText(f'{len(result)} tasks')
        self.services.run(task, done)
    def cancel(self):
        item = self.tasks.currentItem()
        if not item: self.result.setText('Select a task first.'); return
        from PySide6.QtCore import Qt
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
