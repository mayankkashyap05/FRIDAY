"""FRIDAY ambient UI — Qt/QML bridge.

This is the **only** module that (a) imports Qt and (b) is allowed to touch QML
objects. Producers never touch it directly; they call the public façade in
``ui.py``, which marshals onto the GUI thread and then calls into here.

Structure
---------
``UiDispatcher``
    Thread-marshalling primitive. ``call(fn, *args)`` runs inline on the GUI
    thread, or queues a queued-connection signal otherwise. Never blocks.

``_CardsModel`` / ``_ActivityModel`` / ``_TranscriptModel``
    ``QAbstractListModel``s. Mutated **only** on the GUI thread.

``UiBridge``
    The QML-facing object, registered as the ``friday`` context property.
    Exposes scalar ``Property``s for the orb state and plain methods for user
    intents (they are always called from QML on the GUI thread, never through
    the meta-object, so ``Slot`` decoration would only add marshalling cost),
    and signal-driven updates for the models.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Iterable, Sequence

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    Qt,
    QTimer,
    Signal,
)

from . import card_mapper
from .event_bus import (
    Activity,
    ActivityKind,
    Card,
    CardKind,
    EventBus,
    LogChannel,
    LogEvent,
    Severity,
    Topics,
    UiState,
    classify_legacy,
    derive_state_for_log,
)
from .state_machine import AssistantStateMachine, Transition


# ─────────────────────────────────────────────────────────────────────────────
# Thread marshalling
# ─────────────────────────────────────────────────────────────────────────────

class UiDispatcher(QObject):
    """Deliver callables to the Qt GUI thread.

    Design note (changed after real multi-thread testing)
    -----------------------------------------------------
    The obvious implementation — ``Signal(object).emit(thunk)`` from producers —
    is **not** safe when several producer threads emit concurrently: PySide6
    aborts with a refcount error under sustained multi-thread contention
    (reproduced with 8 threads × 250 emits). It is also unsafe to rely on
    before ``exec()`` has started.

    So producers only do one thing: append to a ``deque``. ``deque.append`` is
    atomic under the GIL, order-preserving, and allocation-cheap. A
    GUI-thread ``QTimer`` drains it. That gives:

    * zero Qt object touch from producer threads (the hard requirement);
    * no concurrent ``emit`` ever;
    * a guaranteed FIFO order;
    * nothing lost if a producer fires before the event loop starts — items
      simply wait for the first drain.

    Worst-case latency is the timer interval (8 ms idle, 4 ms when a backlog
    builds), which is far below the frame rate of the overlay.
    """

    #: Retained so QML/other code can connect to a change notification.
    backlogChanged = Signal(int)

    IDLE_MS = 8
    BURST_MS = 4
    BURST_THRESHOLD = 24

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Thread identity comes from the *stdlib*, not from Qt: asking Qt for
        # the current thread makes it adopt a foreign thread and allocate a
        # wrapper object per call, which is slow and crashed under contention.
        # threading.get_ident() is a plain C-level lookup.
        self._gui_ident: int | None = threading.get_ident()
        self._gui_thread_obj = threading.current_thread()
        self._queue: "deque[Callable[[], None]]" = deque()
        self._lock = threading.Lock()
        self._queued = 0
        self._executed = 0
        self._dropped = 0
        self._max_queue = 65536
        self._warned = False

        self._timer = QTimer(self)
        self._timer.setInterval(self.IDLE_MS)
        self._timer.timeout.connect(self.drain)
        # Started lazily: only needed once a producer is active.
        self._timer_active = False

    # ── lifecycle ────────────────────────────────────────────────────────────
    def bind_gui_thread(self) -> None:
        """Call once from the GUI thread at startup."""
        self._gui_ident = threading.get_ident()
        self._gui_thread_obj = threading.current_thread()

    def start(self) -> None:
        """Start the drain timer (GUI thread)."""
        if not self._timer_active:
            self._timer_active = True
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._timer_active = False

    @property
    def on_gui_thread(self) -> bool:
        return threading.get_ident() == self._gui_ident

    # ── producer API ─────────────────────────────────────────────────────────
    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Run ``fn(*args)`` on the GUI thread. Returns immediately.

        Safe from any thread, at any time (including before ``exec()``).
        """
        def _thunk() -> None:
            try:
                fn(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[UiDispatcher] ⚠️ {getattr(fn, '__name__', fn)}: {exc}")

        if self.on_gui_thread:
            self._executed += 1
            _thunk()
            return

        if self._queued - self._executed > self._max_queue:
            self._dropped += 1
            if not self._warned:
                self._warned = True
                print("[UiDispatcher] ⚠️ consumer is not keeping up; "
                      "dropping cross-thread UI updates")
            return

        with self._lock:
            self._queue.append(_thunk)
            self._queued += 1
        # Nothing else. The cadence is adjusted in drain(), which runs on the
        # GUI thread; restarting a timer from a foreign thread is forbidden and
        # corrupts Qt's internal state.

    # ── consumer API (GUI thread) ────────────────────────────────────────────
    def drain(self, max_items: int = 512) -> int:
        """Run queued callables. GUI thread only."""
        # Adaptive cadence: idle quietly, speed up only while a burst is in
        # flight. Decided here, on the GUI thread, where touching the timer is
        # legal.
        backlog = self.pending()
        wanted = self.BURST_MS if backlog > self.BURST_THRESHOLD else self.IDLE_MS
        if self._timer_active and self._timer.interval() != wanted:
            self._timer.setInterval(wanted)

        done = 0
        while done < max_items:
            with self._lock:
                if not self._queue:
                    break
                thunk = self._queue.popleft()
            done += 1
            self._executed += 1
            try:
                thunk()
            except Exception as exc:  # pragma: no cover
                print(f"[UiDispatcher] ⚠️ queued call failed: {exc}")
        if done:
            # Drop back to the idle cadence once the burst is drained.
            if self._timer_active and not self.pending() \
                    and self._timer.interval() != self.IDLE_MS:
                self._timer.setInterval(self.IDLE_MS)
            self.backlogChanged.emit(done)
        return done

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def stats(self) -> dict:
        return {"queued": self._queued, "executed": self._executed,
                "dropped": self._dropped, "pending": self.pending()}


# ─────────────────────────────────────────────────────────────────────────────
# List models
# ─────────────────────────────────────────────────────────────────────────────

class _DictListModel(QAbstractListModel):
    """Base list model over a deque of plain dicts (QML-friendly roles)."""

    ROLES: tuple[str, ...] = ()
    CAPACITY = 64

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: list[dict] = []
        self._roles = list(self.ROLES)
        self._role_ids = {Qt.UserRole + 1 + i: name for i, name in enumerate(self._roles)}

    # QML plumbing ───────────────────────────────────────────────────────────
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802
        return {rid: QByteArray(name.encode()) for rid, name in self._role_ids.items()}

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        name = self._role_ids.get(role)
        if name is None:
            return None
        return self._items[index.row()].get(name)

    # mutation (GUI thread only) ─────────────────────────────────────────────
    def _append(self, item: dict) -> int:
        row = len(self._items)
        self.beginInsertRows(QModelIndex(), row, row)
        self._items.append(item)
        self.endInsertRows()
        self._trim()
        return row

    def _prepend(self, item: dict) -> None:
        self.beginInsertRows(QModelIndex(), 0, 0)
        self._items.insert(0, item)
        self.endInsertRows()
        self._trim()

    def _trim(self) -> None:
        while len(self._items) > self.CAPACITY:
            self.beginRemoveRows(QModelIndex(), len(self._items) - 1, len(self._items) - 1)
            self._items.pop()
            self.endRemoveRows()

    def _replace_all(self, items: Sequence[dict]) -> None:
        self.beginResetModel()
        self._items = list(items)[: self.CAPACITY]
        self.endResetModel()

    def _remove_uid(self, key: str, value: str) -> bool:
        for row, item in enumerate(self._items):
            if item.get(key) == value:
                self.beginRemoveRows(QModelIndex(), row, row)
                self._items.pop(row)
                self.endRemoveRows()
                return True
        return False

    def _touch(self, key: str, value: str, **changes: Any) -> bool:
        for row, item in enumerate(self._items):
            if item.get(key) != value:
                continue
            before = dict(item)
            item.update(changes)
            if item != before:
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx, [])
            return True
        return False

    def items(self) -> list[dict]:
        return list(self._items)

    def count(self) -> int:
        return len(self._items)


