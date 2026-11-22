"""Non-blocking backend calls and common panel widgets."""
import importlib
import logging
import threading
import time
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QPushButton

log = logging.getLogger(__name__)


class PanelServices:
    def __init__(self, dispatcher, player=None):
        self.dispatcher, self.player = dispatcher, player
        self._closed = threading.Event()
        self._slots = threading.BoundedSemaphore(8)
        self._lock = threading.Lock()
        self._workers = set()

    def close(self, timeout=2.0):
        """Reject new work and wait up to a shared deadline for running work.

        Python threads cannot safely be killed. Late workers remain detached and
        cannot dispatch results after close; callers can inspect the return value.
        """
        with self._lock:
            self._closed.set()
            workers = tuple(self._workers)
        deadline = time.monotonic() + timeout
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            return not self._workers

    def run(self, fn, done):
        with self._lock:
            if self._closed.is_set():
                return False
            if not self._slots.acquire(blocking=False):
                self.dispatcher.call(done, None, 'Too many operations are running.')
                return False

            def worker():
                try:
                    try:
                        result, error = fn(), None
                    except Exception as exc:
                        log.exception('FRIDAY panel action failed')
                        result, error = None, str(exc)
                    with self._lock:
                        if not self._closed.is_set():
                            self.dispatcher.call(done, result, error)
                finally:
                    self._slots.release()
                    with self._lock:
                        self._workers.discard(threading.current_thread())

            thread = threading.Thread(target=worker, daemon=True, name='FridayPanelAction')
            self._workers.add(thread)
            try:
                thread.start()
            except Exception:
                self._workers.remove(thread)
                self._slots.release()
                raise
        return True

    def call_action(self, module, name, params, done):
        def invoke():
            fn = getattr(importlib.import_module(module), name)
            return fn(params, player=self.player)
        return self.run(invoke, done)


class Panel(QWidget):
    def __init__(self, services, title):
        super().__init__()
        self.services = services
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(8, 4, 8, 4)
        self.layout.setSpacing(5)
        label = QLabel(title); label.setObjectName('panelTitle'); self.layout.addWidget(label)
        self.result = QLabel(''); self.result.setWordWrap(True); self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)

    def finish(self, result, error):
        self.result.setText(str(error if error else result or 'Done'))

    def buttons(self, items):
        row = QHBoxLayout()
        for text, callback in items:
            button = QPushButton(text); button.clicked.connect(callback); row.addWidget(button)
        self.layout.addLayout(row)
        return row
