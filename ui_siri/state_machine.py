"""FRIDAY ambient UI — interaction lifecycle state machine.

Pure Python, zero Qt. The machine is the single authority on "what is FRIDAY
doing right now"; the Qt bridge only mirrors it.

Why a machine at all? The legacy UI inferred its state from three unrelated
places (``set_state`` from main.py, ``write_log`` prefix side-effects, and
``muted``/``silent_mode`` booleans), which is why it could show "SPEAKING"
while muted. Here every transition is explicit and validated, and legacy
callers are funnelled through :meth:`coerce_legacy` so their semantics are
preserved without letting them corrupt the model.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from .event_bus import UiState

# ── Timing constants (ms) ────────────────────────────────────────────────────
# Kept module-level so tests can monkeypatch them.
THINKING_TIMEOUT_MS = 45_000     # reasoning that never completes → recover
SPEAKING_IDLE_MS = 1_200         # no audio chunk for this long → listening
BARGE_IN_GRACE_MS = 220          # debounce for wake-word barge-in
MUTE_DEBOUNCE_MS = 120


# Allowed transitions. Anything not listed is rejected (and logged) instead of
# silently applied — that is the whole point of having a machine.
_LEGAL: dict[UiState, frozenset[UiState]] = {
    UiState.BOOTING: frozenset({UiState.LISTENING, UiState.MUTED, UiState.THINKING}),
    UiState.LISTENING: frozenset({UiState.MUTED, UiState.THINKING, UiState.PROCESSING,
                                  UiState.SPEAKING, UiState.BOOTING}),
    UiState.THINKING: frozenset({UiState.SPEAKING, UiState.LISTENING, UiState.MUTED,
                                 UiState.PROCESSING}),
    UiState.PROCESSING: frozenset({UiState.SPEAKING, UiState.LISTENING, UiState.THINKING,
                                   UiState.MUTED}),
    UiState.SPEAKING: frozenset({UiState.LISTENING, UiState.MUTED, UiState.THINKING,
                                 UiState.PROCESSING}),
    UiState.MUTED: frozenset({UiState.LISTENING, UiState.THINKING, UiState.BOOTING}),
}


@dataclass(frozen=True, slots=True)
class Transition:
    """Immutable record of one state change."""

    previous: UiState
    current: UiState
    reason: str
    ts: float
    forced: bool = False
    rejected: bool = False

    @property
    def changed(self) -> bool:
        return self.previous is not self.current and not self.rejected


Listener = Callable[[Transition], None]


class AssistantStateMachine:
    """Six-state lifecycle controller with timers and barge-in.

    Thread-safety: all mutators take an ``RLock``. Listeners are invoked
    *outside* the lock so a listener that calls back into the machine cannot
    deadlock, and a throwing listener cannot corrupt state.
    """

    def __init__(self, initial: UiState = UiState.BOOTING) -> None:
        self._state = initial
        self._lock = threading.RLock()
        self._listeners: list[Listener] = []
        self._history: list[Transition] = []
        self._max_history = 64
        self._last_change = time.monotonic()

        # orthogonal flags — NOT states
        self._muted = False
        self._silent = False            # text-only mode (see contract defect D)
        self._connected = False
        self._connecting = False
        self._speaking_audio = False

        # timers
        self._thinking_since: float | None = None
        self._last_audio_ts = 0.0
        self._last_barge_ts = 0.0
        self._barge_pending = False

        self._reason = "boot"

    # ── introspection ────────────────────────────────────────────────────────
    @property
    def state(self) -> UiState:
        return self._state

    @property
    def previous_state(self) -> UiState:
        with self._lock:
            return self._history[-1].previous if self._history else UiState.BOOTING

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def muted(self) -> bool:
        return self._muted

    @property
    def silent_mode(self) -> bool:
        return self._silent

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def connecting(self) -> bool:
        return self._connecting

    @property
    def is_speaking(self) -> bool:
        return self._state is UiState.SPEAKING

    @property
    def history(self) -> tuple[Transition, ...]:
        with self._lock:
            return tuple(self._history)

    def snapshot(self) -> dict:
        """Scalar snapshot for QML properties."""
        return {
            "state": self._state.value,
            "muted": self._muted,
            "silent": self._silent,
            "connected": self._connected,
            "connecting": self._connecting,
            "speaking": self.is_speaking,
            "reason": self._reason,
        }

    # ── listeners ────────────────────────────────────────────────────────────
    def add_listener(self, fn: Listener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(fn)

        def _unsub() -> None:
            with self._lock:
                try:
                    self._listeners.remove(fn)
                except ValueError:
                    pass

        return _unsub

    # ── flags ────────────────────────────────────────────────────────────────
    def set_muted(self, muted: bool, reason: str = "mute") -> Transition:
        """Mute is *orthogonal*: it forces MUTED and restores on unmute."""
        muted = bool(muted)
        with self._lock:
            changed = muted != self._muted
            self._muted = muted
        if changed and muted:
            with self._lock:
                self._speaking_audio = False
            return self.transition(UiState.MUTED, reason, force=True)
        if changed and not muted:
            # Legacy ``_toggle_mute`` semantics: unmuting means "microphone
            # active, listening" — a stale SPEAKING flag must not survive a
            # mute cycle (contract defect D).
            with self._lock:
                self._speaking_audio = False
            return self.transition(UiState.LISTENING, f"{reason}:unmute", force=True)
        # No flag change: still re-assert the visual state.
        return self.transition(UiState.MUTED if muted else self._resume_target(),
                               reason, force=True)

    def set_silent_mode(self, silent: bool, reason: str = "text-only") -> Transition:
        """Text-only mode.

        Contract defect D: legacy latched this forever. We keep the flag but
        only suppress *speech rendering*; the state machine stays coherent and
        an unmute always escapes it.
        """
        silent = bool(silent)
        with self._lock:
            self._silent = silent
        if silent:
            self._speaking_audio = False
            return self.transition(UiState.THINKING, f"{reason}:on", force=True)
        return self.transition(self._resume_target(), f"{reason}:off", force=True)

    def set_connection(self, connected: bool) -> None:
        with self._lock:
            self._connected = bool(connected)
            if connected:
                self._connecting = False

    def set_connecting(self, connecting: bool) -> None:
        with self._lock:
            self._connecting = bool(connecting)
            if connecting:
                self._connected = False

    # ── core transitions ─────────────────────────────────────────────────────
    def transition(self, target: UiState, reason: str = "", force: bool = False) -> Transition:
        target = UiState.coerce(target)
        with self._lock:
            prev = self._state
            if prev is target:
                tr = Transition(prev, target, reason, time.time())
                self._reason = reason
                return tr

            legal = target in _LEGAL.get(prev, frozenset())
            if not legal and not force:
                tr = Transition(prev, target, reason, time.time(), rejected=True)
                self._push_history(tr)
                return tr

            self._state = target
            self._reason = reason
            now = time.monotonic()
            self._last_change = now
            if target is UiState.THINKING:
                self._thinking_since = now
            else:
                self._thinking_since = None
            if target is UiState.SPEAKING:
                self._last_audio_ts = now

            tr = Transition(prev, target, reason, time.time(), forced=not legal)
            self._push_history(tr)

        for fn in tuple(self._listeners):
            try:
                fn(tr)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[StateMachine] ⚠️ listener error: {exc}")
        return tr

    def _push_history(self, tr: Transition) -> None:
        self._history.append(tr)
        if len(self._history) > self._max_history:
            del self._history[: len(self._history) - self._max_history]

    def _resume_target(self) -> UiState:
        if self._muted:
            return UiState.MUTED
        if self._speaking_audio and not self._silent:
            return UiState.SPEAKING
        return UiState.LISTENING

    # ── legacy coercion ──────────────────────────────────────────────────────
    def coerce_legacy(self, value: object, reason: str = "legacy") -> Transition:
        """Accept the strings ``main.py`` actually passes (contract §3.4)."""
        raw = "" if value is None else str(value)
        upper = raw.strip().upper()
        if upper == "MUTED":
            return self.set_muted(True, reason)
        if upper == "SPEAKING":
            with self._lock:
                self._speaking_audio = True
                self._muted = False
            return self.transition(UiState.SPEAKING, reason, force=True)
        if upper in ("THINKING", "PROCESSING"):
            with self._lock:
                self._speaking_audio = False
            return self.transition(UiState.coerce(upper), reason, force=True)
        if upper in ("LISTENING", "ONLINE", "IDLE", ""):
            with self._lock:
                self._speaking_audio = False
            if self._muted:
                return self.transition(UiState.MUTED, reason, force=True)
            return self.transition(UiState.LISTENING, reason, force=True)
        if upper in ("TEXT-ONLY", "TEXT_ONLY", "TEXTONLY"):
            return self.set_silent_mode(True, reason)
        return self.transition(UiState.coerce(raw), reason, force=True)

    # ── speech events ────────────────────────────────────────────────────────
    def on_audio_chunk(self) -> None:
        """First audio chunk of a turn flips us into SPEAKING."""
        with self._lock:
            self._last_audio_ts = time.monotonic()
            if self._silent or self._muted:
                return
            already = self._state is UiState.SPEAKING
            self._speaking_audio = True
        if not already:
            # Real audio arriving is ground truth: it outranks whatever the
            # orchestrator last claimed, including a BOOTING state.
            self.transition(UiState.SPEAKING, "audio", force=True)

    def on_turn_complete(self) -> Transition:
        with self._lock:
            self._speaking_audio = False
        return self.transition(self._resume_target(), "turn-complete", force=True)

    def start_speaking(self) -> None:
        with self._lock:
            self._speaking_audio = True
        self.transition(UiState.SPEAKING, "speak()", force=True)

    def stop_speaking(self) -> None:
        with self._lock:
            self._speaking_audio = False
        self.transition(self._resume_target(), "stop_speaking()", force=True)

    # ── barge-in ─────────────────────────────────────────────────────────────
    def on_wake_word(self) -> Transition:
        """Wake word = interrupt anyone, unmute, listen.

        Debounced by ``BARGE_IN_GRACE_MS`` so a double detection doesn't cause
        two speaker interruptions.
        """
        now = time.monotonic()
        with self._lock:
            if self._barge_pending or (now - self._last_barge_ts) * 1000 < BARGE_IN_GRACE_MS:
                return Transition(self._state, self._state, "wake:debounced", time.time())
            self._barge_pending = True
            self._last_barge_ts = now
            self._muted = False
            self._silent = False
            self._speaking_audio = False
        try:
            return self.transition(UiState.LISTENING, "wake-word", force=True)
        finally:
            with self._lock:
                self._barge_pending = False

    def barge_in(self, reason: str = "user") -> Transition:
        """Unconditional interruption (user started typing / pressed the orb)."""
        with self._lock:
            self._muted = False
            self._silent = False
            self._speaking_audio = False
        return self.transition(UiState.LISTENING, f"barge-in:{reason}", force=True)

    # ── user intent ──────────────────────────────────────────────────────────
    def on_user_input(self, text: str = "") -> Transition:
        """User typed or spoke a command."""
        with self._lock:
            self._speaking_audio = False
            self._silent = self._silent  # unchanged
        return self.transition(UiState.THINKING, "user-input", force=True)

    def on_user_cancelled(self) -> Transition:
        with self._lock:
            self._speaking_audio = False
        return self.transition(self._resume_target(), "user-cancel", force=True)

    # ── timers ───────────────────────────────────────────────────────────────
    def tick(self, now: float | None = None) -> Transition | None:
        """Called ~4×/s from the GUI. Recovers from stuck states."""
        now_m = time.monotonic() if now is None else now
        with self._lock:
            state = self._state
            thinking_since = self._thinking_since
            last_audio = self._last_audio_ts
            silent = self._silent

        if state is UiState.THINKING and thinking_since is not None:
            if (now_m - thinking_since) * 1000 > THINKING_TIMEOUT_MS:
                return self.transition(UiState.LISTENING, "timeout:thinking", force=True)

        if state is UiState.SPEAKING and not silent:
            if (now_m - last_audio) * 1000 > SPEAKING_IDLE_MS:
                return self.on_turn_complete()
        return None

    # ── visual hints consumed by QML ─────────────────────────────────────────
    def visual_energy(self) -> float:
        """0..1 scalar the orb uses for glow intensity."""
        state = self._state
        if self._muted:
            return 0.18
        if state is UiState.SPEAKING:
            return 1.0
        if state in (UiState.THINKING, UiState.PROCESSING):
            return 0.62
        if state is UiState.LISTENING:
            return 0.42
        return 0.24

    def status_label(self) -> str:
        if self._muted:
            return "MUTED"
        if self._silent:
            return "TEXT-ONLY"
        return self._state.value


__all__ = [
    "AssistantStateMachine",
    "Transition",
    "THINKING_TIMEOUT_MS",
    "SPEAKING_IDLE_MS",
    "BARGE_IN_GRACE_MS",
]