class CardsModel(_DictListModel):
    ROLES = ("uid", "kind", "title", "body", "footer", "payload", "severity",
             "ts", "ttl", "dismissible", "age")
    CAPACITY = 8

    def add(self, card: Card) -> None:
        data = card.to_qml()
        data["age"] = 0.0
        # Same kind + same title replaces in place (no duplicate stacking).
        for row, item in enumerate(self._items):
            if item.get("kind") == data["kind"] and item.get("title") == data["title"]:
                self.beginRemoveRows(QModelIndex(), row, row)
                self._items.pop(row)
                self.endRemoveRows()
                break
        self._prepend(data)

    def remove(self, uid: str) -> bool:
        return self._remove_uid("uid", uid)

    def clear(self) -> None:
        self._replace_all([])

    def expire(self, now: float) -> list[str]:
        """Drop TTL-expired cards; return their uids."""
        gone = []
        for row in range(len(self._items) - 1, -1, -1):
            item = self._items[row]
            ttl = float(item.get("ttl") or 0.0)
            if ttl > 0 and (now - float(item.get("ts") or now)) > ttl:
                gone.append(item.get("uid", ""))
                self.beginRemoveRows(QModelIndex(), row, row)
                self._items.pop(row)
                self.endRemoveRows()
        return gone


