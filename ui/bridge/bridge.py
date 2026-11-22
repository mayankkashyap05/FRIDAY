"""Qt-thread dispatcher and model used by the native UI.

Producers may invoke every public bridge setter from any Python thread. The
model and its change signals are updated exclusively on the QObject's thread.
"""
from collections import deque
import logging
import math
import threading
from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt
from .protocol import UiState, classify_log

log = logging.getLogger(__name__)


class UiDispatcher(QObject):
    _wake = Signal()

    def __init__(self, max_pending=2048, parent=None):
        super().__init__(parent)
        if max_pending < 1:
            raise ValueError('max_pending must be positive')
        self._lock = threading.Lock()
        self._queue = deque()
        self._max_pending = max_pending
        self._scheduled = False
        self._stopped = False
        self.dropped = 0
        self._wake.connect(self._drain, Qt.ConnectionType.QueuedConnection)

    @property
    def on_ui_thread(self):
        return QThread.currentThread() == self.thread()

    def call(self, callback, *args, **kwargs):
        # Always queue, including calls from the owner, to preserve FIFO across
        # producers. Emitting a queued signal is safe before app.exec().
        with self._lock:
            if self._stopped:
                return False
            if len(self._queue) >= self._max_pending:
                self._queue.popleft()
                self.dropped += 1
            self._queue.append((callback, args, kwargs))
            wake = not self._scheduled
            self._scheduled = True
        if wake:
            self._wake.emit()
        return True

    @Slot()
    def _drain(self):
        if not self.on_ui_thread:
            raise RuntimeError('UI queue may only be drained on the owner thread')
        # Bound each event-loop turn so a burst cannot starve paint/input.
        for _ in range(256):
            with self._lock:
                if self._stopped or not self._queue:
                    self._scheduled = False
                    return
                callback, args, kwargs = self._queue.popleft()
            try:
                callback(*args, **kwargs)
            except Exception:
                log.exception('Dispatched UI callback failed')
        with self._lock:
            if not self._queue or self._stopped:
                self._scheduled = False
                return
        self._wake.emit()

    def pending(self):
        with self._lock:
            return len(self._queue)

    def stop(self):
        with self._lock:
            self._stopped = True
            self._queue.clear()
            self._scheduled = False


class UiBridge(QObject):
    state_changed = Signal(str)
    amplitude_changed = Signal(float)
    transcript_changed = Signal(str)
    log_added = Signal(object)
    connection_changed = Signal(bool, bool)
    muted_changed = Signal(bool)
    persona_changed = Signal(str, str, str)

    def __init__(self, dispatcher=None, parent=None):
        super().__init__(parent)
        self.dispatcher = dispatcher or UiDispatcher(parent=self)
        self.state = UiState.IDLE
        self.amplitude = 0.0
        self.live_transcript = ''
        self.logs = deque(maxlen=200)
        self.connected = False
        self.connecting = False
        self.muted = False
        self.persona = ('normal', 'calm', 'Aoede')

    def _on_owner(self, fn, *args):
        return self.dispatcher.call(fn, *args)

    def set_state(self, value):
        return self._on_owner(self._set_state, UiState.coerce(value))

    def _set_state(self, value):
        self.state = value
        self.state_changed.emit(value.value)

    def setAmplitude(self, value):
        try:
            value = float(value)
        except (ValueError, TypeError):
            value = 0.0
        if not math.isfinite(value):
            value = 0.0
        return self._on_owner(self._set_amplitude, max(0.0, min(value, 1.0)))

    def _set_amplitude(self, value):
        self.amplitude = value
        self.amplitude_changed.emit(value)

    def set_live_transcript(self, value):
        return self._on_owner(self._set_transcript, str(value or ''))

    def _set_transcript(self, value):
        self.live_transcript = value
        self.transcript_changed.emit(value)

    def ingest(self, value):
        if value is None:
            return False
        return self._on_owner(self._add_log, classify_log(value))

    def _add_log(self, entry):
        self.logs.append(entry)
        self.log_added.emit(entry)

    def set_connection(self, connected):
        return self._on_owner(self._set_connection, bool(connected), False)

    def set_connecting(self, connecting=True):
        return self._on_owner(self._set_connecting, bool(connecting))

    def _set_connection(self, connected, connecting):
        self.connected, self.connecting = connected, connecting
        self.connection_changed.emit(connected, connecting)

    def _set_connecting(self, connecting):
        self._set_connection(self.connected, connecting)

    def set_muted(self, muted, reason=''):
        return self._on_owner(self._set_muted, bool(muted))

    def _set_muted(self, muted):
        self.muted = muted
        self.muted_changed.emit(muted)
        if muted:
            self._set_state(UiState.MUTED)
        elif self.state == UiState.MUTED:
            self._set_state(UiState.LISTENING)

    def set_persona(self, mode, mood, voice):
        return self._on_owner(self._set_persona, str(mode or 'normal'), str(mood or 'calm'), str(voice or 'Aoede'))

    def _set_persona(self, mode, mood, voice):
        self.persona = (mode, mood, voice)
        self.persona_changed.emit(mode, mood, voice)
