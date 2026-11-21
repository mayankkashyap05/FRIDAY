"""FRIDAY UI — pure-Python dispatcher, stores and WebSocket-facing bridge.

There is no Qt here (and no Qt anywhere in the project). The old
``QObject``/``QTimer``/``QAbstractListModel`` triple is replaced by exact
functional equivalents:

``UiDispatcher``
    Thread-marshalling primitive. Producer threads (asyncio voice loop,
    ``sounddevice`` audio callback, pystray thread, keyboard-hotkey thread,
    vision thread, agent task queue) only ever ``append`` a callable to a
    ``collections.deque`` — atomic under the GIL, order-preserving, cheap, and
    allocation-light. One dedicated UI-loop thread owns every bridge mutation:

    * 120 Hz drain while there is work (worst-case latency 8.3 ms),
    * an event-signaled wait when idle, so idle CPU is ~0 %,
    * nothing is lost if a producer fires before the loop starts.

``CardsModel`` / ``ActivityModel`` / ``TranscriptModel``
    Bounded stores with the same semantics as the previous models: 8 cards,
    16 activities, 400 transcript lines, replace-in-place by ``(kind, title)``,
    TTL expiry, and activity pruning.

``UiBridge``
    The single mutation surface. Every change is mirrored into a compact typed
    delta handed to :attr:`UiBridge.on_emit`, which the controller forwards to
    connected renderers over the loopback WebSocket.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Iterable, Sequence

from . import card_mapper, protocol
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

class UiDispatcher:
    """Deliver callables to the UI-loop thread.

    Producers never touch mutable UI state: they append to a deque and wake the
    loop. That gives a guaranteed FIFO order, zero lock contention on the hot
    path, and no reliance on ``signal.emit`` (concurrent emits from several
    producer threads are unsafe in every Qt binding, and are simply not needed
    once a single consumer thread owns the state).
    """

    #: 120 Hz while work is queued.
    BURST_HZ = 120.0
    #: Maximum items handled per drain pass (bounds one pass to ~1 ms).
    MAX_DRAIN = 512
    #: Bounded backlog: a dead consumer must not grow memory without limit.
    MAX_QUEUE = 65_536

    def __init__(self, name: str = "FridayUiLoop") -> None:
        self._name = name
        self._ident: int | None = threading.get_ident()
        self._thread: threading.Thread | None = None
        # ``deque.append``/``popleft`` are atomic under the GIL; the lock only
        # guards the *counter* arithmetic and the multi-item swap in drain().
        self._queue: deque[Callable[[], None]] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._queued = 0
        self._executed = 0
        self._dropped = 0
        self._warned = False
        self._started = False
        #: Optional hook run on the UI thread after every drain pass (the
        #: controller drains the EventBus there).
        self.hook: Callable[[], None] | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    def bind_ui_thread(self) -> None:
        """Declare the calling thread to be the UI thread."""
        self._ident = threading.get_ident()

    def start(self) -> None:
        """Start the dedicated UI-loop thread (idempotent)."""
        if self._started:
            return
        self._started = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self._name,
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None
        self._started = False

    def _run(self) -> None:
        self.bind_ui_thread()
        interval = 1.0 / self.BURST_HZ
        while not self._stop.is_set():
            try:
                worked = self.drain()
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[UiDispatcher] ⚠️ drain failed: {exc}")
                worked = 0
            if worked:
                time.sleep(interval)
            else:
                # Idle: sleep until a producer wakes us. The timeout guarantees
                # timers/grace checks still progress even if nothing queues.
                self._wake.wait(0.25)
                self._wake.clear()
        # Final sweep so a stop() does not lose the last state change.
        try:
            self.drain()
        except Exception:
            pass

    @property
    def on_ui_thread(self) -> bool:
        return threading.get_ident() == self._ident

    @property
    def running(self) -> bool:
        return self._started and not self._stop.is_set()

    # ── producer API ─────────────────────────────────────────────────────────
    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Run ``fn(*args, **kwargs)`` on the UI thread. Never blocks.

        Safe from any thread, at any time (including before the loop starts).
        """
        if self.on_ui_thread:
            self._executed += 1
            try:
                fn(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[UiDispatcher] ⚠️ {getattr(fn, '__name__', fn)}: {exc}")
            return

        if self._queued - self._executed > self.MAX_QUEUE:
            self._dropped += 1
            if not self._warned:
                self._warned = True
                print("[UiDispatcher] ⚠️ consumer is not keeping up; "
                      "dropping cross-thread UI updates")
            return

        def _thunk() -> None:
            try:
                fn(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[UiDispatcher] ⚠️ {getattr(fn, '__name__', fn)}: {exc}")

        with self._lock:
            self._queue.append(_thunk)
            self._queued += 1
        self._wake.set()

    # ── consumer API (UI thread) ─────────────────────────────────────────────
    def drain(self, max_items: int | None = None) -> int:
        """Run queued callables FIFO. UI thread only (tests may drive it)."""
        limit = self.MAX_DRAIN if max_items is None else int(max_items)
        done = 0
        while done < limit:
            with self._lock:
                if not self._queue:
                    break
                thunk = self._queue.popleft()
            done += 1
            self._executed += 1
            try:
                thunk()
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[UiDispatcher] ⚠️ queued call failed: {exc}")

        hook = self.hook
        if hook is not None:
            try:
                hook()
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[UiDispatcher] ⚠️ hook failed: {exc}")
        return done

    def pending(self) -> int:
        return len(self._queue)

    def stats(self) -> dict:
        return {"queued": self._queued, "executed": self._executed,
                "dropped": self._dropped, "pending": self.pending()}


# ─────────────────────────────────────────────────────────────────────────────
# Stores (pure Python, UI thread only)
# ─────────────────────────────────────────────────────────────────────────────

class _DictStore:
    """Bounded list of plain dicts with the roles the renderer reads."""

    ROLES: tuple[str, ...] = ()
    CAPACITY = 64

    def __init__(self) -> None:
        self._items: list[dict] = []

    # mutation ───────────────────────────────────────────────────────────────
    def _append(self, item: dict) -> int:
        self._items.append(item)
        self._trim()
        return len(self._items) - 1

    def _prepend(self, item: dict) -> None:
        self._items.insert(0, item)
        self._trim()

    def _trim(self) -> None:
        if len(self._items) > self.CAPACITY:
            del self._items[self.CAPACITY:]

    def _replace_all(self, items: Sequence[dict]) -> None:
        self._items = list(items)[: self.CAPACITY]

    def _remove_uid(self, key: str, value: str) -> bool:
        for row, item in enumerate(self._items):
            if item.get(key) == value:
                self._items.pop(row)
                return True
        return False

    def _touch(self, key: str, value: str, **changes: Any) -> bool:
        for item in self._items:
            if item.get(key) != value:
                continue
            before = dict(item)
            item.update(changes)
            return item != before
        return False

    # introspection ──────────────────────────────────────────────────────────
    def items(self) -> list[dict]:
        return [dict(item) for item in self._items]

    def raw(self) -> list[dict]:
        """The live list (no copy) — internal/UI-thread callers only."""
        return self._items

    def count(self) -> int:
        return len(self._items)

    def roleNames(self) -> tuple[str, ...]:  # noqa: N802 - legacy spelling
        return self.ROLES


class CardsModel(_DictStore):
    ROLES = ("uid", "kind", "title", "body", "footer", "payload", "severity",
             "ts", "ttl", "dismissible", "age")
    CAPACITY = 8

    def add(self, card: Card) -> None:
        data = card.to_dict()
        data["age"] = 0.0
        # Same kind + same title replaces in place (no duplicate stacking).
        for row, item in enumerate(self._items):
            if item.get("kind") == data["kind"] and item.get("title") == data["title"]:
                self._items.pop(row)
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
                self._items.pop(row)
        return gone


class ActivityModel(_DictStore):
    ROLES = ("aid", "title", "kind", "detail", "progress", "active", "ts")
    CAPACITY = 16

    def upsert(self, activity: Activity) -> None:
        data = activity.to_dict()
        for item in self._items:
            if item.get("aid") == data["aid"]:
                item.update(data)
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
                self._items.pop(row)
        return gone

    def active_count(self) -> int:
        return sum(1 for i in self._items if i.get("active"))


class TranscriptModel(_DictStore):
    ROLES = ("text", "channel", "severity", "source", "ts", "seq", "clock")
    CAPACITY = 400

    def add(self, event: LogEvent) -> None:
        self._append(event.to_dict())

    def remove(self, seq: str) -> bool:  # pragma: no cover - symmetry helper
        return self._remove_uid("seq", seq)

    def clear(self) -> None:
        self._replace_all([])


# ─────────────────────────────────────────────────────────────────────────────
# Bridge
# ─────────────────────────────────────────────────────────────────────────────

class UiBridge:
    """The single mutation surface shared by Python and the renderer."""

    #: How often the microphone level may be broadcast (Hz).
    AMPLITUDE_HZ = 60.0
    #: Emit only when the level moved by at least this much.
    AMPLITUDE_EPSILON = 0.008

    def __init__(
        self,
        state: AssistantStateMachine,
        bus: EventBus,
        dispatcher: UiDispatcher,
        theme: dict | None = None,
    ) -> None:
        self._state = state
        self._bus = bus
        self._dispatcher = dispatcher
        #: Public handle: the controller starts/stops the loop thread.
        self.dispatcher = dispatcher

        self.cards = CardsModel()
        self.activities = ActivityModel()
        self.transcript = TranscriptModel()
        # Legacy private aliases (pre-migration names still referenced by docs
        # and by the verifier's diagnostics output).
        self._cards = self.cards
        self._activities = self.activities
        self._transcript = self.transcript

        # ── outbound wiring (set by the controller) ──────────────────────────
        #: ``fn(message_type: str, payload: dict)`` — broadcast one delta.
        self.on_emit: Callable[[str, dict], None] | None = None
        #: ``fn(text, origin)`` — the user submitted a typed turn.
        self.on_input: Callable[[str, str], None] | None = None
        #: ``fn(key)`` — the setup card handed us an API key.
        self.on_api_key: Callable[[str], None] | None = None
        #: ``fn()`` — the renderer asked to quit.
        self.on_quit: Callable[[], None] | None = None

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
        self._toast = {"text": "", "severity": "info"}
        self._inspector_open = False
        self._inspector_tab = ""
        self._setup_visible = False
        self._last_event = ""
        self._theme = dict(theme or {})
        self._clock = time.strftime("%H:%M:%S")
        self._dnd = False
        self._invoked = False
        self._wake = {"active": False, "status": "Engine idle",
                      "phrase": "Hey Friday"}
        self._mic_amplitude = 0.0
        self._last_amp_ts = 0.0
        self._persona = {"mode": "normal", "mood": "calm", "voice": "Aoede"}

        self._state.add_listener(self._on_transition)
        self._wire_bus()

    # ── outbound plumbing ────────────────────────────────────────────────────
    def _emit(self, kind: str, payload: dict | None = None) -> None:
        callback = self.on_emit
        if callback is None:
            return
        try:
            callback(kind, payload or {})
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[UiBridge] ⚠️ emit {kind} failed: {exc}")

    # ── bus wiring ───────────────────────────────────────────────────────────
    def _wire_bus(self) -> None:
        for topic in (Topics.LOG, Topics.CARD_ADD, Topics.CARD_CLEAR,
                      Topics.ACTIVITY_UPSERT, Topics.ACTIVITY_END):
            self._bus.subscribe(topic, self._on_bus_event)

    def _on_bus_event(self, payload: Any) -> None:
        # Invoked by EventBus.drain on the UI thread.
        try:
            self.ingest(payload)
        except Exception as exc:  # pragma: no cover
            print(f"[UiBridge] ⚠️ ingest failed: {exc}")

    # ── transcript ───────────────────────────────────────────────────────────
    def set_live_transcript(self, text: str) -> None:
        """Update the floating line without adding noise to transcript history."""
        clean = " ".join(str(text or "").split())[:240]
        if not clean or clean == self._last_event:
            return
        self._last_event = clean
        self._emit(protocol.T_LIVE_TRANSCRIPT, {"text": clean})

    def clear_live_transcript(self) -> None:  # noqa: N802
        """Drop the floating line — always, even if it already reads empty.

        Called when the overlay hides so re-summoning Siri can never flash the
        previous session's last line before the new one arrives. The emit is
        unconditional (unlike :meth:`set_live_transcript`, which de-duplicates)
        because the renderer keeps its own hold timer and needs the explicit
        clear on every dismissal.
        """
        self._last_event = ""
        self._emit(protocol.T_LIVE_TRANSCRIPT, {"text": ""})

    def ingest(self, payload: Any) -> None:
        """Single entry point for *any* backend content. UI thread only."""
        card, activities, event = card_mapper.to_cards(payload)
        if event is not None:
            self.transcript.add(event)
            self._emit(protocol.T_TRANSCRIPT_APPEND, event.to_dict())

            # Tool/system logs belong in History, not above the orb.
            if event.channel in (LogChannel.YOU, LogChannel.FRIDAY):
                self.set_live_transcript(event.text)

            implied = derive_state_for_log(event)
            if implied is not None and not self._state.muted:
                # Legacy parity: transcript lines nudge the orb state.
                current = self._state.state
                if implied is UiState.PROCESSING and current is UiState.LISTENING:
                    self._state.transition(UiState.THINKING, "log:you")
                elif implied is UiState.SPEAKING and current in (
                        UiState.LISTENING, UiState.THINKING, UiState.PROCESSING):
                    self._state.start_speaking()
        if card is not None:
            self.cards.add(card)
            self._emit_cards()
        for activity in activities:
            self.activities.upsert(activity)
        if activities:
            self._emit_activities()

    # ── state machine listener (UI thread) ───────────────────────────────────
    def _on_transition(self, tr: Transition) -> None:
        if not tr.changed:
            return
        self._emit_state()

    def _emit_state(self) -> None:
        self._emit(protocol.T_STATE, self.state_payload())

    def _emit_cards(self) -> None:
        self._emit(protocol.T_CARDS, {"items": self.cards.items()})

    def _emit_activities(self) -> None:
        self._emit(protocol.T_ACTIVITIES, {
            "items": self.activities.items(),
            "activeCount": self.activities.active_count(),
        })

    def _emit_vision(self) -> None:
        self._emit(protocol.T_VISION, self.vision_payload())

    def _emit_arcade(self) -> None:
        self._emit(protocol.T_ARCADE, self.arcade_payload())

    # ── payload builders (also used by snapshot()) ────────────────────────────
    def state_payload(self) -> dict:
        return {
            "state": self._state.state.value,
            "statusLabel": self._state.status_label(),
            "energy": self._state.visual_energy(),
            "muted": self._state.muted,
            "silentMode": self._state.silent_mode,
            "speaking": self._state.is_speaking,
            "connected": self._state.connected,
            "connecting": self._state.connecting,
            "connectionLabel": self.connectionLabel,
            "dnd": self._dnd,
            "invoked": self._invoked,
            "clock": self._clock,
            "micAmplitude": round(self._mic_amplitude, 4),
        }

    def persona_payload(self) -> dict:
        return dict(self._persona, badge=self.personaBadge)

    def vision_payload(self) -> dict:
        return {
            "signal": self._vision_signal,
            "mode": self._vision_mode,
            "previewOpen": self._vision_preview_open,
            "detections": list(self._vision_detections),
        }

    def arcade_payload(self) -> dict:
        return {
            "open": self._arcade_open,
            "game": self._arcade_game,
            "score": self._arcade_score,
            "lives": self._arcade_lives,
            "message": self._arcade_message,
        }

    def inspector_payload(self) -> dict:
        return {"open": self._inspector_open, "tab": self._inspector_tab,
                "props": self.inspector_props()}

    def inspector_props(self) -> dict:
        return {
            "wakePhrase": self._wake["phrase"],
            "wakeStatus": self._wake["status"],
            "wakeActive": self._wake["active"],
            "personaBadge": self.personaBadge,
            "theme": self._theme.get("name", "siri"),
            "reducedMotion": bool(self._theme.get("reducedMotion")),
            "transparency": bool(self._theme.get("transparency", True)),
            "alwaysOnTop": True,
            "captureExcluded": True,
            "visionSignal": self._vision_signal,
            "visionMode": self._vision_mode,
            "diagnostics": self.diagnostics(),
        }

    def snapshot(self) -> dict:
        """Full authoritative state, pushed on every (re)connect."""
        return {
            "t": protocol.T_SNAPSHOT,
            "state": self.state_payload(),
            "cards": self.cards.items(),
            "activities": self.activities.items(),
            "activeCount": self.activities.active_count(),
            "transcript": self.transcript.items(),
            "persona": self.persona_payload(),
            "vision": self.vision_payload(),
            "arcade": self.arcade_payload(),
            "setup": {"visible": self._setup_visible},
            "inspector": self.inspector_payload(),
            "theme": dict(self._theme),
            "wake": dict(self._wake),
        }

    # ── scalar properties (renderer + tests read these) ──────────────────────
    @property
    def state(self) -> str:
        return self._state.state.value

    @property
    def statusLabel(self) -> str:  # noqa: N802 - wire name
        return self._state.status_label()

    @property
    def energy(self) -> float:
        return self._state.visual_energy()

    @property
    def muted(self) -> bool:
        return self._state.muted

    @property
    def silentMode(self) -> bool:  # noqa: N802
        return self._state.silent_mode

    @property
    def speaking(self) -> bool:
        return self._state.is_speaking

    @property
    def connected(self) -> bool:
        return self._state.connected

    @property
    def connecting(self) -> bool:
        return self._state.connecting

    @property
    def connectionLabel(self) -> str:  # noqa: N802
        if self._state.connecting:
            return "CONNECTING"
        return "ONLINE" if self._state.connected else "OFFLINE"

    @property
    def clock(self) -> str:
        return self._clock

    @property
    def lastEvent(self) -> str:  # noqa: N802
        return self._last_event

    @property
    def dnd(self) -> bool:
        return self._dnd

    @property
    def invoked(self) -> bool:
        return self._invoked

    @property
    def micAmplitude(self) -> float:  # noqa: N802
        return self._mic_amplitude

    @property
    def personaMode(self) -> str:  # noqa: N802
        return self._persona["mode"]

    @property
    def personaMood(self) -> str:  # noqa: N802
        return self._persona["mood"]

    @property
    def personaVoice(self) -> str:  # noqa: N802
        return self._persona["voice"]

    @property
    def personaBadge(self) -> str:  # noqa: N802
        p = self._persona
        return f"{p['mode'].upper()} · {p['mood'].upper()} · {p['voice']}"

    @property
    def visionSignal(self) -> str:  # noqa: N802
        return self._vision_signal

    @property
    def visionMode(self) -> str:  # noqa: N802
        return self._vision_mode

    @property
    def visionPreviewOpen(self) -> bool:  # noqa: N802
        return self._vision_preview_open

    @property
    def visionDetections(self) -> list[dict]:  # noqa: N802
        return list(self._vision_detections)

    @property
    def arcadeOpen(self) -> bool:  # noqa: N802
        return self._arcade_open

    @property
    def arcadeGame(self) -> str:  # noqa: N802
        return self._arcade_game

    @property
    def arcadeScore(self) -> int:  # noqa: N802
        return self._arcade_score

    @property
    def arcadeLives(self) -> int:  # noqa: N802
        return self._arcade_lives

    @property
    def arcadeMessage(self) -> str:  # noqa: N802
        return self._arcade_message

    @property
    def toastText(self) -> str:  # noqa: N802
        return self._toast["text"]

    @property
    def toastSeverity(self) -> str:  # noqa: N802
        return self._toast["severity"]

    @property
    def inspectorOpen(self) -> bool:  # noqa: N802
        return self._inspector_open

    @property
    def inspectorTab(self) -> str:  # noqa: N802
        return self._inspector_tab

    @property
    def setupVisible(self) -> bool:  # noqa: N802
        return self._setup_visible

    @property
    def theme(self) -> dict:
        return dict(self._theme)

    @property
    def cardCount(self) -> int:  # noqa: N802
        return self.cards.count()

    @property
    def activeActivities(self) -> int:  # noqa: N802
        return self.activities.active_count()

    @property
    def awaitingConfirmation(self) -> bool:
        return any(item.get("kind") == CardKind.CONFIRMATION.value
                   for item in self.cards.raw())

    # ── microphone level ─────────────────────────────────────────────────────
    def setAmplitude(self, value: float) -> None:  # noqa: N802 - wire name
        """Live microphone level (0..1); feeds Siri's voice-reactive ribbons."""
        try:
            level = max(0.0, min(1.0, float(value or 0.0)))
        except (TypeError, ValueError):
            return
        previous = self._mic_amplitude
        self._mic_amplitude = level
        if abs(level - previous) < self.AMPLITUDE_EPSILON:
            return
        now = time.monotonic()
        if (now - self._last_amp_ts) < (1.0 / self.AMPLITUDE_HZ):
            return
        self._last_amp_ts = now
        self._emit(protocol.T_AMPLITUDE, {"value": round(level, 4)})

    # ── user intents (renderer / legacy text path) ───────────────────────────
    def submitText(self, text: str) -> None:  # noqa: N802
        text = (text or "").strip()
        if not text:
            return
        self._state.on_user_input(text)
        self.transcript.add(classify_legacy(f"You: {text}"))
        self.set_live_transcript(text)
        self._emit(protocol.T_TRANSCRIPT_APPEND, self.transcript.raw()[-1])
        callback = self.on_input
        if callback is not None:
            try:
                callback(text, "text")
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[UiBridge] ⚠️ input callback failed: {exc}")

    def submitApiKey(self, key: str) -> None:  # noqa: N802
        callback = self.on_api_key
        if callback is not None:
            callback(str(key or "").strip())

    def toggleMute(self) -> None:  # noqa: N802
        self._state.set_muted(not self._state.muted, "ui")

    def toggleSilent(self) -> None:  # noqa: N802
        self._state.set_silent_mode(not self._state.silent_mode, "ui")

    def cancel(self) -> None:
        self._state.on_user_cancelled()

    def bargeIn(self) -> None:  # noqa: N802
        self._state.barge_in("ui")

    def requestQuit(self) -> None:  # noqa: N802
        callback = self.on_quit
        if callback is not None:
            callback()

    # ── inspector / cards / transcript ───────────────────────────────────────
    def openInspector(self, tab: str = "") -> None:  # noqa: N802
        self._inspector_open = True
        self._inspector_tab = (tab or self._inspector_tab
                               or ("arcade" if self._arcade_open else "camera"))
        self._emit(protocol.T_INSPECTOR, self.inspector_payload())

    def closeInspector(self) -> None:  # noqa: N802
        self._inspector_open = False
        self._emit(protocol.T_INSPECTOR, self.inspector_payload())

    def dismissCard(self, uid: str) -> None:  # noqa: N802
        if self.cards.remove(uid):
            self._emit_cards()

    def clearCards(self) -> None:  # noqa: N802
        self.cards.clear()
        self._emit_cards()

    def clearTranscript(self) -> None:  # noqa: N802
        self.transcript.clear()
        self._emit(protocol.T_TRANSCRIPT_CLEAR, {})

    def cancelActivity(self, aid: str) -> None:  # noqa: N802
        if self.activities.end(aid):
            self._emit_activities()

    def dismissToast(self) -> None:  # noqa: N802
        self._toast = {"text": "", "severity": "info"}
        self._emit(protocol.T_TOAST, dict(self._toast))

    # ── arcade / effects ─────────────────────────────────────────────────────
    def setArcade(self, game: str, open_: bool) -> None:  # noqa: N802
        self._arcade_game = game or self._arcade_game
        self._arcade_open = bool(open_)
        self._emit_arcade()

    def setArcadeState(self, score: int, lives: int, message: str) -> None:  # noqa: N802
        self._arcade_score = int(score)
        self._arcade_lives = int(lives)
        self._arcade_message = message or ""
        self._emit_arcade()

    def toast(self, text: str, severity: str = "info") -> None:
        self._toast = {"text": str(text or "")[:200], "severity": severity}
        self._emit(protocol.T_TOAST, dict(self._toast))

    def showToast(self, text: str) -> None:  # noqa: N802
        self.toast(text, "info")

    # ── theme / persona / connection ─────────────────────────────────────────
    def apply_theme(self, theme: dict) -> None:
        self._theme = dict(theme)
        self._emit(protocol.T_THEME, dict(self._theme))

    def cycleTheme(self, name: str = "") -> None:  # noqa: N802
        from .themes import DEFAULT_THEME, THEME_NAMES, build_theme

        # Apple Siri is the sole theme of the product: cycling returns to the
        # same palette by design. The named-variant path stays for API parity.
        if name and name in THEME_NAMES:
            target = name
        else:
            try:
                idx = THEME_NAMES.index(self._theme.get("name") or DEFAULT_THEME)
            except ValueError:
                idx = -1
            target = THEME_NAMES[(idx + 1) % len(THEME_NAMES)]
        self.apply_theme(build_theme(target))

    def set_persona(self, mode: str, mood: str, voice: str) -> None:
        self._persona = {"mode": mode or "normal", "mood": mood or "calm",
                         "voice": voice or "Aoede"}
        self._emit(protocol.T_PERSONA, self.persona_payload())

    def set_connection(self, ok: bool) -> None:
        self._state.set_connection(bool(ok))
        self._emit_state()

    def set_connecting(self, on: bool = True) -> None:
        self._state.set_connecting(bool(on))
        self._emit_state()

    def set_dnd(self, dnd: bool) -> None:
        self._dnd = bool(dnd)
        self._emit_state()

    def set_invoked(self, invoked: bool) -> None:
        """Siri is on screen (orb summoned) / has been dismissed."""
        value = bool(invoked)
        if value != self._invoked:
            self._invoked = value
            self._emit_state()

    def set_wake(self, active: bool, status: str = "", phrase: str = "") -> None:
        self._wake = {
            "active": bool(active),
            "status": status or ("Listening" if active else "Engine idle"),
            "phrase": phrase or self._wake.get("phrase") or "Hey Friday",
        }
        self._emit(protocol.T_WAKE, dict(self._wake))
        if self._inspector_open:
            self._emit(protocol.T_INSPECTOR, self.inspector_payload())

    # ── vision ───────────────────────────────────────────────────────────────
    def set_vision_signal(self, text: str) -> None:
        value = str(text or "Awaiting camera input")[:160]
        if value == self._vision_signal:
            return
        self._vision_signal = value
        self._emit_vision()

    def set_vision_mode(self, mode: str) -> None:
        value = str(mode or "STANDBY").upper()
        if value == self._vision_mode:
            return
        self._vision_mode = value
        self._emit_vision()

    def set_vision_detections(self, detections: Iterable[dict]) -> None:
        self._vision_detections = list(detections)[:32]
        self._emit_vision()

    def set_vision_preview_open(self, open_: bool) -> None:
        value = bool(open_)
        if value == self._vision_preview_open:
            return
        self._vision_preview_open = value
        self._emit_vision()

    # ── setup ────────────────────────────────────────────────────────────────
    def set_setup_visible(self, visible: bool) -> None:
        value = bool(visible)
        if value == self._setup_visible:
            return
        self._setup_visible = value
        self._emit(protocol.T_SETUP, {"visible": value})

    # ── periodic housekeeping (4 Hz, UI thread) ───────────────────────────────
    def tick_clock(self) -> None:
        clock = time.strftime("%H:%M:%S")
        if clock != self._clock:
            self._clock = clock
            self._emit(protocol.T_STATE, self.state_payload())

    def expire_content(self) -> None:
        now = time.time()
        if self.cards.expire(now):
            self._emit_cards()
        if self.activities.prune():
            self._emit_activities()

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
            "bus": self._bus.stats(),
        }


__all__ = [
    "UiDispatcher",
    "UiBridge",
    "CardsModel",
    "ActivityModel",
    "TranscriptModel",
]
