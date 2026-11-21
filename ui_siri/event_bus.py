"""FRIDAY ambient UI — thread-safe typed event bus and UI schemas.

Design constraints (see ``docs/UI_AUDIT_CONTRACT.md`` §4.2):

* Producers live on arbitrary threads (asyncio loop, vision thread, tray thread,
  wake-word thread, agent task queue). They must **never** touch a Qt object.
* Consumers are QML models living on the GUI thread.
* High-frequency traffic (video frames, telemetry) must not flood the GUI event
  queue, so frames travel as *revisions* in :mod:`ui_siri.image_provider` and
  only cheap scalars go through the bus.

Everything here is pure Python (stdlib only) so it can be imported and unit
tested without Qt.
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Iterable, Iterator, Sequence


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations shared with QML
# ─────────────────────────────────────────────────────────────────────────────

class UiState(str, Enum):
    """The six-state assistant lifecycle.

    ``str`` mixin keeps it JSON/QML friendly and keeps legacy
    ``set_state("LISTENING")`` string comparisons working.
    """

    BOOTING = "BOOTING"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    MUTED = "MUTED"

    @classmethod
    def coerce(cls, value: Any) -> "UiState":
        """Best-effort coercion from legacy strings / members.

        Unknown values degrade to :data:`UiState.LISTENING` rather than
        raising — the legacy Tk UI mapped unknown strings to ``ONLINE``.
        """
        if isinstance(value, UiState):
            return value
        text = str(value or "").strip().upper().replace("-", "_")
        # Legacy / external spellings that must reach a real state. Anything
        # missing here silently degrades to LISTENING (the fallthrough below),
        # which is how "ACTING" ended up rendering as LISTENING.
        aliases = {
            "ONLINE": cls.LISTENING,
            "IDLE": cls.LISTENING,
            "READY": cls.LISTENING,
            # A tool call is running: that is PROCESSING, not idling.
            "ACTING": cls.PROCESSING,
            "ACTING_ON": cls.PROCESSING,
            "TOOL": cls.PROCESSING,
            "TOOL_CALL": cls.PROCESSING,
            "EXECUTING": cls.PROCESSING,
            "WORKING": cls.PROCESSING,
            "TEXT_ONLY": cls.MUTED,
            "TEXTONLY": cls.MUTED,
            "INITIALISING": cls.BOOTING,
            "INITIALIZING": cls.BOOTING,
            "CONNECTING": cls.BOOTING,
            # DORMANT is the *idle, awake* state -- FRIDAY is running and
            # listening, it is just not doing anything. Mapping it to BOOTING
            # (as an earlier revision did) rendered a spinner and a spinning
            # perimeter glow forever. It must land on LISTENING.
            "DORMANT": cls.LISTENING,
            "SLEEPING": cls.LISTENING,
            "ASLEEP": cls.LISTENING,
            "ERROR": cls.MUTED,
            "FAILED": cls.MUTED,
            "OFFLINE": cls.MUTED,
        }
        if text in aliases:
            return aliases[text]
        try:
            return cls(text)
        except ValueError:
            return cls.LISTENING


class CardKind(str, Enum):
    """Visual/behavioural archetype of a snippet card."""

    TEXT = "text"
    WEATHER = "weather"
    CALENDAR = "calendar"
    EMAIL = "email"
    FLIGHT = "flight"
    TELEMETRY = "telemetry"
    CONTROL = "control"
    DOCUMENT = "document"
    CONFIRMATION = "confirmation"


class ActivityKind(str, Enum):
    """Kind of background job shown in the Live Activity Bar."""

    TASK = "task"
    VISION = "vision"
    CODE = "code"
    BROWSER = "browser"
    DOWNLOAD = "download"
    RECORDING = "recording"
    REMINDER = "reminder"
    GENERIC = "generic"


class Severity(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARN = "warn"
    ERROR = "error"
    SECURITY = "security"

    @classmethod
    def coerce(cls, value: Any) -> "Severity":
        try:
            return cls(str(value or "").lower())
        except ValueError:
            return cls.INFO


class LogChannel(str, Enum):
    """Who produced a log line — drives colour and card placement."""

    YOU = "you"
    FRIDAY = "friday"
    SYSTEM = "system"
    ERROR = "error"
    VISION = "vision"
    SECURITY = "security"
    ALERT = "alert"


# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class LogEvent:
    """One line of the transcript."""

    text: str
    channel: LogChannel = LogChannel.SYSTEM
    severity: Severity = Severity.INFO
    source: str = ""
    ts: float = field(default_factory=time.time)
    seq: int = 0

    def to_qml(self) -> dict:
        return {
            "text": self.text,
            "channel": self.channel.value,
            "severity": self.severity.value,
            "source": self.source,
            "ts": self.ts,
            "seq": self.seq,
            "clock": time.strftime("%H:%M:%S", time.localtime(self.ts)),
        }


@dataclass(slots=True)
class Card:
    """A snippet-stack card.

    ``payload`` is intentionally free-form so ``card_mapper`` can attach
    weather/calendar/flight specifics without widening this dataclass.
    """

    kind: CardKind = CardKind.TEXT
    title: str = ""
    body: str = ""
    footer: str = ""
    payload: dict = field(default_factory=dict)
    severity: Severity = Severity.INFO
    ts: float = field(default_factory=time.time)
    uid: str = ""
    ttl: float = 0.0  # 0 = persist until dismissed
    dismissible: bool = True

    def to_qml(self) -> dict:
        data = {
            "uid": self.uid,
            "kind": self.kind.value,
            "title": self.title,
            "body": self.body,
            "footer": self.footer,
            "payload": self.payload,
            "severity": self.severity.value,
            "ts": self.ts,
            "ttl": self.ttl,
            "dismissible": self.dismissible,
        }
        return data


@dataclass(slots=True)
class Activity:
    """A live background job for the Dynamic-Island style capsule."""

    aid: str
    title: str
    kind: ActivityKind = ActivityKind.GENERIC
    detail: str = ""
    progress: float = -1.0  # <0 → indeterminate
    active: bool = True
    ts: float = field(default_factory=time.time)

    def to_qml(self) -> dict:
        return {
            "aid": self.aid,
            "title": self.title,
            "kind": self.kind.value,
            "detail": self.detail,
            "progress": self.progress,
            "active": self.active,
            "ts": self.ts,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Bus
# ─────────────────────────────────────────────────────────────────────────────

Subscriber = Callable[[Any], None]


class EventBus:
    """Thread-safe fan-out bus with a GUI-side drain queue.

    Two delivery modes:

    ``subscribe(topic, fn)``
        Delivered on the **draining thread** (GUI) only. Subscribers are never
        called from a producer thread. This is the only mode QML models use.

    ``publish_now(topic, payload)``
        Same as publish; there is no synchronous mode on purpose.

    ``drain(max_items=256)``
        Called by a QTimer on the GUI thread. Invokes subscribers FIFO. Returns
        the number of events processed.
    """

    def __init__(self) -> None:
        self._q: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._subs: dict[str, list[Subscriber]] = {}
        self._lock = threading.RLock()
        self._seq = itertools.count(1)
        self._dropped = 0
        self._published = 0

    # ── producer side ────────────────────────────────────────────────────────
    def next_seq(self) -> int:
        return next(self._seq)

    def publish(self, topic: str, payload: Any = None) -> None:
        """Enqueue an event. Safe from any thread. Never blocks."""
        self._published += 1
        self._q.put((topic, payload))

    # ── consumer side (GUI thread) ───────────────────────────────────────────
    def subscribe(self, topic: str, fn: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subs.setdefault(topic, []).append(fn)

        def _unsub() -> None:
            with self._lock:
                try:
                    self._subs.get(topic, []).remove(fn)
                except ValueError:
                    pass

        return _unsub

    def subscribers(self, topic: str) -> tuple[Subscriber, ...]:
        with self._lock:
            return tuple(self._subs.get(topic, ()))

    def drain(self, max_items: int = 256) -> int:
        """Process up to ``max_items`` events. GUI thread only."""
        done = 0
        while done < max_items:
            try:
                topic, payload = self._q.get_nowait()
            except queue.Empty:
                break
            done += 1
            for fn in self.subscribers(topic):
                try:
                    fn(payload)
                except Exception as exc:  # pragma: no cover - defensive
                    # A broken subscriber must never take down the UI.
                    print(f"[EventBus] ⚠️ subscriber error on {topic!r}: {exc}")
            for fn in self.subscribers("*"):
                try:
                    fn((topic, payload))
                except Exception as exc:  # pragma: no cover
                    print(f"[EventBus] ⚠️ wildcard subscriber error: {exc}")
        if done == 0 and not self._q.empty():
            self._dropped += 1
        return done

    def pending(self) -> int:
        return self._q.qsize()

    def flush(self, max_items: int = 100_000) -> int:
        """Drain everything — used by tests and by shutdown."""
        total = 0
        while True:
            n = self.drain(max_items)
            if n == 0:
                return total
            total += n

    # ── introspection ────────────────────────────────────────────────────────
    def stats(self) -> dict:
        with self._lock:
            topics = {k: len(v) for k, v in self._subs.items()}
        return {
            "published": self._published,
            "pending": self.pending(),
            "topics": topics,
        }

    def clear(self) -> None:
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break


# ─────────────────────────────────────────────────────────────────────────────
# Topic constants (single source of truth for producers & consumers)
# ─────────────────────────────────────────────────────────────────────────────

class Topics:
    """Canonical topic names. ``str`` constants, not an enum, for cheap compare."""

    LOG = "ui.log"
    STATE = "ui.state"
    CONNECTION = "ui.connection"
    PERSONA = "ui.persona"
    CARD_ADD = "ui.card.add"
    CARD_CLEAR = "ui.card.clear"
    ACTIVITY_UPSERT = "ui.activity.upsert"
    ACTIVITY_END = "ui.activity.end"
    VISION_SIGNAL = "vision.signal"
    VISION_DETECTIONS = "vision.detections"
    VISION_MODE = "vision.mode"
    VISION_PREVIEW = "vision.preview"
    ARCADE_OPEN = "arcade.open"
    ARCADE_STATE = "arcade.state"
    SCREEN_EFFECT = "screen.effect"
    RETICLE = "screen.reticle"
    THEME = "ui.theme"
    INSPECTOR = "ui.inspector"
    SETUP = "ui.setup"
    WAKE_WORD = "ui.wakeword"
    BARGE_IN = "ui.bargein"
    TOAST = "ui.toast"
    TECHNOTE = "ui.debug"


# ─────────────────────────────────────────────────────────────────────────────
# Legacy string classification
# ─────────────────────────────────────────────────────────────────────────────

# Legacy emitters use ad-hoc prefixes. We map them to channels *and* strip the
# prefix for display, exactly like the Tk version did (``_write_log``).
_PREFIX_TABLE: Sequence[tuple[str, LogChannel, Severity]] = (
    ("you:", LogChannel.YOU, Severity.INFO),
    ("friday:", LogChannel.FRIDAY, Severity.INFO),
    ("ai:", LogChannel.FRIDAY, Severity.INFO),
    ("sys:", LogChannel.SYSTEM, Severity.INFO),
    ("err:", LogChannel.ERROR, Severity.ERROR),
    ("vision:", LogChannel.VISION, Severity.INFO),
    ("[guardian]", LogChannel.ALERT, Severity.WARN),
    ("[monitor]", LogChannel.ALERT, Severity.WARN),
    ("[email]", LogChannel.ALERT, Severity.INFO),
    ("[browser]", LogChannel.SYSTEM, Severity.INFO),
    ("[cmd]", LogChannel.SYSTEM, Severity.INFO),
    ("[code]", LogChannel.SYSTEM, Severity.INFO),
    ("[computer]", LogChannel.SYSTEM, Severity.INFO),
    ("[desktop]", LogChannel.SYSTEM, Severity.INFO),
    ("[devagent]", LogChannel.SYSTEM, Severity.INFO),
    ("[file]", LogChannel.SYSTEM, Severity.INFO),
    ("[flightfinder]", LogChannel.SYSTEM, Severity.INFO),
    ("[gameupdater]", LogChannel.SYSTEM, Severity.INFO),
    ("[msg]", LogChannel.SYSTEM, Severity.INFO),
    ("[open_app]", LogChannel.SYSTEM, Severity.INFO),
    ("[reminder]", LogChannel.ALERT, Severity.INFO),
    ("[search]", LogChannel.SYSTEM, Severity.INFO),
    ("[youtube]", LogChannel.SYSTEM, Severity.INFO),
    ("security:", LogChannel.SECURITY, Severity.SECURITY),
    ("⏰", LogChannel.ALERT, Severity.INFO),
    ("🔔", LogChannel.ALERT, Severity.INFO),
)

_ERROR_MARKERS = ("error", "failed", "traceback", "exception", "denied", "unable")


def classify_legacy(text: str) -> LogEvent:
    """Turn one legacy ``write_log`` string into a typed :class:`LogEvent`.

    Never raises. Never drops content: the returned ``text`` is always a
    human-readable rendering of the original line.
    """
    raw = "" if text is None else str(text)
    channel = LogChannel.SYSTEM
    severity = Severity.INFO
    body = raw.strip()

    lowered = raw.lstrip().lower()
    for prefix, chan, sev in _PREFIX_TABLE:
        if lowered.startswith(prefix):
            channel, severity = chan, sev
            body = raw.lstrip()[len(prefix):].strip(" :—-")
            if prefix.startswith("[") or prefix.startswith("⏰") or prefix.startswith("🔔"):
                # Keep bracketed module tags — they read as context, not noise.
                body = raw.lstrip()
            break

    if not body:
        body = raw.strip()

    low = body.lower()
    if channel is not LogChannel.ERROR and any(m in low for m in _ERROR_MARKERS):
        severity = Severity.ERROR if "error" in low or "traceback" in low else Severity.WARN

    if channel in (LogChannel.ALERT, LogChannel.SECURITY) and severity is Severity.INFO:
        severity = Severity.WARN

    return LogEvent(text=body, channel=channel, severity=severity, source=raw[:0] or "")


def derive_state_for_log(event: LogEvent) -> UiState | None:
    """Legacy side-effect: a log line could move the orb state.

    Kept because ``main.py`` relies on the orb reacting to transcript lines
    even when it forgets to call ``set_state`` (see contract §3.4).
    """
    if event.channel is LogChannel.YOU:
        return UiState.PROCESSING
    if event.channel is LogChannel.FRIDAY:
        return UiState.SPEAKING
    return None


__all__ = [
    "UiState",
    "CardKind",
    "ActivityKind",
    "Severity",
    "LogChannel",
    "LogEvent",
    "Card",
    "Activity",
    "EventBus",
    "Topics",
    "classify_legacy",
    "derive_state_for_log",
    "Subscriber",
]