class ActivityModel(_DictListModel):
    ROLES = ("aid", "title", "kind", "detail", "progress", "active", "ts")
    CAPACITY = 16

    def upsert(self, activity: Activity) -> None:
        data = activity.to_qml()
        for row, item in enumerate(self._items):
            if item.get("aid") == data["aid"]:
                item.update(data)
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx, [])
                return
        self._prepend(data)

    def end(self, aid: str) -> bool:
        """Mark finished, then let the UI animate it out."""
        return self._touch("aid", aid, active=False, progress=1.0, ts=time.time())

    def remove(self, aid: str) -> bool:
        return self._remove_uid("aid", aid)

    def prune(self, max_age: float = 25.0) -> list[str]:
        now = time.time()
        gone = []
        for row in range(len(self._items) - 1, -1, -1):
            item = self._items[row]
            if item.get("active"):
                continue
            if now - float(item.get("ts") or now) > max_age:
                gone.append(item.get("aid", ""))
                self.beginRemoveRows(QModelIndex(), row, row)
                self._items.pop(row)
                self.endRemoveRows()
        return gone

    def active_count(self) -> int:
        return sum(1 for i in self._items if i.get("active"))


class TranscriptModel(_DictListModel):
    ROLES = ("text", "channel", "severity", "source", "ts", "seq", "clock")
    CAPACITY = 400

    def add(self, event: LogEvent) -> None:
        self._append(event.to_qml())

    def clear(self) -> None:
        self._replace_all([])


# ─────────────────────────────────────────────────────────────────────────────
# Bridge
# ─────────────────────────────────────────────────────────────────────────────

