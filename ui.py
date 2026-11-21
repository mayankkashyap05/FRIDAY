"""FRIDAY — public UI facade.

This module is the **only** entry point ``main.py`` and ``actions/`` may import.
It preserves, byte for byte in behaviour, every attribute and method the
legacy Tk implementation exposed (see ``docs/UI_AUDIT_CONTRACT.md`` §3), while
delegating the actual pixels to the ambient Siri interface in
:mod:`ui_siri`. ``ui_siri`` is the sole UI of the application; there is no
Tk fallback, no legacy theme switch, and no parallel UI engine.

Threading model
---------------
``main.py`` keeps its shape: ``mainloop()``/``exec()`` on the main OS thread,
the asyncio voice loop on a worker thread. Every public method below is safe to
call from **any** thread: it routes through :class:`_Dispatcher` which either
runs inline (GUI thread) or queues a Qt signal (producer thread) and returns
immediately.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
import time
from pathlib import Path
from typing import Any, Callable

# ─────────────────────────────────────────────────────────────────────────────
# Module-level configuration — USED BY tests/test_p0_security.py
# ─────────────────────────────────────────────────────────────────────────────


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR = get_base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE = CONFIG_DIR / "api_keys.json"
THEME_FILE = CONFIG_DIR / "ui_theme.json"

SYSTEM_NAME = "FRIDAY"
MODEL_BADGE = "FRIDAY"

# Kept for parity with the legacy module: callers/tests may read THEMES.
# The Qt-free theme module, so `import ui` never loads Qt. Siri is the sole
# theme now — the legacy cyber/ocean/aurora/sunset variants were removed with
# ui_legacy.py; `ui_siri.themes` exports exactly one palette, "siri".
from ui_siri.themes import DEFAULT_THEME, THEMES, THEME_NAMES  # noqa: F401


def _load_theme_name() -> str:
    """Read the persisted theme (same file the Siri UI writes)."""
    try:
        if THEME_FILE.exists():
            data = json.loads(THEME_FILE.read_text(encoding="utf-8"))
            name = str(data.get("theme", DEFAULT_THEME)).strip().lower()
            if name in THEME_NAMES:
                return name
    except Exception:
        pass
    return DEFAULT_THEME


def _save_theme_name(name: str) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        THEME_FILE.write_text(json.dumps({"theme": name}, indent=2), encoding="utf-8")
    except Exception:
        pass


def _log_exception(exc: BaseException) -> None:
    """Append a traceback to the application log, if logging is up.

    ``logger.log`` is already used across main.py; it may not be imported yet
    when make_ui() runs, and it must never mask the real failure.
    """
    try:
        from logger import log

        log(f"UI: ambient interface failed: {type(exc).__name__}: {exc}",
            level="ERROR")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Thread marshalling
# ─────────────────────────────────────────────────────────────────────────────

class _ImmediateDispatcher:
    """Fallback for the degraded path (no Qt): run inline, never queue."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: list[tuple[Callable, tuple, dict]] = []

    def call(self, fn: Callable, *args: Any, **kwargs: Any) -> bool:
        with self._lock:
            if self.on_gui_thread:
                try:
                    fn(*args, **kwargs)
                except Exception as exc:
                    print(f"[UI] ⚠️ {getattr(fn, '__name__', fn)}: {exc}")
                return True
            self._pending.append((fn, args, kwargs))
            return False

    @property
    def on_gui_thread(self) -> bool:
        return True

    def drain(self) -> int:
        with self._lock:
            pending, self._pending = self._pending, []
        for fn, args, kwargs in pending:
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                print(f"[UI] ⚠️ deferred {getattr(fn, '__name__', fn)}: {exc}")
        return len(pending)


# ─────────────────────────────────────────────────────────────────────────────
# Adapter that makes the Qt app look like a Tk root
# ─────────────────────────────────────────────────────────────────────────────

