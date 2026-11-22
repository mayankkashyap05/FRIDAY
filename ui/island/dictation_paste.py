"""Focus-checked clipboard paste primitives for a dictation session.

All methods must be called on the Qt thread. The adapter is injectable so the
clipboard and focus contract can be exercised without interacting with a desktop.
This module does not record audio or interpret assistant transcripts.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
from . import win32_overlay


@dataclass(frozen=True)
class Target:
    handle: int
    title: str


def style_text(text, style):
    text = str(text)
    if style == 'Exact':
        return text
    if style not in ('Clean', 'Polished'):
        raise ValueError('Unknown dictation style')
    text = re.sub(r'[ \t]+', ' ', text).strip()
    text = re.sub(r' +([,.;:!?])', r'\1', text)
    if style == 'Polished' and text:
        text = text[0].upper() + text[1:]
        if text[-1] not in '.!?':
            text += '.'
    return text


class QtWindowsAdapter:
    """Qt clipboard and Win32 input, with no focus changes or forced activation."""
    def __init__(self):
        if not win32_overlay.IS_WINDOWS:
            raise RuntimeError('External paste is only available on Windows')
        from PySide6.QtWidgets import QApplication
        self.clipboard = QApplication.clipboard()

    def foreground(self):
        return Target(*win32_overlay.foreground_window())

    def snapshot(self):
        mime = self.clipboard.mimeData()
        if mime is None:
            return {}
        # Preserve all accessible MIME formats, including HTML and images.
        return {name: bytes(mime.data(name)) for name in mime.formats()}

    def set_text(self, text):
        self.clipboard.setText(text)

    def restore(self, snapshot):
        from PySide6.QtCore import QByteArray, QMimeData
        mime = QMimeData()
        for name, data in snapshot.items():
            mime.setData(name, QByteArray(data))
        self.clipboard.setMimeData(mime)

    def sequence(self): return win32_overlay.clipboard_sequence()
    def paste(self): win32_overlay.send_paste()
    def later(self, ms, fn):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(ms, fn)


class DictationPaste:
    def __init__(self, adapter, *, restore_delay_ms=700):
        self.adapter = adapter
        self.restore_delay_ms = restore_delay_ms
        self.target = None
        self._restore_pending = False

    def begin(self):
        target = self.adapter.foreground()
        if not target.handle:
            raise RuntimeError('No target application is focused')
        self.target = target
        return target

    def cancel(self):
        self.target = None

    def commit(self, text, *, style='Exact', mode='paste'):
        if mode not in ('paste', 'clipboard'):
            raise ValueError('Unknown paste mode')
        if self._restore_pending:
            raise RuntimeError('A previous paste is still restoring the clipboard')
        text = style_text(text, style)
        if not text.strip():
            raise ValueError('No dictated text to commit')
        target = self.target
        if target is None:
            raise RuntimeError('No dictation target was captured')
        # Do not move focus or paste into a different application if the user
        # switched windows while dictating.
        if mode == 'paste' and self.adapter.foreground().handle != target.handle:
            raise RuntimeError('Target application is no longer focused')
        before = self.adapter.sequence()
        snapshot = self.adapter.snapshot()
        # Avoid replacing clipboard content that changed while being copied.
        if self.adapter.sequence() != before:
            raise RuntimeError('Clipboard changed while it was being saved')
        self.adapter.set_text(text)
        ours = self.adapter.sequence()
        self.target = None
        if mode == 'clipboard':
            return 'copied'
        def restore():
            try:
                if self.adapter.sequence() == ours:
                    self.adapter.restore(snapshot)
            finally:
                self._restore_pending = False
        try:
            if self.adapter.foreground().handle != target.handle:
                raise RuntimeError('Target application is no longer focused')
            if self.adapter.sequence() != ours:
                raise RuntimeError('Clipboard changed before paste')
            self.adapter.paste()
        except Exception:
            restore()
            raise
        self._restore_pending = True
        try:
            self.adapter.later(self.restore_delay_ms, restore)
        except Exception:
            restore()
            raise
        return 'pasted'


class DictationKeyLogic:
    """Reference hold/double-tap state machine, independent of OS hooks."""
    HOLD_THRESHOLD = .30
    DOUBLE_TAP_WINDOW = .40

    def __init__(self):
        self.phase = 'idle'
        self.since = self.until = 0.0

    def down(self, now):
        if self.phase == 'idle':
            self.phase, self.since = 'holding', now
            return 'start'
        if self.phase == 'holding':
            return None
        if self.phase == 'tap_wait':
            if now <= self.until:
                self.phase = 'locked'
                return 'lock'
            self.phase, self.since = 'holding', now
            return 'restart'
        self.phase = 'idle'
        return 'finish'

    def up(self, now):
        if self.phase != 'holding': return None
        if now - self.since >= self.HOLD_THRESHOLD:
            self.phase = 'idle'
            return 'finish'
        self.phase, self.until = 'tap_wait', now + self.DOUBLE_TAP_WINDOW
        return None

    def tick(self, now):
        if self.phase == 'tap_wait' and now > self.until:
            self.phase = 'idle'
            return 'cancel'
        return None

    def other_key(self):
        if self.phase in ('holding', 'tap_wait'):
            self.phase = 'idle'
            return 'cancel'
        return None

    def escape(self):
        if self.phase == 'idle': return None
        self.phase = 'idle'
        return 'cancel'
