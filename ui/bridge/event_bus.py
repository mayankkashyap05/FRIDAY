"""Thread-safe queued events, delivered only by an explicit consumer drain."""
from collections import deque
import logging
import threading

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self, max_pending=2048):
        if max_pending < 1:
            raise ValueError('max_pending must be positive')
        self._queue = deque()
        self._subscribers = {}
        self._lock = threading.Lock()
        self.max_pending = max_pending
        self.dropped = 0

    def subscribe(self, topic, callback):
        with self._lock:
            self._subscribers.setdefault(topic, []).append(callback)
        def unsubscribe():
            with self._lock:
                callbacks = self._subscribers.get(topic, [])
                if callback in callbacks:
                    callbacks.remove(callback)
        return unsubscribe

    def publish(self, topic, payload):
        with self._lock:
            if len(self._queue) >= self.max_pending:
                self._queue.popleft()
                self.dropped += 1
            self._queue.append((topic, payload))

    def pending(self):
        with self._lock:
            return len(self._queue)

    def drain(self, max_items=256):
        count = 0
        while count < max_items:
            with self._lock:
                if not self._queue:
                    break
                topic, payload = self._queue.popleft()
                subscribers = list(self._subscribers.get(topic, ()))
                wildcard = list(self._subscribers.get('*', ()))
            for callback, arg in [(fn, payload) for fn in subscribers] + [(fn, (topic, payload)) for fn in wildcard]:
                try:
                    callback(arg)
                except Exception:
                    log.exception('UI event subscriber failed for %s', topic)
            count += 1
        return count

    def flush(self):
        count = 0
        while self.pending():
            count += self.drain()
        return count
