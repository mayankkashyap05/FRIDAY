"""FRIDAY UI — local WebSocket server, Electron supervisor, UI lifecycle.

The controller is the only place that talks to the operating system on behalf
of the UI. It owns:

* the **UI-loop thread** (:class:`ui.bridge.UiDispatcher`) that serialises every
  mutation of the bridge stores,
* a **loopback WebSocket server** (``websockets``, bound to ``127.0.0.1:0`` with
  a per-launch 256-bit capability token) that carries the JSON state protocol
  and the binary JPEG preview stream to the Electron renderer,
* the **Electron process** itself, spawned straight from
  ``node_modules/electron/dist/electron.exe`` (never through ``cmd.exe`` or an
  ``electron.cmd`` shim) so ``terminate()`` really ends the UI and leaves no
  orphaned processes,
* the **4 Hz housekeeping** loop (state-machine timers, clock, card TTL,
  activity pruning, Do-Not-Disturb polling) and the smart reading-grace
  dismissal that makes Siri dissolve after the user has finished reading.

Failure policy: there is no fallback UI. If Electron cannot be launched the
controller raises :class:`UiStartupError` with actionable instructions, and
``make_ui()`` turns that into a hard ``SystemExit`` — a broken install must be
loud, never a window that silently shows nothing.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import protocol, win32_native as w32
from .audio_ux import AudioUX
from .bridge import UiBridge, UiDispatcher
from .event_bus import CardKind, EventBus, UiState
from .state_machine import AssistantStateMachine
from .themes import (  # noqa: F401  (re-exported for convenience)
    APP_DIR,
    DEFAULT_THEME,
    DIST_DIR,
    THEMES,
    THEME_NAMES,
    build_theme,
)


class UiStartupError(RuntimeError):
    """The UI cannot be brought up (missing build, missing Electron, ...)."""


# ─────────────────────────────────────────────────────────────────────────────
# Electron discovery
# ─────────────────────────────────────────────────────────────────────────────

_ELECTRON_BINARIES = (
    "electron.exe",              # Windows (node_modules/electron/dist)
    "electron",                  # Linux
    "Electron",                  # macOS binary name inside .app
    "electron.app/Contents/MacOS/Electron",
)


def electron_package_dir(app_dir: Path | None = None) -> Path:
    """``ui/app/node_modules/electron`` — where npm puts the Electron package."""
    return Path(app_dir or APP_DIR) / "node_modules" / "electron"


def _platform_binary_name() -> str:
    if os.name == "nt":
        return "electron.exe"
    if sys.platform == "darwin":
        return "Electron.app/Contents/MacOS/Electron"
    return "electron"


def electron_executable(app_dir: Path | None = None) -> Path | None:
    """Absolute path to the real Electron binary, or ``None``.

    ``npm install`` succeeds even when Electron's postinstall download fails, so
    "the package exists" is *not* the same as "the binary exists" — that gap is
    exactly what produces a silent half-install. We therefore look for the
    binary itself, starting with the name the package records in ``path.txt``.

    ``node_modules/.bin/electron.cmd`` is deliberately **not** considered: on
    Windows that is a ``cmd.exe`` batch shim, and terminating the shim leaves the
    actual Electron process running (plus a console window flash). We always
    launch the executable directly.
    """
    pkg = electron_package_dir(app_dir)
    dist = pkg / "dist"
    candidates: list[Path] = []
    try:
        hint = (pkg / "path.txt").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        hint = ""
    if hint:
        candidates.append(dist / Path(hint.replace("\\", "/")))
    candidates.extend(dist / name for name in _ELECTRON_BINARIES)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def renderer_files(app_dir: Path | None = None) -> tuple[Path, Path]:
    """The two artefacts ``npm run build`` must leave behind."""
    root = Path(app_dir or APP_DIR)
    return root / "dist" / "index.html", root / "dist" / "electron" / "main.js"


def renderer_built(app_dir: Path | None = None) -> bool:
    """Whether ``npm run build`` has produced a loadable renderer."""
    return all(path.is_file() for path in renderer_files(app_dir))


def ui_install_status(app_dir: Path | None = None) -> dict[str, Any]:
    """Everything needed to explain *which* piece of the UI install is missing.

    Deliberately path-free of side effects: it only stats files, so it is safe
    to call from an error path and from diagnostics.
    """
    root = Path(app_dir or APP_DIR)
    index, main_js = renderer_files(root)
    pkg = electron_package_dir(root)
    binary = electron_executable(root)
    return {
        "app": str(root),
        "renderer": index.is_file() and main_js.is_file(),
        "rendererIndex": index.is_file(),
        "rendererMain": main_js.is_file(),
        "package": pkg.is_dir(),
        "binary": str(binary) if binary is not None else "",
        "binaryExpected": str(pkg / "dist" / _platform_binary_name()),
    }


def missing_ui_help(app_dir: Path | None = None) -> str:
    """The exact commands a user needs to finish (or repair) the UI install.

    A missing Electron *binary* and a never-installed frontend need different
    fixes, so the message names the failing piece instead of repeating the same
    three commands until something works.
    """
    status = ui_install_status(app_dir)
    app = status["app"]
    binary_missing = status["package"] and not status["binary"]
    renderer_missing = not status["renderer"]

    mark = lambda ok: "✓" if ok else "✗"  # noqa: E731 - tiny local formatter
    lines = [
        "FRIDAY's Electron UI is not ready.",
        "",
        f"  app directory    {app}",
        f"  renderer         {mark(status['renderer'])} "
        + ("built" if status["renderer"] else "missing")
        + "  (dist/index.html, dist/electron/main.js)",
        f"  electron package {mark(status['package'])} "
        + ("installed" if status["package"] else "missing")
        + "  (node_modules/electron)",
        f"  electron binary  {mark(bool(status['binary']))} "
        + (status["binary"] or f"missing  ({status['binaryExpected']})"),
        "",
    ]

    if binary_missing:
        lines += [
            "The `electron` package is installed but its binary was never downloaded.",
            "npm reports success even when that postinstall step fails, so the",
            "install looks complete while node_modules/electron/dist stays empty.",
            "",
            "Repair it — this re-runs the binary download:",
            f"  cd {app}",
            "  npm rebuild electron",
            "",
            "If npm still cannot reach the download server, run the installer",
            "directly so it prints the real error:",
            f"  node {Path(app) / 'node_modules' / 'electron' / 'install.js'}",
            "",
        ]
    elif renderer_missing:
        lines += [
            "The renderer has not been built yet (or the build was interrupted).",
            "",
        ]
    else:
        lines += [
            "Every piece is present but the controller still could not start it;",
            "check the message above for the underlying error.",
            "",
        ]

    lines += [
        ("If a clean rebuild still does not help:" if binary_missing
         else "Build it once with:"),
        f"  cd {app}",
        "  npm install",
        "  npm run build",
        "",
        "Start FRIDAY again:",
        "  python main.py",
        "",
        "Or verify every piece at once, from the repository root:",
        "  python scripts\\verify_windows_ui.py",
        "",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket server
# ─────────────────────────────────────────────────────────────────────────────

class _Client:
    """Per-connection outbound state (owned by the server's event loop)."""

    __slots__ = ("ws", "json_q", "frames", "dirty", "addr")

    MAX_JSON_BACKLOG = 512

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.json_q: asyncio.Queue[str] = asyncio.Queue()
        #: stream id -> latest un-sent JPEG (latest-frame-wins, zero backlog).
        self.frames: dict[int, bytes] = {}
        self.dirty = asyncio.Event()
        self.addr = getattr(ws, "remote_address", None)

    def put_json(self, text: str) -> None:
        # A stalled renderer must not grow memory without bound: the newest
        # state is always more useful than the oldest, so drop from the front.
        while self.json_q.qsize() >= self.MAX_JSON_BACKLOG:
            try:
                self.json_q.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - race-free in-loop
                break
        self.json_q.put_nowait(text)
        self.dirty.set()

    def put_frame(self, stream: int, jpeg: bytes) -> None:
        self.frames[stream] = jpeg
        self.dirty.set()

    def has_work(self) -> bool:
        return not self.json_q.empty() or bool(self.frames)


def _token_from_request(request: Any) -> str | None:
    """Extract the capability token from query string or headers.

    Accepted forms (the renderer uses the query string; the same server is
    reachable from test clients and from a browser dev session):

    * ``ws://127.0.0.1:PORT/?token=<token>``
    * ``Authorization: Bearer <token>`` / ``X-Friday-Token: <token>``
    * ``Sec-WebSocket-Protocol: friday.v1.<token>``
    """
    if request is None:
        return None
    try:
        query = parse_qs(urlparse(str(getattr(request, "path", "") or "")).query)
        values = query.get("token") or []
        if values and values[0]:
            return str(values[0])
    except Exception:
        pass
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    try:
        auth = str(headers.get("Authorization") or "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip() or None
        direct = str(headers.get("X-Friday-Token") or "").strip()
        if direct:
            return direct
        proto = str(headers.get("Sec-WebSocket-Protocol") or "")
        for part in proto.split(","):
            part = part.strip()
            if part.startswith(protocol.SUBPROTOCOL_PREFIX):
                return part[len(protocol.SUBPROTOCOL_PREFIX):] or None
    except Exception:
        pass
    return None


class WsServer:
    """Loopback-only WebSocket server with token auth and binary frames.

    Runs an asyncio event loop on a dedicated daemon thread. All public methods
    are thread-safe; the loop itself is only ever touched through
    ``call_soon_threadsafe``.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        on_message: Callable[[dict], None] | None = None,
        on_connect: Callable[[], dict | None] | None = None,
        on_client_count: Callable[[int], None] | None = None,
    ) -> None:
        self.token = token or protocol.new_token()
        self.host = host
        self.requested_port = int(port)
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_client_count = on_client_count

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self._clients: set[_Client] = set()
        self._ready = threading.Event()
        self._stop_event: asyncio.Event | None = None
        self._error: str | None = None
        self._port = 0
        self._connections = 0
        self._rejected = 0
        self._messages = 0

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self, timeout: float = 10.0) -> str:
        """Bind and serve; returns the ``ws://127.0.0.1:<port>`` URL."""
        if self._thread is not None and self._thread.is_alive():
            return self.url
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="FridayWsServer",
                                        daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise UiStartupError(
                f"FRIDAY's local UI socket did not start within {timeout:.0f}s")
        if self._error:
            raise UiStartupError(self._error)
        return self.url

    def stop(self, timeout: float = 3.0) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._begin_stop)
            except RuntimeError:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None
        self._server = None

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self._port}"

    @property
    def port(self) -> int:
        return self._port

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # ── thread-safe producers ────────────────────────────────────────────────
    def broadcast(self, text: str) -> None:
        """Queue one JSON message for every connected renderer."""
        self._schedule(self._fanout_json, text)

    def broadcast_binary(self, stream: int, jpeg: bytes) -> None:
        """Queue one JPEG frame (latest-frame-wins per client)."""
        if not jpeg:
            return
        self._schedule(self._fanout_frame, int(stream), jpeg)

    def _schedule(self, fn: Callable[..., None], *args: Any) -> None:
        loop = self._loop
        if loop is None or loop.is_closed() or not loop.is_running():
            return
        try:
            loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:  # pragma: no cover - loop closed mid-shutdown
            pass

    # ── event-loop side ──────────────────────────────────────────────────────
    def _run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            loop.run_until_complete(self._serve())
        except Exception as exc:  # pragma: no cover - startup failure path
            self._error = f"FRIDAY's local UI socket failed: {exc}"
            self._ready.set()
        finally:
            try:
                loop = self._loop
                if loop is not None and not loop.is_closed():
                    loop.close()
            except Exception:
                pass
            self._loop = None
            self._ready.set()

    async def _serve(self) -> None:
        try:
            import websockets
            from websockets.asyncio.server import serve
        except Exception as exc:  # pragma: no cover - dependency missing
            self._error = (
                "FRIDAY's UI needs the 'websockets' package.\n"
                f"  {sys.executable} -m pip install 'websockets>=13,<17'\n"
                f"({exc})")
            self._ready.set()
            return

        self._stop_event = asyncio.Event()
        try:
            self._server = await serve(
                self._handler, self.host, self.requested_port,
                process_request=self._process_request,
                max_size=1 << 20, ping_interval=20, ping_timeout=20,
                close_timeout=1,
            )
        except TypeError:
            # Older websockets without process_request: authenticate in the
            # handler instead (the handler still closes with 4001).
            self._server = await serve(self._handler, self.host,
                                       self.requested_port, max_size=1 << 20)
        sockets = getattr(self._server, "sockets", None) or []
        if sockets:
            self._port = int(sockets[0].getsockname()[1])
        self._ready.set()
        try:
            await self._stop_event.wait()
        finally:
            # Flush what is already queued (the ``shutdown`` message above all)
            # *before* closing, otherwise a renderer never learns why the
            # socket went away and lingers for its full grace period.
            await self._shutdown_async()
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass

    async def _shutdown_async(self) -> None:
        clients = list(self._clients)
        self._clients.clear()
        for client in clients:
            try:
                await self._flush(client)
            except Exception:
                pass
            try:
                await client.ws.close(protocol.CLOSE_GOING_AWAY, "shutdown")
            except Exception:
                pass
        self._notify_clients()

    def _begin_stop(self) -> None:
        event = self._stop_event
        if event is not None and not event.is_set():
            event.set()

    def _process_request(self, connection: Any, request: Any) -> Any:
        """Reject a bad capability token before the handshake completes."""
        token = _token_from_request(request)
        if token == self.token:
            return None
        self._rejected += 1
        try:
            from http import HTTPStatus

            return connection.respond(HTTPStatus.UNAUTHORIZED, "unauthorized\n")
        except Exception:  # pragma: no cover - very old websockets
            return None

    async def _handler(self, ws: Any) -> None:
        # Defence in depth: even if the request slipped past process_request,
        # verify again and close with the documented 4001 code.
        request = getattr(ws, "request", None)
        if _token_from_request(request) != self.token:
            self._rejected += 1
            try:
                await ws.close(protocol.CLOSE_UNAUTHORIZED, "unauthorized")
            except Exception:
                pass
            return

        client = _Client(ws)
        self._clients.add(client)
        self._connections += 1
        self._notify_clients()

        snapshot = self._snapshot()
        if snapshot is not None:
            client.put_json(protocol.dumps(snapshot))

        writer = asyncio.create_task(self._writer(client))
        try:
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    continue
                message = protocol.loads(raw)
                if not message:
                    continue
                self._messages += 1
                callback = self._on_message
                if callback is not None:
                    try:
                        callback(message)
                    except Exception as exc:  # pragma: no cover - defensive
                        print(f"[WsServer] ⚠️ message handler failed: {exc}")
        except Exception:
            pass
        finally:
            writer.cancel()
            self._clients.discard(client)
            self._notify_clients()

    async def _writer(self, client: _Client) -> None:
        try:
            while True:
                await self._flush(client)
                client.dirty.clear()
                if client.has_work():
                    continue
                await client.dirty.wait()
        except asyncio.CancelledError:  # pragma: no cover - normal teardown
            raise
        except Exception:
            # A dead socket simply ends its writer; the reader notices on the
            # next receive and cleans up.
            return

    async def _flush(self, client: _Client) -> None:
        while not client.json_q.empty():
            text = client.json_q.get_nowait()
            await client.ws.send(text)
        for stream in list(client.frames):
            jpeg = client.frames.pop(stream)
            await client.ws.send(protocol.frame_message(stream, jpeg))

    def _fanout_json(self, text: str) -> None:
        for client in list(self._clients):
            client.put_json(text)

    def _fanout_frame(self, stream: int, jpeg: bytes) -> None:
        for client in list(self._clients):
            client.put_frame(stream, jpeg)

    def _snapshot(self) -> dict | None:
        callback = self._on_connect
        if callback is None:
            return None
        try:
            return callback()
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[WsServer] ⚠️ snapshot failed: {exc}")
            return None

    def _notify_clients(self) -> None:
        callback = self._on_client_count
        if callback is None:
            return
        try:
            callback(len(self._clients))
        except Exception:  # pragma: no cover - defensive
            pass

    # ── introspection ────────────────────────────────────────────────────────
    def stats(self) -> dict:
        return {
            "url": self.url if self._port else "",
            "port": self._port,
            "clients": len(self._clients),
            "connections": self._connections,
            "rejected": self._rejected,
            "messages": self._messages,
            "running": self.running,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Binary frame pump
# ─────────────────────────────────────────────────────────────────────────────

class FramePump:
    """Encode the newest camera/arcade frame off the producer thread.

    Vision and arcade produce ~30 fps. Encoding inline in the producing thread
    would add JPEG latency to the vision loop; encoding on the UI thread would
    stutter the overlay. One dedicated thread with a single slot per stream
    keeps the newest frame and drops everything it missed (latest-frame-wins).
    """

    def __init__(self, sender: Callable[[int, bytes], None]) -> None:
        self._sender = sender
        self._lock = threading.Lock()
        self._slots: dict[int, Any] = {}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._encoded = 0
        self._dropped = 0
        self._failed = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="FridayFrames",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.5) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None

    def push(self, stream: str, frame: Any) -> bool:
        """Publish the newest frame for ``stream``. Safe from any thread."""
        stream_id = (protocol.STREAM_BY_NAME.get(str(stream).lower())
                     if isinstance(stream, str) else stream)
        if not stream_id:
            return False
        with self._lock:
            if stream_id in self._slots:
                self._dropped += 1
            self._slots[stream_id] = frame
        self._wake.set()
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.25)
            self._wake.clear()
            while not self._stop.is_set():
                with self._lock:
                    if not self._slots:
                        break
                    stream_id, frame = self._slots.popitem()
                jpeg = protocol.encode_jpeg(frame)
                if not jpeg:
                    self._failed += 1
                    continue
                self._encoded += 1
                try:
                    self._sender(stream_id, jpeg)
                except Exception:  # pragma: no cover - defensive
                    pass

    def stats(self) -> dict:
        return {"encoded": self._encoded, "dropped": self._dropped,
                "failed": self._failed, "queued": len(self._slots)}


