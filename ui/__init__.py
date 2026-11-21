"""FRIDAY — public UI facade.

This module is the **only** entry point ``main.py`` and ``actions/`` may
import. It preserves, behaviour for behaviour, every attribute and method the
legacy implementations exposed (see ``docs/UI_AUDIT_CONTRACT.md`` §3), while
delegating the actual pixels to the Electron + React + WebGL2 Siri interface in
``ui/app`` via :mod:`ui.controller`.

There is one UI: the ambient Apple-Siri orb. No Tk, no Qt, no QML, no fallback.

Threading model
---------------
``main.py`` keeps its shape: ``mainloop()``/``run()`` on the main OS thread, the
asyncio voice loop on a worker thread. Every public method below is safe to call
from **any** thread: it routes through :class:`ui.bridge.UiDispatcher`, which
runs inline on the UI-loop thread and queues (never blocks) everywhere else.

Import purity
-------------
``import ui`` must be cheap and dependency-free: only stdlib modules and
:mod:`ui.themes` load at import time. The WebSocket server, the Electron
process and Pillow are only touched when :class:`FridayUI` is constructed.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from .themes import APP_DIR, DEFAULT_THEME, THEMES, THEME_NAMES  # noqa: F401

# ─────────────────────────────────────────────────────────────────────────────
# Module-level configuration — USED BY tests/test_p0_security.py
# ─────────────────────────────────────────────────────────────────────────────


def get_base_dir() -> Path:
    """Repository (or bundle) root.

    ``ui`` is a package now: ``__file__`` is ``<root>/ui/__init__.py``, so the
    root is **two** levels up. Getting this wrong points ``CONFIG_DIR`` at
    ``ui/config`` and every API-key/theme read silently fails.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE = CONFIG_DIR / "api_keys.json"
THEME_FILE = CONFIG_DIR / "ui_theme.json"

SYSTEM_NAME = "FRIDAY"
MODEL_BADGE = "FRIDAY"


def _load_theme_name() -> str:
    """Read the persisted theme (same file the UI writes)."""
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


def _headless_requested() -> bool:
    """``FRIDAY_UI_HEADLESS=1`` runs the bridge with no native window.

    Used by the test-suite and by headless verification runs; normal launches
    never set it.
    """
    return os.environ.get("FRIDAY_UI_HEADLESS", "").strip().lower() in (
        "1", "true", "yes", "on")


# ─────────────────────────────────────────────────────────────────────────────
# Adapter that makes the UI look like a Tk root
# ─────────────────────────────────────────────────────────────────────────────

