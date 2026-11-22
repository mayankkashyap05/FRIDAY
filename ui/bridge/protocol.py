"""Typed in-process events shared by FRIDAY producers and the Qt frontend.

There is intentionally no network protocol: the native UI runs in the same
process.  Payloads must not contain widgets or other Qt-owned objects.
"""
from dataclasses import dataclass
from enum import Enum


class UiState(str, Enum):
    IDLE = 'IDLE'
    LISTENING = 'LISTENING'
    THINKING = 'THINKING'
    SPEAKING = 'SPEAKING'
    MUTED = 'MUTED'
    ERROR = 'ERROR'

    @classmethod
    def coerce(cls, value):
        normalized = str(value or '').strip().upper().replace('-', '_')
        aliases = {'ONLINE': cls.LISTENING, 'PROCESSING': cls.THINKING,
                   'WORKING': cls.THINKING, 'INITIALISING': cls.IDLE,
                   'INITIALIZING': cls.IDLE}
        try:
            return cls(normalized)
        except ValueError:
            return aliases.get(normalized, cls.IDLE)


@dataclass(frozen=True)
class LogEntry:
    text: str
    channel: str = 'system'


def classify_log(text):
    """Preserve the original text while exposing common legacy prefixes."""
    raw = str(text or '')
    prefix = raw.partition(':')[0].strip().lower()
    channel = {'you': 'user', 'friday': 'assistant', 'sys': 'system',
               'err': 'error', 'security': 'security', 'vision': 'vision'}.get(prefix, 'system')
    return LogEntry(raw, channel)