class _QtRootAdapter:
    """Duck-typed ``root`` so ``main.py``'s legacy calls keep working.

    Legacy usage (audit contract §3.5)::

        root.after(0, cb)        # from the pystray / keyboard threads  ← Bug A
        root.state()             # "normal" | "withdrawn"
        root.withdraw() / deiconify() / lift()
        root.destroy()
        root.mainloop()
        root.report_callback_exception = fn

    ``after`` is the important one: it is called from **non-GUI threads**, which
    the Tk version could not do safely. Here it posts onto the Qt queue, so it
    is both thread-safe and non-blocking.
    """

    def __init__(self, ui: "FridayUI") -> None:
        self._ui = ui
        self.report_callback_exception: Callable | None = None

    # ── thread-safe scheduling ───────────────────────────────────────────────
    def after(self, ms: int, fn: Callable | None = None, *args: Any) -> str:
        if fn is None:
            return ""
        delay = max(0, int(ms or 0))
        if delay == 0:
            self._ui._dispatch(fn, *args)
            return f"after#{time.monotonic_ns()}"
        timer = threading.Timer(delay / 1000.0,
                                lambda: self._ui._dispatch(fn, *args))
        timer.daemon = True
        timer.start()
        return f"after#{time.monotonic_ns()}"

    def after_cancel(self, _token: Any) -> None:
        return None

    # ── window state ─────────────────────────────────────────────────────────
    def state(self) -> str:
        return "normal" if self._ui.visible else "withdrawn"

    def withdraw(self) -> None:
        self._ui.hide()

    def deiconify(self) -> None:
        self._ui.show()

    def lift(self) -> None:
        self._ui.raise_window()

    def iconify(self) -> None:
        self._ui.hide()

    def destroy(self) -> None:
        self._ui.quit()

    def quit(self) -> None:
        self._ui.quit()

    # ── geometry ─────────────────────────────────────────────────────────────
    def winfo_screenwidth(self) -> int:
        return self._ui.screen_size()[0]

    def winfo_screenheight(self) -> int:
        return self._ui.screen_size()[1]

    def geometry(self, *_args: Any) -> str:
        w, h = self._ui.window_size()
        return f"{w}x{h}"

    def title(self, *_args: Any) -> None:
        return None

    def configure(self, **_kwargs: Any) -> None:
        return None

    def bind(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def protocol(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def mainloop(self, *_args: Any) -> int:
        return self._ui.run()

    def update(self) -> None:
        return None

    def update_idletasks(self) -> None:
        return None

    def tk(self) -> None:  # legacy code guards this with try/except
        raise AttributeError("tk")

    def __getattr__(self, name: str) -> Any:
        # Unknown Tk attributes degrade to a no-op callable rather than an
        # AttributeError halfway through a shutdown path.
        def _noop(*_a: Any, **_k: Any) -> None:
            return None

        if name.startswith("winfo_") or name in ("update", "update_idletasks"):
            return _noop
        raise AttributeError(name)


# ─────────────────────────────────────────────────────────────────────────────
# Ambient Qt Quick implementation
# ─────────────────────────────────────────────────────────────────────────────

class FridayUI:
    """Ambient, zero-chrome FRIDAY overlay.

    Constructs the Qt application object and all overlay layers, but does **not**
    enter the event loop — ``main.py`` calls :meth:`run` (via ``root.mainloop``)
    on the main OS thread, exactly as before.
    """

    #: True so callers can feature-detect without importing ui_siri.
    ambient = True

    def __init__(self, face_path: str = "face.png", size: Any = None) -> None:
        self._face_path = str(face_path)
        self._size = size                    # accepted, unused (parity)
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._loop_entered = False

        # ── legacy public attributes (written directly by main.py) ───────────
        self.muted = False
        self.silent_mode = False
        self._last_silent = False
        self.speaking = False
        self.connected = False
        self.connecting = False
        self.on_text_command = None          # Callable[[str], None] | None
        self.vision_preview_open = False
        self.arcade_open = False
        self.vision_mode = "STANDBY"
        self.vision_signal = "Awaiting camera input"
        self.persona_mode = "normal"
        self.persona_mood = "calm"
        self.persona_voice = "Aoede"
        self.last_event = ""
        self._state = "INITIALISING"
        self.status_text = "INITIALISING"

        self._api_key_ready = self._api_keys_exist()
        self._started = time.time()
        #: Log lines that arrived before the event loop was running.
        self._pending_cards: list[Any] = []
        self._MAX_PENDING_LOGS = 200

        # ── Qt side ──────────────────────────────────────────────────────────
        from PySide6.QtGui import QGuiApplication

        from ui_siri.audio_ux import AudioUX
        from ui_siri.controller import UiController

        self._app = QGuiApplication.instance()
        self._owns_app = self._app is None
        if self._app is None:
            from ui_siri import win32_native as w32

            # Both of these MUST run before the QGuiApplication exists: Qt
            # reads the alpha-buffer flag and the DPI-awareness context once,
            # at platform-window creation.
            w32.enable_alpha_buffer()
            w32.enable_per_monitor_dpi()
            self._app = QGuiApplication(sys.argv[:1] or ["friday"])
        try:
            self._app.setApplicationName("FRIDAY")
            self._app.setQuitOnLastWindowClosed(False)
        except Exception:
            pass

        theme = _load_theme_name()
        # Dormant by design: the Siri overlay is constructed but never shown
        # until an explicit invocation (wake word, hotkey, tray, programmatic).
        self.controller = UiController(self._app, theme_name=theme,
                                       audio=AudioUX(enabled=True, ducking=False),
                                       start_hidden=True)
        self.bridge = self.controller.bridge
        self.state_machine = self.controller.state

        # Wire QML intents -> the legacy backend callback.
        self.bridge.inputRequested.connect(self._on_qml_submit)
        self.bridge.toastRequested.connect(self._on_qml_toast)
        self.controller.apiKeySubmitted.connect(self._on_qml_api_key)

        # Mirror the state machine onto the legacy attributes.
        self.state_machine.add_listener(self._on_transition)

        self.root = _QtRootAdapter(self)

        # Setup prompt when no key is configured (parity with the Tk flow).
        if not self._api_key_ready:
            self.bridge.set_setup_visible(True)

        self.set_state("LISTENING")

    # ── identity / introspection ─────────────────────────────────────────────
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FridayUI ambient state={self._state}>"

    @property
    def visible(self) -> bool:
        return bool(self.controller.visible)

    def screen_size(self) -> tuple[int, int]:
        try:
            screen = self.controller.active_screen() or self._app.primaryScreen()
            if screen is not None:
                g = screen.geometry()
                return (g.width(), g.height())
        except Exception:
            pass
        return (1920, 1080)

    def window_size(self) -> tuple[int, int]:
        try:
            win = self.controller.anchor
            if win is not None:
                g = win.geometry()
                return (g.width(), g.height())
        except Exception:
            pass
        return (456, 216)

    # ── dispatch plumbing ────────────────────────────────────────────────────
    def _dispatch(self, fn: Callable, *args: Any, **kwargs: Any) -> None:
        """Run ``fn`` on the GUI thread. Safe from any thread."""
        if self._closed.is_set():
            return
        try:
            self.controller.dispatcher.call(fn, *args, **kwargs)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[UI] ⚠️ dispatch failed: {exc}")

    def _safe_ui(self, fn: Callable, *args: Any) -> None:
        """Legacy private name used by ``main.py`` (Telegram /mute)."""
        self._dispatch(fn, *args)

    def _on_transition(self, tr) -> None:
        snap = self.state_machine.snapshot()
        self._state = snap["state"]
        self.status_text = snap["state"]
        self.muted = snap["muted"]
        # The machine is the owner; track its transitions into/out of text-only
        # mode so main.py's speech sink can be told to flush pending audio.
        if snap["silent"] != self._last_silent:
            self._last_silent = snap["silent"]
            self._on_silent_toggled(snap["silent"])
        self.silent_mode = snap["silent"]
        self.speaking = snap["speaking"]
        self.connected = snap["connected"]
        self.connecting = snap["connecting"]

    def _on_silent_toggled(self, silent: bool) -> None:
        """Text-only mode is a *suppression* switch, not just a label.

        ``main.py`` skips TTS whenever ``ui.silent_mode`` is true, but audio
        already queued in the Gemini live session would keep playing. Ask the
        backend (if it exposed a hook) to drop what is in flight.
        """
        hook = getattr(self, "on_silent_change", None)
        if callable(hook):
            try:
                hook(bool(silent))
            except Exception as exc:
                print(f"[UI] ⚠️ on_silent_change failed: {exc}")

    # ── QML -> backend ───────────────────────────────────────────────────────
    def _on_qml_submit(self, text: str, origin: str) -> None:
        self.write_log(f"You: {text}")
        callback = self.on_text_command
        if callback is not None:
            threading.Thread(target=callback, args=(text,), daemon=True,
                             name="UiTextCommand").start()

    def _on_qml_api_key(self, key: str) -> None:
        if not self.submit_api_key(key):
            self.write_log("ERR: That API key could not be saved.")

    def _on_qml_toast(self, text: str, severity: str) -> None:
        if text:
            self.write_log(f"SYS: {text}")

    # ── public API — state / connection / persona ────────────────────────────
    def set_state(self, state: str) -> None:
        """Legacy entry point. Accepts the exact literals main.py passes."""
        self._dispatch(self.state_machine.coerce_legacy, str(state or ""))

    def set_connection(self, ok: bool) -> None:
        self.connected = bool(ok)
        self.connecting = False
        self._dispatch(self.bridge.set_connection, bool(ok))

    def set_connecting(self, on: bool = True) -> None:
        self.connecting = bool(on)
        self._dispatch(self.bridge.set_connecting, bool(on))

    def set_persona(self, mode: str, mood: str, voice: str) -> None:
        self.persona_mode = mode or "normal"
        self.persona_mood = mood or "calm"
        self.persona_voice = voice or "Aoede"
        self._dispatch(self.bridge.set_persona, self.persona_mode,
                       self.persona_mood, self.persona_voice)

    def start_speaking(self) -> None:
        self._dispatch(self.state_machine.start_speaking)

    def stop_speaking(self) -> None:
        self.connected = self.connected
        if not self.muted and not self.silent_mode:
            self._dispatch(self.state_machine.stop_speaking)
        else:
            # Muted / text-only: the branch above used to do *nothing*, so the
            # machine stayed on SPEAKING and the controller never restored the
            # audio it had ducked. release_speech() always emits a transition.
            self._dispatch(self.state_machine.release_speech, "stop_speaking")

    # ── public API — transcript ──────────────────────────────────────────────
    def write_log(self, text: str) -> None:
        """The single most-used legacy sink (30 call sites in actions/).

        Before the event loop starts (``wait_for_api_key`` runs on the worker
        thread while the main thread has not reached ``exec()`` yet), logs are
        coalesced instead of queued: 30 modules start talking at once at
        launch, and an unbounded queue of transcript lines would all be
        replayed into the UI in the first 8 ms. Only the tail is interesting.
        """
        if text is None:
            return
        payload = str(text)
        if not self._loop_entered:
            self._pending_cards.append(payload)
            if len(self._pending_cards) > self._MAX_PENDING_LOGS:
                del self._pending_cards[: len(self._pending_cards) // 2]
            return
        self._dispatch(self._ingest, payload)

    def _flush_pending_logs(self) -> None:
        if not self._pending_cards:
            return
        pending, self._pending_cards = self._pending_cards, []
        for payload in pending:
            self._ingest(payload)

    def _ingest(self, payload: Any) -> None:
        self.bridge.ingest(payload)

    # ── public API — mute / silent (also used by the tray) ───────────────────
    def _toggle_mute(self) -> None:
        self._dispatch(self._toggle_mute_gui)

    def _toggle_mute_gui(self) -> None:
        target = not self.state_machine.muted
        self.state_machine.set_muted(target, "toggle_mute")
        self.muted = target
        self.write_log("SYS: Microphone muted." if target
                       else "SYS: Microphone active.")
        if target:
            self.controller.audio.play("mute")

    def _draw_mute_button(self) -> None:
        """Legacy repaint hook (called from main.py's Telegram /mute).

        There is no canvas to redraw any more; the QML orb binds to
        ``friday.muted``. We push the current flag so a *direct* attribute
        write from Telegram still lands on screen.
        """
        self._dispatch(self._sync_muted_flag)

    def _sync_muted_flag(self) -> None:
        want = bool(self.muted)
        if want != self.state_machine.muted:
            self.state_machine.set_muted(want, "external")

    def _toggle_silent_mode(self) -> None:
        self._dispatch(self._toggle_silent_gui)

    def _toggle_silent_gui(self) -> None:
        target = not self.state_machine.silent_mode
        self.state_machine.set_silent_mode(target, "toggle_silent")
        self.silent_mode = target
        self.write_log("SYS: Text-only mode enabled — Friday will type, not speak."
                       if target else "SYS: Voice output restored.")

    # ── public API — theme ───────────────────────────────────────────────────
    def set_theme(self, name: str) -> None:
        key = str(name or "").strip().lower()
        if key not in THEME_NAMES:
            return
        _save_theme_name(key)
        self._dispatch(self.controller.apply_theme, key)

    def cycle_theme(self) -> None:
        def _cycle() -> None:
            name = self.controller.cycle_theme()
            _save_theme_name(name)
            self.write_log(f"SYS: Theme → {name.upper()}.")

        self._dispatch(_cycle)

    # ── public API — vision (called from VisionGestureThread) ────────────────
    def set_vision_signal(self, text: str) -> None:
        self.vision_signal = str(text or "Awaiting camera input")[:160]
        self._dispatch(self.bridge.set_vision_signal, self.vision_signal)

    def set_vision_detections(self, events: Any) -> None:
        items = []
        for event in events or []:
            label = getattr(event, "label", None)
            if label is None and isinstance(event, dict):
                label = event.get("label")
            if label is None:
                continue
            items.append({
                "label": str(label),
                "confidence": float(getattr(event, "confidence", 0.0) or 0.0),
                "box": list(getattr(event, "box", []) or []),
            })
        self._dispatch(self.bridge.set_vision_detections, items)

    def set_vision_frame(self, frame: Any) -> None:
        """Receive one privacy-filtered BGR frame (GUI-free path)."""
        if not self.vision_preview_open or frame is None:
            return
        try:
            self.controller.push_frame("vision", frame)
        except Exception:
            pass

    def open_vision_preview(self) -> None:
        self.vision_preview_open = True
        self.vision_mode = "PREVIEW"

        def _open() -> None:
            self.bridge.set_vision_preview_open(True)
            self.bridge.set_vision_mode("PREVIEW")
            self.controller.open_inspector("camera")

        self._dispatch(_open)
        self.write_log("SYS: Vision preview opened.")

    def close_vision_preview(self) -> None:
        self.vision_preview_open = False

        def _close() -> None:
            self.bridge.set_vision_preview_open(False)

        self._dispatch(_close)

    def set_arcade_frame(self, frame: Any) -> None:
        if not self.arcade_open or frame is None:
            return
        try:
            self.controller.push_frame("arcade", frame)
        except Exception:
            pass

    def set_arcade_signal(self, gesture: str, x: float = 0.5) -> None:
        if not self.arcade_open:
            return
        self._dispatch(self.bridge.set_vision_signal,
                       f"Arcade · {gesture} @ {float(x):.2f}")

    def set_arcade_pose(self, arms_raised: bool, lean: float = 0.0) -> None:
        if not self.arcade_open:
            return
        self._dispatch(self.bridge.set_vision_signal,
                       f"Pose · arms={'up' if arms_raised else 'down'} "
                       f"lean={float(lean):.2f}")

    def open_vision_arcade(self) -> None:
        self.arcade_open = True

        def _open() -> None:
            self.bridge.setArcade("face_filter", True)
            self.bridge.set_vision_mode("ARCADE")
            self.controller.open_inspector("arcade")

        self._dispatch(_open)
        self.write_log("SYS: Vision Arcade → face_filter")
        callback = self.on_text_command
        if callback is not None:
            threading.Thread(target=callback, args=("start vision gesture",),
                             daemon=True, name="UiArcadeStart").start()

    def close_vision_arcade(self) -> None:
        self.arcade_open = False
        self._dispatch(self.bridge.setArcade, "face_filter", False)

    # ── public API — window control (tray / hotkeys) ─────────────────────────
    def show(self) -> None:
        self._dispatch(self.controller.show)

    def hide(self) -> None:
        self._dispatch(self.controller.hide)

    def invoke(self) -> None:
        """Explicit Siri invocation: the orb pops into view (spring bloom)."""
        self.show()

    def dismiss(self) -> None:
        """Graceful Siri dismissal: orb and cards dissolve to invisibility."""
        self.hide()

    def raise_window(self) -> None:
        def _raise() -> None:
            win = self.controller.anchor
            if win is not None:
                win.show()
                win.requestActivate()

        self._dispatch(_raise)

    def quit(self) -> None:
        if self._closed.is_set():
            return

        def _quit() -> None:
            self._closed.set()
            try:
                self.controller.shutdown()
            except Exception:
                pass
            try:
                self._app.quit()
            except Exception:
                os._exit(0)

        try:
            self._dispatch(_quit)
        except Exception:
            os._exit(0)

    # ── event loop ───────────────────────────────────────────────────────────
    def run(self) -> int:
        """Enter the Qt event loop. MUST be called on the main OS thread."""
        self._loop_entered = True
        # Replay what was logged during startup, once, from the GUI thread.
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._flush_pending_logs)
        except Exception:
            self._flush_pending_logs()
        # Ctrl+C: turn it into an orderly teardown. Installed here (not in the
        # controller's __init__, which the verifier and the tests also use) so
        # only a real interactive run owns the process-wide handler. Must happen
        # before exec(), otherwise the first Python frame SIGINT reaches is a
        # timer callback inside UiDispatcher.drain and it escapes as a bare
        # KeyboardInterrupt traceback with no teardown at all.
        if not self.controller.install_interrupt_handler():
            print("[UI] ⚠️ Could not install the Ctrl+C handler "
                  "(not on the main thread?) — use the tray Quit item.")

        code = 0
        try:
            code = int(self._app.exec())
        except KeyboardInterrupt:
            # Belt and braces: a SIGINT that slips past the handler.
            code = 130
        finally:
            try:
                self.controller.shutdown()
            except Exception:
                pass
        return code

    # Legacy alias some scripts call directly.
    def mainloop(self) -> int:
        return self.run()

    # ── API key bootstrap (parity with the Tk setup dialog) ──────────────────
    def _api_keys_exist(self) -> bool:
        """A usable Gemini key must exist (env or config), not just a file.

        NOTE: intentionally tolerant of ``self`` being a bare ``Stub()`` —
        ``tests/test_p0_security.py`` binds this unbound to a stub instance.
        """
        if os.environ.get("FRIDAY_GEMINI_API_KEY", "").strip():
            return True
        if os.environ.get("FRIDAY_GEMINI_API_KEYS", "").strip():
            return True
        try:
            path = API_FILE
            cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return False
        if isinstance(cfg.get("gemini_api_key"), str) and cfg["gemini_api_key"].strip():
            return True
        keys = cfg.get("gemini_api_keys")
        return bool(isinstance(keys, list)
                    and any(isinstance(k, str) and k.strip() for k in keys))

    def wait_for_api_key(self) -> None:
        """Block until a key exists. Same contract as the Tk version.

        Contract defect G: the Tk version spun forever with ``sleep(0.1)``. We
        keep the blocking semantics (the voice worker must not start without a
        key) but stop cleanly on shutdown, and re-check the file at 4 Hz so a
        key added by the setup card is picked up immediately.
        """
        while not self._api_key_ready:
            if self._closed.is_set():
                return
            time.sleep(0.25)
            if self._api_keys_exist():
                self._api_key_ready = True
                self._dispatch(self.bridge.set_setup_visible, False)
                self.write_log("SYS: Systems initialised. Friday online.")
                return

    def submit_api_key(self, key: str) -> bool:
        """Called by the setup card once the user pastes a key."""
        key = (key or "").strip()
        if not key:
            return False
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            API_FILE.write_text(json.dumps({"gemini_api_key": key}, indent=4),
                                encoding="utf-8")
        except Exception as exc:
            print(f"[UI] ⚠️ could not write API key: {exc}")
            return False
        self._api_key_ready = True
        self._dispatch(self.bridge.set_setup_visible, False)
        self.set_state("LISTENING")
        self.write_log("SYS: Systems initialised. Friday online.")
        return True

    # ── diagnostics ──────────────────────────────────────────────────────────
    def diagnostics(self) -> dict:
        data = {"legacy": False, "uptime_s": round(time.time() - self._started, 1),
                "muted": self.muted, "silent": self.silent_mode,
                "state": self._state, "api_key_ready": self._api_key_ready}
        try:
            data.update(self.controller.diagnostics())
        except Exception as exc:
            data["error"] = str(exc)
        return data


def make_ui(face_path: str = "face.png", size: Any = None):
    """Return the sole UI implementation: the ambient Siri overlay in ui_siri.

    Siri is the only UI of the application; there is no Tk fallback, no legacy
    theme switch, and no parallel UI engine anymore. On a QML load failure the
    error is surfaced (traceback + SystemExit) instead of silently falling
    back to a removed implementation.
    """
    try:
        ui = FridayUI(face_path, size)
    except Exception:
        # Construction failure (QML missing, Qt platform broken, ...):
        # surface the traceback and exit hard — no silent fallback to a
        # removed implementation.
        traceback.print_exc()
        _log_exception(RuntimeError("FRIDAY's Siri UI failed to construct"))
        raise SystemExit(
            "FRIDAY's Siri UI failed to construct.\n\n"
            "Install the UI dependencies with:\n"
            f"  {sys.executable} -m pip install -r requirements.txt\n"
        )
    load_errors = []
    try:
        load_errors = ui.controller.qml_errors()
    except Exception:
        pass
    if load_errors:
        detail = "\n".join(f"  [{layer}] {url}:{line}:{col}: {msg}"
                           for layer, url, line, col, msg in load_errors)
        print(f"[UI] ⚠️ QML failed to load:\n{detail}")
        traceback.print_exc()
        _log_exception(RuntimeError(detail))
        raise SystemExit(
            "FRIDAY's Siri UI failed to load its QML interface.\n"
            f"  {detail}\n\n"
            "Install the UI dependencies with:\n"
            f"  {sys.executable} -m pip install -r requirements.txt\n"
        )
    return ui


__all__ = [
    "FridayUI",
    "make_ui",
    "get_base_dir",
    "BASE_DIR",
    "CONFIG_DIR",
    "API_FILE",
    "THEME_FILE",
    "THEMES",
    "THEME_NAMES",
    "SYSTEM_NAME",
    "MODEL_BADGE",
]