class _RootAdapter:
    """Duck-typed ``root`` so ``main.py``'s legacy calls keep working.

    Legacy usage (audit contract §3.5)::

        root.after(0, cb)        # from the pystray / keyboard threads
        root.state()             # "normal" | "withdrawn"
        root.withdraw() / deiconify() / lift()
        root.destroy()
        root.mainloop()
        root.report_callback_exception = fn

    ``after`` is the important one: it is called from **non-GUI threads**. Here
    it posts onto the UI dispatcher, so it is both thread-safe and
    non-blocking.
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
# Ambient Siri implementation
# ─────────────────────────────────────────────────────────────────────────────

class FridayUI:
    """Ambient, zero-chrome FRIDAY overlay.

    Constructs the UI transport, the state machine and the Electron overlay, but
    does **not** enter the event loop — ``main.py`` calls :meth:`run` (via
    ``root.mainloop``) on the main OS thread, exactly as before.
    """

    #: True so callers can feature-detect without importing the controller.
    ambient = True

    def __init__(self, face_path: str = "face.png", size: Any = None) -> None:
        from .audio_ux import AudioUX
        from .controller import UiController, UiStartupError  # noqa: F401

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
        self.on_silent_change = None         # Callable[[bool], None] | None
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

        # ── transport + overlay ──────────────────────────────────────────────
        theme = _load_theme_name()
        headless = _headless_requested()
        self.controller = UiController(
            theme_name=theme,
            audio=AudioUX(enabled=True, ducking=False),
            enable_overlay=not headless,
            start_hidden=True,
        )
        self.bridge = self.controller.bridge
        self.state_machine = self.controller.state

        # Wire renderer intents -> the legacy backend callback.
        self.controller.on_text_command = self._forward_text_command
        self.controller.on_api_key_submitted = self._on_renderer_api_key

        # Mirror the state machine onto the legacy attributes.
        self.state_machine.add_listener(self._on_transition)

        self.root = _RootAdapter(self)

        # Setup prompt when no key is configured (parity with the old flow).
        if not self._api_key_ready:
            self.controller.set_setup_visible(True)

        self.set_state("LISTENING")

    # ── identity / introspection ─────────────────────────────────────────────
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FridayUI ambient state={self._state}>"

    @property
    def visible(self) -> bool:
        return bool(self.controller.visible)

    def screen_size(self) -> tuple[int, int]:
        try:
            from . import win32_native as w32

            monitors = w32.enumerate_monitors()
            primary = next((m for m in monitors if m.primary), None) or monitors[0]
            return (int(primary.width), int(primary.height))
        except Exception:
            return (1920, 1080)

    def window_size(self) -> tuple[int, int]:
        """The stage window's design footprint (bottom-centre, above taskbar)."""
        width, height = self.screen_size()
        return (min(width, 560), min(680, max(360, height - 24)))

    # ── dispatch plumbing ────────────────────────────────────────────────────
    def _dispatch(self, fn: Callable, *args: Any, **kwargs: Any) -> None:
        """Run ``fn`` on the UI thread. Safe from any thread."""
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

    # ── renderer -> backend ─────────────────────────────────────────────────
    def _forward_text_command(self, text: str) -> None:
        """Called on a worker thread by the controller; never blocks the UI."""
        callback = self.on_text_command
        if callback is not None:
            callback(text)

    def set_live_transcript(self, text: str) -> None:
        """Show the current partial user/assistant utterance above the orb."""
        self._dispatch(self.bridge.set_live_transcript, str(text or ""))

    def _on_renderer_api_key(self, key: str) -> None:
        if not self.submit_api_key(key):
            self.write_log("ERR: That API key could not be saved.")

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
        if not self.muted and not self.silent_mode:
            self._dispatch(self.state_machine.stop_speaking)
        else:
            # Muted / text-only: the plain branch used to do *nothing*, so the
            # machine stayed on SPEAKING and the controller never restored the
            # audio it had ducked. release_speech() always emits a transition.
            self._dispatch(self.state_machine.release_speech, "stop_speaking")

    # ── public API — transcript ──────────────────────────────────────────────
    def write_log(self, text: str) -> None:
        """The single most-used legacy sink (30 call sites in actions/).

        Before the event loop starts (``wait_for_api_key`` runs on the worker
        thread while the main thread has not reached ``mainloop()`` yet), logs
        are coalesced instead of queued: 30 modules start talking at once at
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

        There is no canvas to redraw any more; the orb is bound to the state
        message. Push the current flag so a *direct* attribute write from
        Telegram still lands on screen.
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
            self.bridge.openInspector("camera")

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
            self.bridge.openInspector("arcade")

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
        self.controller.show()

    def hide(self) -> None:
        self.controller.hide()

    def invoke(self) -> None:
        """Explicit Siri invocation: the orb pops into view (spring bloom)."""
        self.controller.invoke()

    def dismiss(self) -> None:
        """Graceful Siri dismissal: orb and cards dissolve to invisibility."""
        self.controller.dismiss()

    def raise_window(self) -> None:
        self.controller.raise_window()

    def quit(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self.controller.request_quit()
        except Exception:
            try:
                self.controller.shutdown()
            except Exception:
                os._exit(0)

    # ── event loop ───────────────────────────────────────────────────────────
    def run(self) -> int:
        """Run until shutdown. MUST be called on the main OS thread."""
        self._loop_entered = True
        # Replay what was logged during startup, once, from the UI thread.
        self._dispatch(self._flush_pending_logs)
        self.controller.start()
        # Ctrl+C: turn it into an orderly teardown. Installed here (not in the
        # controller's __init__, which the tests also use) so only a real
        # interactive run owns the process-wide handler.
        if not self.controller.install_interrupt_handler():
            print("[UI] ⚠️ Could not install the Ctrl+C handler "
                  "(not on the main thread?) — use the tray Quit item.")

        code = 0
        try:
            self.controller.wait()
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

    # ── API key bootstrap (parity with the legacy setup dialog) ──────────────
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
        """Block until a key exists. Same contract as the legacy version.

        We keep the blocking semantics (the voice worker must not start without
        a key) but stop cleanly on shutdown, and re-check the file at 4 Hz so a
        key added by the setup card is picked up immediately.
        """
        while not self._api_key_ready:
            if self._closed.is_set():
                return
            time.sleep(0.25)
            if self._api_keys_exist():
                self._api_key_ready = True
                self.controller.set_setup_visible(False)
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
        self.controller.set_setup_visible(False)
        self.set_state("LISTENING")
        self.write_log("SYS: Systems initialised. Friday online.")
        return True

    # ── diagnostics ──────────────────────────────────────────────────────────
    def diagnostics(self) -> dict:
        data = {"legacy": False, "backend": "electron",
                "uptime_s": round(time.time() - self._started, 1),
                "muted": self.muted, "silent": self.silent_mode,
                "state": self._state, "api_key_ready": self._api_key_ready}
        try:
            data.update(self.controller.diagnostics())
        except Exception as exc:
            data["error"] = str(exc)
        return data


def make_ui(face_path: str = "face.png", size: Any = None):
    """Return the sole UI implementation: the ambient Siri overlay.

    There is no Tk fallback, no legacy theme switch, and no parallel UI engine.
    A construction failure (no Electron binary, missing renderer build, broken
    transport) surfaces the traceback and exits hard with the exact commands
    needed to fix it.
    """
    try:
        instance = FridayUI(face_path, size)
    except Exception as exc:
        traceback.print_exc()
        _log_exception(RuntimeError("FRIDAY's Siri UI failed to construct"))
        raise SystemExit(
            "FRIDAY's Siri UI failed to construct.\n"
            f"  {type(exc).__name__}: {exc}\n\n"
            "Install and build the frontend with:\n"
            f"  cd {APP_DIR}\n"
            "  npm install\n"
            "  npm run build\n\n"
            "Then run FRIDAY again:\n"
            "  python main.py\n"
        ) from exc
    return instance


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
    "DEFAULT_THEME",
    "SYSTEM_NAME",
    "MODEL_BADGE",
    "APP_DIR",
]
