"""Opt-in, bounded synthesized UI cues.

The Coucou checkout's sound engine references external WAV assets that are not
included here. These short tones are a fallback, not copies of those assets.
Playback is Windows-only and never opens an audio device for offscreen tests.
"""
import io
import math
import os
import queue
import struct
import sys
import threading
import wave

# start Hz, end Hz, seconds, peak level; deliberately gentle fallback tones.
_CUE_SPECS = {
    'expand': (440, 620, .09, .12), 'collapse': (620, 440, .09, .12),
    'hover': (620, 650, .035, .05), 'click': (700, 760, .045, .08),
    'dictation_start': (500, 740, .11, .10), 'dictation_stop': (740, 500, .11, .10),
    'completion': (660, 990, .12, .12), 'detach': (420, 620, .09, .10),
    'dock': (620, 420, .09, .10), 'error': (360, 230, .13, .10),
}


def synthesize(name, rate=22050):
    start, end, duration, level = _CUE_SPECS[name]
    count = int(rate * duration)
    frames = bytearray()
    phase = 0.0
    for i in range(count):
        t = i / rate
        fraction = i / max(1, count - 1)
        phase += 2 * math.pi * (start + (end-start)*fraction) / rate
        envelope = min(1.0, t/.008, (duration-t)/.025)
        frames.extend(struct.pack('<h', round(32767 * level * max(0, envelope) * math.sin(phase))))
    result = io.BytesIO()
    with wave.open(result, 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(rate); output.writeframes(frames)
    return result.getvalue()


class UiSounds:
    def __init__(self):
        self.enabled = False
        self._queue = queue.Queue(maxsize=8)
        self._closed = threading.Event()
        self._thread = None

    def play(self, name):
        if (not self.enabled or self._closed.is_set() or sys.platform != 'win32' or
                os.environ.get('QT_QPA_PLATFORM', '').lower() in ('offscreen', 'minimal') or name not in _CUE_SPECS):
            return False
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name='FridayUiSounds')
            self._thread.start()
        try:
            self._queue.put_nowait(name)
            return True
        except queue.Full:
            return False

    def _run(self):
        try:
            import winsound
        except ImportError:
            return
        while not self._closed.is_set():
            try: name = self._queue.get(timeout=.1)
            except queue.Empty: continue
            if not self.enabled or self._closed.is_set(): continue
            try: winsound.PlaySound(synthesize(name), winsound.SND_MEMORY | winsound.SND_NODEFAULT)
            except (RuntimeError, OSError): pass

    def close(self):
        self._closed.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(.4)