class UiBridge(QObject):
    """QML-facing object. Registered as the ``friday`` context property."""

    # ── scalar change notifications ──────────────────────────────────────────
    stateChanged = Signal()
    #: A new camera or arcade frame landed; the inspector must re-request the
    #: image URL because QQuickImageProvider URLs are revision-based.
    framesChanged = Signal()
    connectionChanged = Signal()
    personaChanged = Signal()
    visionChanged = Signal()
    arcadeChanged = Signal()
    themeChanged = Signal()
    effectsChanged = Signal()
    activityChanged = Signal()
    cardsChanged = Signal()
    inspectorChanged = Signal()
    setupChanged = Signal()
    inputRequested = Signal(str, str)      # (text, origin)
    toastRequested = Signal(str, str)      # (text, severity)
    reticleRequested = Signal(float, float)
    scanRequested = Signal(str)            # "start" | "stop"

    def __init__(
        self,
        state: AssistantStateMachine,
        bus: EventBus,
        dispatcher: UiDispatcher,
        theme: dict | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._bus = bus
        self._dispatcher = dispatcher
        #: Public handle: the controller starts/stops its drain timer.
        self.dispatcher = dispatcher

        # QML reads these directly (SnippetStack.model / LiveActivityBar.model),
        # so they must stay QObject-owned children of the bridge: that pins
        # their C++ lifetime to the QQmlEngine thread and keeps the models alive
        # as long as the QML that references them.
        self.cards = CardsModel(self)
        self.activities = ActivityModel(self)
        self.transcript = TranscriptModel(self)

        # ── vision / arcade / effects scalars ────────────────────────────────
        self._vision_signal = "Awaiting camera input"
        self._vision_mode = "STANDBY"
        self._vision_detections: list[dict] = []
        self._vision_preview_open = False
        self._arcade_open = False
        self._arcade_game = "face_filter"
        self._arcade_score = 0
        self._arcade_lives = 3
        self._arcade_message = "Ready"
        self._reticle = {"x": -1.0, "y": -1.0, "visible": False}
        self._scan_active = False
        self._toast = {"text": "", "severity": "info"}
        self._inspector_open = False
        self._inspector_tab = ""
        #: Bumped per frame batch; QML appends it to image provider URLs.
        self._frame_revision = 0
        self._setup_visible = False
        self._last_event = ""
        self._input_echo = ""
        self._theme = dict(theme or {})
        self._clock = time.strftime("%H:%M:%S")
        self._muted = False
        self._silent = False
        self._dnd = False

        self._state.add_listener(self._on_transition)
        self._wire_bus()

    # ── bus wiring ───────────────────────────────────────────────────────────
    def _wire_bus(self) -> None:
        for topic in (Topics.LOG, Topics.CARD_ADD, Topics.CARD_CLEAR,
                      Topics.ACTIVITY_UPSERT, Topics.ACTIVITY_END):
            self._bus.subscribe(topic, self._on_bus_event)

    def _on_bus_event(self, payload: Any) -> None:
        # Invoked by EventBus.drain on the GUI thread.
        try:
            self.ingest(payload)
        except Exception as exc:  # pragma: no cover
            print(f"[UiBridge] ⚠️ ingest failed: {exc}")

    def ingest(self, payload: Any) -> None:
        """Single entry point for *any* backend content. GUI thread only."""
        card, activities, event = card_mapper.to_cards(payload)
        if event is not None:
            self.transcript.add(event)
            self._last_event = event.text[:120]
            implied = derive_state_for_log(event)
            if implied is not None and not self._muted:
                # Legacy parity: transcript lines nudge the orb state.
                current = self._state.state
                if implied is UiState.PROCESSING and current is UiState.LISTENING:
                    self._state.transition(UiState.THINKING, "log:you")
                elif implied is UiState.SPEAKING and current in (
                        UiState.LISTENING, UiState.THINKING, UiState.PROCESSING):
                    self._state.start_speaking()
        if card is not None:
            self.cards.add(card)
            self.cardsChanged.emit()
        for activity in activities:
            self.activities.upsert(activity)
        if activities:
            self.activityChanged.emit()

    # ── state machine listener (GUI thread, since machine may be driven by it)
    def _on_transition(self, tr: Transition) -> None:
        if not tr.changed:
            return
        self._muted = self._state.muted
        self._silent = self._state.silent_mode
        self.stateChanged.emit()
        if tr.current is UiState.SPEAKING:
            self.scanRequested.emit("stop")
        if tr.current in (UiState.THINKING, UiState.PROCESSING):
            if not self._scan_active:
                self.scanRequested.emit("start")

    # ── properties: core state ───────────────────────────────────────────────
    @Property(str, notify=stateChanged)
    def state(self) -> str:
        return self._state.state.value

    @Property(str, notify=stateChanged)
    def statusLabel(self) -> str:
        return self._state.status_label()

    @Property(float, notify=stateChanged)
    def energy(self) -> float:
        return self._state.visual_energy()

    @Property(bool, notify=stateChanged)
    def muted(self) -> bool:
        return self._state.muted

    @Property(bool, notify=stateChanged)
    def silentMode(self) -> bool:
        return self._state.silent_mode

    @Property(bool, notify=stateChanged)
    def speaking(self) -> bool:
        return self._state.state is UiState.SPEAKING

    @Property(bool, notify=connectionChanged)
    def connected(self) -> bool:
        return self._state.connected

    @Property(bool, notify=connectionChanged)
    def connecting(self) -> bool:
        return self._state.connecting

    @Property(str, notify=connectionChanged)
    def connectionLabel(self) -> str:
        if self._state.connecting:
            return "CONNECTING"
        return "ONLINE" if self._state.connected else "OFFLINE"

    @Property(str, notify=stateChanged)
    def clock(self) -> str:
        return self._clock

    @Property(str, notify=stateChanged)
    def lastEvent(self) -> str:
        return self._last_event

    @Property(bool, notify=stateChanged)
    def dnd(self) -> bool:
        return self._dnd

    # ── properties: persona ──────────────────────────────────────────────────
    _persona = {"mode": "normal", "mood": "calm", "voice": "Aoede"}

    @Property(str, notify=personaChanged)
    def personaMode(self) -> str:
        return self._persona["mode"]

    @Property(str, notify=personaChanged)
    def personaMood(self) -> str:
        return self._persona["mood"]

    @Property(str, notify=personaChanged)
    def personaVoice(self) -> str:
        return self._persona["voice"]

    @Property(str, notify=personaChanged)
    def personaBadge(self) -> str:
        p = self._persona
        return f"{p['mode'].upper()} · {p['mood'].upper()} · {p['voice']}"

    # ── properties: vision / arcade / effects ────────────────────────────────
    @Property(str, notify=visionChanged)
    def visionSignal(self) -> str:
        return self._vision_signal

    @Property(str, notify=visionChanged)
    def visionMode(self) -> str:
        return self._vision_mode

    @Property(bool, notify=visionChanged)
    def visionPreviewOpen(self) -> bool:
        return self._vision_preview_open

    @Property("QVariantList", notify=visionChanged)
    def visionDetections(self) -> list:
        return self._vision_detections

    @Property(bool, notify=arcadeChanged)
    def arcadeOpen(self) -> bool:
        return self._arcade_open

    @Property(str, notify=arcadeChanged)
    def arcadeGame(self) -> str:
        return self._arcade_game

    @Property(int, notify=arcadeChanged)
    def arcadeScore(self) -> int:
        return self._arcade_score

    @Property(int, notify=arcadeChanged)
    def arcadeLives(self) -> int:
        return self._arcade_lives

    @Property(str, notify=arcadeChanged)
    def arcadeMessage(self) -> str:
        return self._arcade_message

    @Property(str, notify=effectsChanged)
    def toastText(self) -> str:
        return self._toast["text"]

    @Property(str, notify=effectsChanged)
    def toastSeverity(self) -> str:
        return self._toast["severity"]

    @Property(bool, notify=effectsChanged)
    def scanActive(self) -> bool:
        return self._scan_active

    @Property("QVariantMap", notify=effectsChanged)
    def reticle(self) -> dict:
        return self._reticle

    # ── properties: inspector / setup / theme ────────────────────────────────
    @Property(bool, notify=inspectorChanged)
    def inspectorOpen(self) -> bool:
        return self._inspector_open

    @Property(str, notify=inspectorChanged)
    def inspectorTab(self) -> str:
        return self._inspector_tab

    @Property(bool, notify=setupChanged)
    def setupVisible(self) -> bool:
        return self._setup_visible

    @Property("QVariantMap", notify=themeChanged)
    def theme(self) -> dict:
        return self._theme

    @Property(int, notify=cardsChanged)
    def cardCount(self) -> int:
        return self.cards.count()

    @Property(int, notify=activityChanged)
    def activeActivities(self) -> int:
        return self.activities.active_count()

    # ── slots: user intents ──────────────────────────────────────────────────
    def submitText(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._state.on_user_input(text)
        self.transcript.add(classify_legacy(f"You: {text}"))
        self.inputRequested.emit(text, "text")

    def toggleMute(self) -> None:
        self._state.set_muted(not self._state.muted, "ui")

    def toggleSilent(self) -> None:
        self._state.set_silent_mode(not self._state.silent_mode, "ui")

    def cancel(self) -> None:
        self._state.on_user_cancelled()

    def bargeIn(self) -> None:
        self._state.barge_in("ui")

    def notify_frames(self) -> None:
        """Called when new frame(s) are available.

        The revision only has to *change*: QML appends it to the provider URL,
        which makes QQuickImageProvider re-read the stream. Bumping per batch
        keeps it to one decode per batch rather than one per frame.
        """
        self._frame_revision += 1
        self.framesChanged.emit()

    @Property(int, notify=framesChanged)
    def frameRevision(self) -> int:
        return self._frame_revision

    def openInspector(self, tab: str = "") -> None:
        self._inspector_open = True
        self._inspector_tab = tab or self._inspector_tab or "camera"
        self.inspectorChanged.emit()

    def closeInspector(self) -> None:
        self._inspector_open = False
        self.inspectorChanged.emit()

    def dismissCard(self, uid: str) -> None:
        if self.cards.remove(uid):
            self.cardsChanged.emit()

    def clearCards(self) -> None:
        self.cards.clear()
        self.cardsChanged.emit()

    def clearTranscript(self) -> None:
        self.transcript.clear()

    def dismissToast(self) -> None:
        self._toast = {"text": "", "severity": "info"}
        self.effectsChanged.emit()

    def setArcade(self, game: str, open_: bool) -> None:
        self._arcade_game = game or self._arcade_game
        self._arcade_open = bool(open_)
        self.arcadeChanged.emit()

    def setArcadeState(self, score: int, lives: int, message: str) -> None:
        self._arcade_score = int(score)
        self._arcade_lives = int(lives)
        self._arcade_message = message or ""
        self.arcadeChanged.emit()

    def requestReticle(self, x: float, y: float) -> None:
        self._reticle = {"x": float(x), "y": float(y), "visible": True}
        self.effectsChanged.emit()
        self.reticleRequested.emit(float(x), float(y))

    def clearReticle(self) -> None:
        self._reticle = {"x": -1.0, "y": -1.0, "visible": False}
        self.effectsChanged.emit()

    def showToast(self, text: str) -> None:
        self.toast(text, "info")

    def toast(self, text: str, severity: str = "info") -> None:
        self._toast = {"text": str(text or "")[:200], "severity": severity}
        self.effectsChanged.emit()
        self.toastRequested.emit(self._toast["text"], severity)

    def cycleTheme(self, name: str = "") -> None:
        from .controller import THEME_NAMES, build_theme

        if name and name in THEME_NAMES:
            target = name
        else:
            try:
                idx = THEME_NAMES.index(self._theme.get("name", "cyber"))
            except ValueError:
                idx = -1
            target = THEME_NAMES[(idx + 1) % len(THEME_NAMES)]
        self.apply_theme(build_theme(target))

    # ── GUI-thread mutators called by the controller ─────────────────────────
    def apply_theme(self, theme: dict) -> None:
        self._theme = dict(theme)
        self.themeChanged.emit()

    def set_persona(self, mode: str, mood: str, voice: str) -> None:
        self._persona = {"mode": mode or "normal", "mood": mood or "calm",
                         "voice": voice or "Aoede"}
        self.personaChanged.emit()

    def set_connection(self, ok: bool) -> None:
        self._state.set_connection(bool(ok))
        self.connectionChanged.emit()

    def set_connecting(self, on: bool = True) -> None:
        self._state.set_connecting(bool(on))
        self.connectionChanged.emit()

    def set_vision_signal(self, text: str) -> None:
        self._vision_signal = str(text or "Awaiting camera input")[:160]
        self.visionChanged.emit()

    def set_vision_mode(self, mode: str) -> None:
        self._vision_mode = str(mode or "STANDBY").upper()
        self.visionChanged.emit()

    def set_vision_detections(self, detections: Iterable[dict]) -> None:
        self._vision_detections = list(detections)[:32]
        self.visionChanged.emit()

    def set_vision_preview_open(self, open_: bool) -> None:
        self._vision_preview_open = bool(open_)
        self.visionChanged.emit()

    def set_scan_active(self, active: bool) -> None:
        self._scan_active = bool(active)
        self.effectsChanged.emit()

    def set_setup_visible(self, visible: bool) -> None:
        self._setup_visible = bool(visible)
        self.setupChanged.emit()

    def set_dnd(self, dnd: bool) -> None:
        self._dnd = bool(dnd)
        self.stateChanged.emit()

    def tick_clock(self) -> None:
        clock = time.strftime("%H:%M:%S")
        if clock != self._clock:
            self._clock = clock
            self.stateChanged.emit()

    def expire_content(self) -> None:
        now = time.time()
        gone = self.cards.expire(now)
        if gone:
            self.cardsChanged.emit()
        if self.activities.prune():
            self.activityChanged.emit()

    # ── diagnostics ──────────────────────────────────────────────────────────
    def diagnostics(self) -> dict:
        return {
            "state": self._state.snapshot(),
            "dispatcher": self._dispatcher.stats(),
            "cards": self.cards.count(),
            "activities": self.activities.count(),
            "transcript": self.transcript.count(),
            "theme": self._theme.get("name", "?"),
            "vision": self._vision_mode,
            "arcadeOpen": self._arcade_open,
        }


__all__ = [
    "UiDispatcher",
    "UiBridge",
    "CardsModel",
    "ActivityModel",
    "TranscriptModel",
]