# ─────────────────────────────────────────────────────────────────────────────
# Controller
# ─────────────────────────────────────────────────────────────────────────────

class UiController:
    """Owns the UI for the lifetime of the process."""

    TICK_MS = 250
    SLOW_TICK_MS = 4000
    #: How long Electron gets to exit after the shutdown message.
    ELECTRON_GRACE_S = 1.5

    def __init__(
        self,
        *,
        theme_name: str = "siri",
        audio: AudioUX | None = None,
        enable_overlay: bool = True,
        enable_capture_exclusion: bool = True,
        start_hidden: bool = True,
        token: str | None = None,
        app_dir: Path | None = None,
    ) -> None:
        # Interrupt state must exist before any handler can run.
        self._interrupted = False
        self._interrupt_installed = False
        self._shutdown_done = False
        self._closed = threading.Event()

        self.app_dir = Path(app_dir or APP_DIR)
        self.bus = EventBus()
        self.state = AssistantStateMachine()
        self.dispatcher = UiDispatcher()
        self.dispatcher.bind_ui_thread()

        # ── system probes (all non-fatal) ────────────────────────────────────
        self.caps = w32.probe(warn=False)
        self.reduced_motion = not w32.animations_enabled()
        self.transparency = w32.transparency_enabled()
        self.system_dark = w32.system_dark_mode()
        self._dnd = False
        self._monitors = w32.enumerate_monitors()

        self._theme_name = theme_name if theme_name in THEMES else DEFAULT_THEME
        self.theme = build_theme(self._theme_name, light=not self.system_dark,
                                 reduced_motion=self.reduced_motion,
                                 transparency=self.transparency)

        # ── bridge + audio ───────────────────────────────────────────────────
        self.bridge = UiBridge(self.state, self.bus, self.dispatcher, self.theme)
        self.bridge.on_emit = self._on_bridge_emit
        self.bridge.on_input = self._on_bridge_input
        self.bridge.on_api_key = self._on_bridge_api_key
        self.bridge.on_quit = self.request_quit
        # The loop thread drains the EventBus; producers only publish.
        self.dispatcher.hook = self._pump_bus

        self.audio = audio or AudioUX(enabled=True, ducking=False)
        try:
            self.audio.set_dnd_probe(lambda: self._dnd)
        except Exception:
            pass

        self._hidden = bool(start_hidden)
        self._typing = False
        self._pointer_inside = False
        self._setup_visible = False
        self._wake_active = False
        self._wake_status = "Engine idle"
        self._dismiss_deadline: float | None = None
        self._dismissal_grace_ms = 5000
        self._started = False
        self._electron: subprocess.Popen | None = None
        self._electron_exit = threading.Event()
        self._electron_error: str | None = None
        self._capture_excluded = bool(enable_capture_exclusion)
        #: Last authoritative snapshot, rebuilt on the UI thread after every
        #: delta. Renderers get it on connect without touching live stores from
        #: the socket thread.
        self._snapshot_cache: dict = {}

        # Callbacks the facade installs (legacy backend coupling).
        self.on_text_command: Callable[[str], None] | None = None
        self.on_api_key_submitted: Callable[[str], Any] | None = None

        # ── WebSocket server ─────────────────────────────────────────────────
        self.token = token or protocol.new_token()
        self.ws = WsServer(
            self.token,
            on_message=self._on_client_message,
            on_connect=self._snapshot,
            on_client_count=self._on_client_count,
        )
        self.frames = FramePump(self._on_encoded_frame)

        self.state.add_listener(self._on_state)

        self.bridge.apply_theme(self.theme)
        self._snapshot_cache = self.bridge.snapshot()

        # ── bring the transport up; Electron attaches to it ──────────────────
        self.ws_url = self.ws.start()
        self.frames.start()
        self._enable_overlay = bool(enable_overlay)
        if self._enable_overlay:
            self._spawn_electron()
        else:
            # Headless: the full bridge + state machine is still exercised;
            # only the native window is skipped (tests, CI, verification).
            self._hidden = True

        if not self._hidden and self._enable_overlay:
            self.bridge.set_invoked(True)

    # ── identity ─────────────────────────────────────────────────────────────
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<UiController state={self.state.state.value} ws={self.ws_url}>"

    @property
    def visible(self) -> bool:
        return not self._hidden

    @property
    def headless(self) -> bool:
        return self._electron is None

    @property
    def electron_running(self) -> bool:
        proc = self._electron
        return proc is not None and proc.poll() is None

    # ── Electron lifecycle ───────────────────────────────────────────────────
    def _spawn_electron(self) -> None:
        exe = electron_executable(self.app_dir)
        if exe is None or not renderer_built(self.app_dir):
            raise UiStartupError(missing_ui_help(self.app_dir))

        env = os.environ.copy()
        env[protocol.URL_ENV] = self.ws_url
        env[protocol.TOKEN_ENV] = self.token
        env["FRIDAY_WS_URL"] = self.ws_url
        env.setdefault("ELECTRON_DISABLE_SECURITY_WARNINGS", "1")
        if not env.get("FRIDAY_UI_DEBUG"):
            env.setdefault("ELECTRON_ENABLE_LOGGING", "0")

        creationflags = 0
        if os.name == "nt":
            # No console flash for the overlay process.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        out = None if os.environ.get("FRIDAY_UI_DEBUG") else subprocess.DEVNULL
        try:
            self._electron = subprocess.Popen(
                # Windows rule: the real executable, never cmd.exe/electron.cmd.
                [str(exe), str(self.app_dir.resolve())],
                cwd=str(self.app_dir.resolve()),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=out,
                creationflags=creationflags,
            )
        except Exception as exc:
            raise UiStartupError(
                "FRIDAY could not start its Electron UI "
                f"({type(exc).__name__}: {exc}).\n\n{missing_ui_help(self.app_dir)}"
            ) from exc

        threading.Thread(target=self._watch_electron, name="FridayElectronWatch",
                         daemon=True).start()

    def _watch_electron(self) -> None:
        proc = self._electron
        if proc is None:
            return
        code = proc.wait()
        self._electron_exit.set()
        if not self._shutdown_done and not self._closed.is_set():
            # The renderer was closed by the user (or crashed): Friday has no
            # UI left, so the process must exit rather than linger invisibly.
            print(f"[FRIDAY] 👋 UI window closed (exit {code}) — shutting down.")
            self.request_quit()

    def _terminate_electron(self) -> None:
        # Tell every renderer to close *before* the window is torn down, so an
        # abrupt Python exit never leaves a zombie overlay behind. Sent even in
        # headless mode: the socket is the contract, not the process.
        try:
            self.ws.broadcast(protocol.dumps({"t": protocol.T_SHUTDOWN}))
        except Exception:
            pass
        proc = self._electron
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
            self._electron_exit.wait(self.ELECTRON_GRACE_S)
            if proc.poll() is None:
                proc.kill()
                proc.wait(2.0)
        except Exception:
            pass
        finally:
            self._electron = None

    # ── outbound: bridge deltas -> renderers ─────────────────────────────────
    def _on_bridge_emit(self, kind: str, payload: dict) -> None:
        message = dict(payload)
        message["t"] = kind
        self.ws.broadcast(protocol.dumps(message))
        if kind != protocol.T_AMPLITUDE:
            # Rebuild the reconnect snapshot off the live stores here, on the
            # thread that owns them; the socket thread only ever serves it.
            self._snapshot_cache = self.bridge.snapshot()

    def _on_encoded_frame(self, stream: int, jpeg: bytes) -> None:
        if not self._frames_wanted(stream):
            return
        if self.ws.client_count == 0:
            return
        self.ws.broadcast_binary(stream, jpeg)

    def _frames_wanted(self, stream: int) -> bool:
        if stream == protocol.STREAM_VISION:
            return self.bridge.inspectorOpen or self.bridge.visionPreviewOpen
        return self.bridge.arcadeOpen

    def _snapshot(self) -> dict:
        """Called by the server on every (re)connect (socket thread)."""
        return self._snapshot_cache or {"t": protocol.T_SNAPSHOT}

    def _on_client_count(self, count: int) -> None:
        # A fresh renderer (re)connected: re-assert the authoritative state so
        # a reload can never show a stale orb.
        if count > 0:
            self.dispatcher.call(self._resync_renderer)

    def _resync_renderer(self) -> None:
        self._snapshot_cache = self.bridge.snapshot()
        message = {"t": protocol.T_STATE, **self.bridge.state_payload()}
        self.ws.broadcast(protocol.dumps(message))

    def _pump_bus(self) -> None:
        """Drain the EventBus on the UI-loop thread (set as dispatcher hook)."""
        if self.bus.pending():
            try:
                self.bus.drain(max_items=512)
            except Exception as exc:  # pragma: no cover
                print(f"[Controller] ⚠️ bus drain: {exc}")

    # ── inbound: renderer messages ───────────────────────────────────────────
    def _on_client_message(self, message: dict) -> None:
        kind = str(message.get("t") or "")
        if kind not in protocol.CLIENT_TYPES:
            return
        if kind == protocol.C_PING:
            self.ws.broadcast(protocol.dumps({"t": protocol.T_PONG,
                                              "ts": time.time()}))
            return
        if kind == protocol.C_REQUEST_QUIT:
            self.request_quit()
            return
        if kind == protocol.C_WINDOW_DISMISSED:
            self.dispatcher.call(self._on_window_dismissed)
            return
        if kind == protocol.C_INTERACTION:
            self.dispatcher.call(self._on_interaction)
            return
        if kind == protocol.C_TYPING_CHANGED:
            typing = bool(message.get("value"))
            self.dispatcher.call(self._set_typing, typing)
            return
        if kind == protocol.C_POINTER_INSIDE:
            self._pointer_inside = bool(message.get("value"))
            self.dispatcher.call(self._reschedule_dismissal)
            return
        if kind == protocol.C_SUBMIT_TEXT:
            text = str(message.get("text") or "")
            self.dispatcher.call(self.bridge.submitText, text)
            return
        if kind == protocol.C_SUBMIT_API_KEY:
            key = str(message.get("key") or "")
            self.dispatcher.call(self._submit_api_key, key)
            return
        if kind == protocol.C_DISMISS_CARD:
            self.dispatcher.call(self.bridge.dismissCard,
                                 str(message.get("uid") or ""))
            return
        if kind == protocol.C_CLEAR_CARDS:
            self.dispatcher.call(self.bridge.clearCards)
            return
        if kind == protocol.C_CLEAR_TRANSCRIPT:
            self.dispatcher.call(self.bridge.clearTranscript)
            return
        if kind == protocol.C_CANCEL_ACTIVITY:
            self.dispatcher.call(self.bridge.cancelActivity,
                                 str(message.get("aid") or ""))
            return
        if kind == protocol.C_TOGGLE_MUTE:
            self.dispatcher.call(self.bridge.toggleMute)
            return
        if kind == protocol.C_TOGGLE_SILENT:
            self.dispatcher.call(self.bridge.toggleSilent)
            return
        if kind == protocol.C_CANCEL:
            self.dispatcher.call(self.bridge.cancel)
            return
        if kind == protocol.C_BARGE_IN:
            self.dispatcher.call(self.bridge.bargeIn)
            return
        if kind == protocol.C_SET_THEME:
            self.apply_theme(str(message.get("name") or ""))
            return
        if kind == protocol.C_OPEN_INSPECTOR:
            self.open_inspector(str(message.get("tab") or "camera"))
            return
        if kind == protocol.C_CLOSE_INSPECTOR:
            self.close_inspector()
            return
        if kind == protocol.C_VISION_ACTION:
            self._on_vision_action(str(message.get("mode") or ""))
            return
        if kind == protocol.C_ARCADE_GAME:
            self._on_arcade_game(str(message.get("game") or ""))
            return
        if kind == protocol.C_COMMAND:
            self._run_command(str(message.get("text") or ""))
            return

    def _on_bridge_input(self, text: str, origin: str) -> None:
        """A typed/spoken turn was submitted in the renderer."""
        callback = self.on_text_command
        if callback is None:
            return
        threading.Thread(target=callback, args=(text,), daemon=True,
                         name="UiTextCommand").start()

    def _on_bridge_api_key(self, key: str) -> None:
        self._submit_api_key(key)

    def _submit_api_key(self, key: str) -> None:
        callback = self.on_api_key_submitted
        if callback is not None:
            try:
                callback(key)
                return
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[Controller] ⚠️ API key handler failed: {exc}")
        self.bridge.toast("That API key could not be saved.", "error")

    def _set_typing(self, typing: bool) -> None:
        self._typing = bool(typing)
        self._reschedule_dismissal()

    def _on_interaction(self) -> None:
        self._pointer_inside = True
        self._reschedule_dismissal()

    def _run_command(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        callback = self.on_text_command
        if callback is not None:
            threading.Thread(target=callback, args=(text,), daemon=True,
                             name="UiCommand").start()

    def _on_vision_action(self, mode: str) -> None:
        """Inspector vision pills -> backend command, with local state echoed."""
        mode = (mode or "").strip().upper()
        if not mode:
            return
        if mode in ("STOP", "OFF", "NONE"):
            self.bridge.set_vision_mode("STANDBY")
            self.bridge.set_vision_signal("Vision stopped")
            self._run_command("stop vision")
            return
        self.bridge.set_vision_mode(mode)
        self.bridge.set_vision_signal(f"{mode.title()} mode requested")
        self._run_command(f"start vision {mode.lower()}")

    def _on_arcade_game(self, game: str) -> None:
        game = (game or "").strip().lower()
        if not game:
            return
        self.dispatcher.call(self.bridge.setArcade, game, True)
        self._run_command("start vision gesture")

    # ── window ops (tray / hotkeys / invocation) ─────────────────────────────
    def show(self) -> None:
        """Summon Siri: the orb blooms into view. Safe from any thread."""
        self.dispatcher.call(self._show_ui)

    def _show_ui(self) -> None:
        was_hidden = self._hidden
        self._hidden = False
        self._dismiss_deadline = None
        if was_hidden:
            try:
                self.audio.play("wake")
            except Exception:
                pass
        self.bridge.set_invoked(True)
        self._reschedule_dismissal()

    def hide(self) -> None:
        """Dismiss Siri: the orb contracts, then the window hides."""
        self.dispatcher.call(self._hide_ui)

    def _hide_ui(self) -> None:
        self._hidden = True
        self._typing = False
        self._pointer_inside = False
        self._dismiss_deadline = None
        self.bridge.set_invoked(False)

    def invoke(self) -> None:
        """Explicit invocation (wake word / hotkey / tray / programmatic)."""
        self.show()

    def dismiss(self) -> None:
        """Graceful dismissal back to zero desktop footprint."""
        self.hide()

    def toggle_visibility(self) -> None:
        self.dispatcher.call(self._toggle_visibility_ui)

    def _toggle_visibility_ui(self) -> None:
        if self._hidden:
            self._show_ui()
        else:
            self.hide()

    def _on_window_dismissed(self) -> None:
        """The renderer finished its contraction animation."""
        if not self._hidden:
            self._hidden = True
            self._typing = False
            self._pointer_inside = False
            try:
                self.audio.play("done")
            except Exception:
                pass
        self._dismiss_deadline = None
        self.bridge.set_invoked(False)

    def raise_window(self) -> None:
        """Bring the overlay forward (Electron re-asserts always-on-top)."""
        self.ws.broadcast(protocol.dumps({"t": protocol.T_STATE,
                                          **self.bridge.state_payload()}))
        self.show()

    # ── inspector ────────────────────────────────────────────────────────────
    def open_inspector(self, tab: str = "camera") -> None:
        self.dispatcher.call(self.bridge.openInspector, tab)
        self.show()

    def close_inspector(self) -> None:
        self.dispatcher.call(self.bridge.closeInspector)

    def waive_grace(self) -> None:
        """Any user interaction pushes the reading grace back."""
        self.dispatcher.call(self._reschedule_dismissal)

    # ── theme ────────────────────────────────────────────────────────────────
    def apply_theme(self, name: str) -> None:
        if name not in THEMES:
            return
        self._theme_name = name
        self.theme = build_theme(name, light=not self.system_dark,
                                 reduced_motion=self.reduced_motion,
                                 transparency=self.transparency)
        self.dispatcher.call(self.bridge.apply_theme, self.theme)

    def cycle_theme(self) -> str:
        idx = (THEME_NAMES.index(self._theme_name)
               if self._theme_name in THEME_NAMES else 0)
        nxt = THEME_NAMES[(idx + 1) % len(THEME_NAMES)]
        self.apply_theme(nxt)
        return nxt

    # ── frames (thread-safe) ─────────────────────────────────────────────────
    def push_frame(self, stream: str, frame: Any) -> bool:
        """Store the newest camera/arcade frame. Callable from any thread."""
        if frame is None:
            return False
        if not self._frames_wanted(protocol.STREAM_BY_NAME.get(stream, 0)):
            return False
        return self.frames.push(stream, frame)

    # ── wake word ────────────────────────────────────────────────────────────
    def set_wake_status(self, active: bool, status: str = "") -> None:
        self._wake_active = bool(active)
        self._wake_status = status or ("Listening" if active else "Engine idle")
        self.dispatcher.call(self.bridge.set_wake, self._wake_active,
                             self._wake_status)

    # ── state machine reactions ──────────────────────────────────────────────
    def _on_state(self, tr: Any) -> None:
        try:
            if tr.current is UiState.SPEAKING:
                self.audio.on_speaking_start()
            elif tr.previous is UiState.SPEAKING:
                self.audio.on_speaking_end()
            if tr.current is UiState.MUTED:
                self.audio.play("mute")
            elif tr.current is UiState.LISTENING and tr.previous is UiState.MUTED:
                self.audio.play("listen")
        except Exception:
            pass
        # Show the overlay whenever Friday enters an active conversational or
        # work state so the orb is visible during the turn, not hidden.
        if tr.current in (UiState.THINKING, UiState.PROCESSING, UiState.SPEAKING):
            self.dispatcher.call(self._show_ui)
        self.dispatcher.call(self._reschedule_dismissal)

    # ── reading grace ────────────────────────────────────────────────────────
    def _busy(self) -> bool:
        """Whether dismissing now would interrupt the user or an active turn."""
        st = self.state.state
        if st not in (UiState.LISTENING, UiState.MUTED):
            return True
        if self._setup_visible or self.bridge.setupVisible:
            return True
        if self.bridge.awaitingConfirmation:
            return True
        if self.bridge.activeActivities > 0:
            return True
        if self._pointer_inside or self._typing:
            return True
        # Do not dissolve while the user is actively speaking.
        if self.bridge.micAmplitude > 0.055:
            return True
        return False

    def _reading_grace_ms(self) -> int:
        """Scale the quiet reading period with visible response length."""
        characters = len(self.bridge.lastEvent or "")
        try:
            for card in self.bridge.cards.raw():
                if card.get("kind") == CardKind.CONFIRMATION.value:
                    continue
                characters += len(str(card.get("title") or ""))
                characters += len(str(card.get("body") or ""))
                characters += len(str(card.get("footer") or ""))
        except Exception:
            pass
        return max(4500, min(14000, 3200 + characters * 38))

    def _reschedule_dismissal(self) -> None:
        if self._hidden or self._busy():
            self._dismiss_deadline = None
            return
        self._dismiss_deadline = time.monotonic() + (self._reading_grace_ms() / 1000.0)

    def _cancel_dismissal(self) -> None:
        self._dismiss_deadline = None

    def _check_dismissal(self) -> None:
        deadline = self._dismiss_deadline
        if deadline is None:
            return
        if time.monotonic() < deadline:
            return
        self._dismiss_deadline = None
        if not self._busy():
            self._hide_ui()

    # ── timers ───────────────────────────────────────────────────────────────
    def start(self) -> None:
        """Start the UI loop and housekeeping. Idempotent."""
        if self._started:
            return
        self._started = True
        self.dispatcher.start()
        self._tick_stop = threading.Event()
        threading.Thread(target=self._tick_loop, name="FridayUiTick",
                         daemon=True).start()
        threading.Thread(target=self._slow_loop, name="FridayUiSlow",
                         daemon=True).start()

    def _tick_loop(self) -> None:
        while not self._tick_stop.wait(self.TICK_MS / 1000.0):
            if self._shutdown_done:
                return
            try:
                self.dispatcher.call(self._ui_tick)
            except Exception:
                pass

    def _ui_tick(self) -> None:
        """4 Hz housekeeping, always on the UI thread."""
        try:
            self.state.tick()
            self.bridge.tick_clock()
            self.bridge.expire_content()
            self._check_dismissal()
        except Exception as exc:  # pragma: no cover
            print(f"[Controller] ⚠️ tick: {exc}")

    def _slow_loop(self) -> None:
        while not self._tick_stop.wait(self.SLOW_TICK_MS / 1000.0):
            if self._shutdown_done:
                return
            try:
                dnd = w32.do_not_disturb()
            except Exception:
                dnd = False
            if dnd != self._dnd:
                self._dnd = dnd
                self.dispatcher.call(self.bridge.set_dnd, dnd)
            if not self._monitors:
                self._monitors = w32.enumerate_monitors()

    # ── API key / setup ──────────────────────────────────────────────────────
    def set_setup_visible(self, visible: bool) -> None:
        value = bool(visible)
        self.dispatcher.call(self.bridge.set_setup_visible, value)

        def _apply() -> None:
            self._setup_visible = value
            if value:
                self._show_ui()

        self.dispatcher.call(_apply)

    # ── graceful interrupt ───────────────────────────────────────────────────
    def install_interrupt_handler(self) -> bool:
        """Route Ctrl+C to a clean shutdown instead of a KeyboardInterrupt.

        The first Ctrl+C asks the UI loop to tear down gracefully; the second
        exits immediately with code 130. Returns ``False`` (never raises) when
        signals are unavailable, e.g. off the main thread.
        """
        try:
            signal.signal(signal.SIGINT, self._on_sigint)
        except (ValueError, OSError, AttributeError):
            return False
        self._interrupt_installed = True
        return True

    def _on_sigint(self, _signum=None, _frame=None) -> None:
        if self._interrupted:
            print("[FRIDAY] 🛑 Second interrupt — exiting immediately.")
            os._exit(130)
        self._interrupted = True
        print("\n[FRIDAY] 🛑 Ctrl+C — shutting down cleanly...")
        self.request_quit()

    def request_quit(self) -> None:
        """Thread-safe "please exit": post the teardown to the UI thread."""
        def _quit() -> None:
            print("[FRIDAY] 👋 Tray stopped, audio restored, UI closed.")
            self.shutdown()
            self._closed.set()

        if self.dispatcher.running or self.dispatcher.on_ui_thread:
            self.dispatcher.call(_quit)
        else:  # pragma: no cover - teardown before the loop started
            _quit()

    # ── shutdown ─────────────────────────────────────────────────────────────
    def shutdown(self) -> None:
        # Idempotent: the SIGINT path, the renderer's Quit, the Electron watcher
        # and the event loop's finally-block can all arrive here. Tearing down
        # twice would restore audio that was never ducked and close sockets that
        # are already gone.
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._cancel_dismissal()

        tick_stop = getattr(self, "_tick_stop", None)
        if tick_stop is not None:
            tick_stop.set()

        # 1. Tell the renderer to close, then make sure it really exits.
        self._terminate_electron()

        # 2. Stop the transport.
        try:
            self.frames.stop()
        except Exception:
            pass
        try:
            self.ws.stop()
        except Exception:
            pass

        # 3. Restore any ducked audio session *before* the process can exit; if
        #    the machine was interrupted mid-reply this is the only thing that
        #    puts other apps' volume back.
        try:
            self.audio.stop()
        except Exception:
            pass

        # 4. The tray owns a Win32 message loop; stop() is the only way it ends.
        try:
            from actions import system_tray

            system_tray.stop()
        except Exception:
            pass

        # 5. Retire the UI loop last: every step above may still emit state.
        try:
            self.dispatcher.stop()
        except Exception:
            pass
        self._closed.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the UI has shut down. Returns True if it did."""
        return self._closed.wait(timeout)

    # ── diagnostics ──────────────────────────────────────────────────────────
    def diagnostics(self) -> dict:
        return {
            "backend": "electron-websocket",
            "theme": self._theme_name,
            "reducedMotion": self.reduced_motion,
            "transparency": self.transparency,
            "dark": self.system_dark,
            "dnd": self._dnd,
            "headless": self.headless,
            "visible": self.visible,
            "ws": self.ws.stats(),
            "frames": self.frames.stats(),
            "electron": {
                "path": str(electron_executable(self.app_dir) or ""),
                "install": ui_install_status(self.app_dir),
                "running": self.electron_running,
                "pid": getattr(self._electron, "pid", None),
                "error": self._electron_error,
            },
            "bus": self.bus.stats(),
            "dispatcher": self.dispatcher.stats(),
            "win32": w32.summary(),
        }


__all__ = [
    "UiController", "UiStartupError", "WsServer", "FramePump",
    "electron_executable", "electron_package_dir", "renderer_built",
    "renderer_files", "missing_ui_help", "ui_install_status",
    "THEMES", "THEME_NAMES", "DEFAULT_THEME", "build_theme", "APP_DIR",
    "DIST_DIR",
]
