"""FRIDAY ambient UI — multi-window orchestrator.

Owns:

* the four overlay layers (perimeter/screen-effects, anchor pill + stack +
  activity bar, inspector drawer) as separate top-level windows so each can
  carry different window flags;
* monitor tracking (the pill follows the monitor the user is working on);
* Win32 polish hooks (click-through, capture exclusion, DWM corners);
* the 4 Hz UI tick (timers, card TTL, clock, DND polling);
* the QML engine, theme table and the QML-side signal surface.

Everything here runs on the **GUI thread**. Producers reach it through
:class:`ui_siri.bridge.UiDispatcher` (wired up by ``ui.py``), or through the
ambient methods further down which are individually thread-safe.

Failure policy: if QML fails to load (corrupt install, missing QML modules),
the controller falls back to a *degraded* one-window mode that still satisfies
the legacy contract. It never raises into ``main.py``.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    Property,
    QObject,
    QPoint,
    QRect,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QScreen
from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, qmlRegisterSingletonType
from PySide6.QtQuick import QQuickImageProvider, QQuickItem, QQuickView

from . import win32_native as w32
from .audio_ux import AudioUX
from .bridge import UiBridge, UiDispatcher
from .event_bus import EventBus, Topics, UiState
from .image_provider import FridayImageProvider
from .state_machine import AssistantStateMachine
from .themes import (  # noqa: F401  (re-exported for convenience)
    DEFAULT_THEME,
    QML_DIR,
    QML_MODULE,
    THEMES,
    THEME_NAMES,
    build_theme,
)

# ─────────────────────────────────────────────────────────────────────────────
# QML singleton wiring
# ─────────────────────────────────────────────────────────────────────────────

_singleton_ready = False


def _register_singleton() -> None:
    """Register Theme.qml as a QML singleton under ``ui_siri.qml``.

    Both registration AND the matching ``import ui_siri.qml`` are required:
    the explicit ``qmlRegisterSingletonType`` above makes ``Theme`` resolvable
    from the module, and the import is what actually pulls the module in. A
    ``qmldir`` alone does not make ``Theme`` a global name.
    """
    global _singleton_ready
    if _singleton_ready:
        return

    # QUrl.fromLocalFile, never an f-string: a Windows path is C:\... and a
    # hand-built file:// URL would not survive the backslashes or the drive colon.
    qmlRegisterSingletonType(QUrl.fromLocalFile(str((QML_DIR / "Theme.qml").resolve())),
                             QML_MODULE, 1, 0, "Theme")
    _singleton_ready = True


# ─────────────────────────────────────────────────────────────────────────────
# Window wrappers
# ─────────────────────────────────────────────────────────────────────────────

class _LayerWindow(QQuickView):
    """A top-level transparent QML window with per-layer Win32 behaviour."""

    def __init__(self, qml_file: str, *, click_through: bool,
                 capture_excluded: bool, focusable: bool, dark: bool = True,
                 initial_properties: dict[str, Any] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._dark = bool(dark)
        self.setColor(QColor(0, 0, 0, 0))
        self.setFlags(Qt.WindowType.FramelessWindowHint
                      | Qt.WindowType.WindowStaysOnTopHint
                      | Qt.WindowType.NoDropShadowWindowHint
                      | Qt.WindowType.Tool)
        if not focusable:
            self.setFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self._click_through = click_through
        self._capture_excluded = capture_excluded
        self._qml_file = qml_file
        self._polished = False
        self._root: QQuickItem | None = None

        self.statusChanged.connect(self._on_status)
        self.load_error = ""
        self.load_errors: list[tuple[str, int, int, str]] = []

        # `required property` bindings must exist BEFORE the component is
        # instantiated: setting them afterwards means Qt warns "Required
        # property ... was not initialized" and, for a `var` holding an object,
        # the QML that binds to it never re-evaluates. setInitialProperties +
        # loadFromModule is the supported order.
        self.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        if initial_properties:
            self.setInitialProperties(initial_properties)
        self.loadFromModule(QML_MODULE, Path(qml_file).stem)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def _on_status(self, status) -> None:
        if status == QQuickView.Status.Error:
            # Newlines kept on purpose: a QML error is multi-line (message,
            # file, line, column) and joining it into one line is how the
            # original "silently broken window" bug hid.
            details = "\n".join(
                f"    {e.url().toString()}:{e.line()}:{e.column()}: {e.description()}"
                for e in self.errors()) or "    (no detail reported by Qt)"
            self.load_error = details
            self.load_errors = [(e.url().toString(), e.line(), e.column(),
                                 e.description()) for e in self.errors()]
            print(f"[Controller] ⚠️ {self._qml_file} failed to load:\n{details}")

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._polished:
            self._polished = True
            QTimer.singleShot(0, self.apply_native)

    def apply_native(self) -> None:
        """Apply Win32 behaviour once the HWND exists (and after show)."""
        try:
            hwnd = int(self.winId())
        except Exception:
            hwnd = 0
        if not hwnd:
            return
        if self._click_through:
            w32.make_click_through(hwnd, True)
        w32.keep_on_top(hwnd, True, no_activate=self._click_through)
        if self._capture_excluded:
            w32.exclude_from_capture(hwnd, True)
        # `dark` follows the OS theme; rounded corners are skipped for the
        # click-through overlay (DWM refuses on layered tool windows).
        w32.apply_dwm_polish(hwnd, dark=self._dark,
                             rounded=not self._click_through)

    def set_click_through(self, enabled: bool) -> None:
        self._click_through = enabled
        try:
            w32.make_click_through(int(self.winId()), enabled)
        except Exception:
            pass

    # ── helpers ──────────────────────────────────────────────────────────────
    @property
    def root_item(self) -> QQuickItem | None:
        return self.rootObject()

    def set_root_property(self, name: str, value: Any) -> bool:
        obj = self.rootObject()
        if obj is None:
            return False
        try:
            obj.setProperty(name, value)
            return True
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Controller
# ─────────────────────────────────────────────────────────────────────────────

class UiController(QObject):
    """Owns the ambient UI for the lifetime of the process."""

    # ── QML-facing signals (mirrored from the bridge) ────────────────────────
    reloadTheme = Signal("QVariantMap")
    inspectorChanged = Signal()
    toast = Signal(str, str)
    apiKeySubmitted = Signal(str)

    TICK_MS = 250

    def __init__(
        self,
        app: QGuiApplication | None = None,
        *,
        theme_name: str = "cyber",
        audio: AudioUX | None = None,
        enable_overlay: bool = True,
        enable_capture_exclusion: bool = True,
        start_hidden: bool = False,
    ) -> None:
        super().__init__()
        self.app = app or QGuiApplication.instance()
        if self.app is None:
            raise RuntimeError("UiController requires a QGuiApplication")

        self.bus = EventBus()
        self.state = AssistantStateMachine()
        self.dispatcher = UiDispatcher(self)
        self.dispatcher.bind_gui_thread()

        # ── system probes (all non-fatal) ────────────────────────────────────
        caps = w32.probe()
        self.reduced_motion = not w32.animations_enabled()
        self.transparency = w32.transparency_enabled()
        self.system_dark = w32.system_dark_mode()
        self._dnd = False
        self._monitors = w32.enumerate_monitors()
        self._scale = self._detect_scale()
        self._system_dark = self.system_dark

        self._theme_name = theme_name if theme_name in THEMES else "cyber"
        self.theme = build_theme(self._theme_name, light=not self.system_dark,
                                 reduced_motion=self.reduced_motion,
                                 transparency=self.transparency,
                                 scale=self._scale)

        # ── image provider + QML engines ─────────────────────────────────────
        self.images = FridayImageProvider()
        self.engine = QQmlEngine()
        self.engine.addImageProvider(FridayImageProvider.PROVIDER_ID, self.images)
        # `ui_siri.qml` is the module uri declared in ui_siri/qml/qmldir, so
        # the import root is the repository (or bundle) directory — QML_DIR's
        # parent — NOT QML_DIR itself.
        # resolved() paths only: Qt compares import paths literally, and a
        # relative or symlinked path silently fails to match on Windows.
        self.engine.addImportPath(str(QML_DIR.parent.resolve()))
        self.engine.addImportPath(str(QML_DIR.resolve()))
        _register_singleton()
        self.engine.rootContext().setContextProperty("fridayTheme", self.theme)

        # ── bridge ───────────────────────────────────────────────────────────
        self.bridge = UiBridge(self.state, self.bus, self.dispatcher, self.theme)
        self.engine.rootContext().setContextProperty("friday", self.bridge)
        self.engine.rootContext().setContextProperty("fridayController", self)

        # ── audio UX ─────────────────────────────────────────────────────────
        self.audio = audio or AudioUX(enabled=True, ducking=False)
        self.audio.set_dnd_probe(lambda: self._dnd)

        # ── windows ──────────────────────────────────────────────────────────
        self.overlay: _LayerWindow | None = None
        self.anchor: _LayerWindow | None = None
        self.inspector: _LayerWindow | None = None
        self._degraded = False
        self._hidden = bool(start_hidden)
        self._pill_expanded = False
        self._setup_visible = False
        # Filled from the backend's wake-word engine when it reports in.
        self._wake_active = False
        self._wake_status = "Engine idle"

        if enable_overlay:
            self._create_windows(capture_excluded=enable_capture_exclusion)
        else:
            self._degraded = True

        # ── state machine → audio/effects ────────────────────────────────────
        self.state.add_listener(self._on_state)

        # ── periodic work ────────────────────────────────────────────────────
        self._tick = QTimer(self)
        self._tick.setInterval(self.TICK_MS)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

        self._slow = QTimer(self)
        self._slow.setInterval(4000)
        self._slow.timeout.connect(self._on_slow_tick)
        self._slow.start()

        # Drain the bus on the GUI thread at ~30 Hz: cheap, and keeps the
        # transcript live even while QML is busy animating.
        # Bus drain (event_bus -> QML models).
        self._drain = QTimer(self)
        self._drain.setInterval(33)
        self._drain.timeout.connect(self._on_drain)
        self._drain.start()
        # Dispatcher drain (cross-thread legacy calls -> GUI thread).
        self.dispatcher.start()

        self.bridge.apply_theme(self.theme)
        self.reloadTheme.emit(self.theme)

    def qml_errors(self) -> list[tuple[str, str, int, int, str]]:
        """Every window that failed to load: (layer, url, line, column, message).

        ``make_ui()`` treats a non-empty result as a startup failure when
        FRIDAY_STRICT_UI=1, and logs it unconditionally otherwise. A window
        that failed to load but still "exists" is the worst case: the app runs,
        shows nothing, and looks hung.
        """
        found: list[tuple[str, str, int, int, str]] = []
        for layer, win in (("overlay", self.overlay), ("anchor", self.anchor),
                           ("inspector", self.inspector)):
            for url, line, column, message in getattr(win, "load_errors", []) or []:
                found.append((layer, url, line, column, message))
        return found

    # ── construction ─────────────────────────────────────────────────────────
    def _detect_scale(self) -> float:
        try:
            screen = self.app.primaryScreen()
            if screen is not None:
                dpr = float(screen.devicePixelRatio())
                # Clamp: 2.0+ would make the pill enormous on 4K at 200%.
                return max(0.85, min(1.6, dpr))
        except Exception:
            pass
        return 1.0

    def _create_windows(self, capture_excluded: bool) -> None:
        try:
            self.overlay = _LayerWindow(
                "OverlayWindow.qml", click_through=True,
                capture_excluded=capture_excluded, focusable=False,
                dark=self._system_dark,
                initial_properties={"bridge": self.bridge,
                                    "reducedMotion": self.reduced_motion,
                                    "themeData": self.theme})
            self.overlay.visibilityChanged.connect(self._sync_overlay_geometry)
        except Exception as exc:
            print(f"[Controller] ⚠️ overlay window unavailable: {exc}")
            self.overlay = None

        try:
            self.anchor = _LayerWindow(
                "AnchorWindow.qml", click_through=False,
                capture_excluded=capture_excluded, focusable=True,
                dark=self._system_dark,
                initial_properties={"bridge": self.bridge,
                                    "reducedMotion": self.reduced_motion,
                                    "setupVisible": self._setup_visible,
                                    "themeData": self.theme})
            root = self.anchor.rootObject()
            if root is not None:
                for signal_name, handler in (("contentHeightChanged", self._resize_anchor),
                                             ("apiKeySubmitted", self._on_api_key_submitted)):
                    try:
                        getattr(root, signal_name).connect(handler)
                    except Exception:
                        pass
            self.bridge.setupChanged.connect(self._sync_setup_visibility)
            if self.anchor.load_error:
                # Keep the window object so qml_errors() can report it; just
                # stop treating it as usable.
                print("[Controller] ⚠️ anchor QML did not load; the ambient "
                      "overlay cannot be shown")
                self.anchor.hide()
                self._degraded = True
        except Exception as exc:
            print(f"[Controller] ⚠️ anchor window unavailable: {exc}")
            self.anchor = None
            self._degraded = True

        self._create_inspector(capture_excluded)
        self._layout_windows()
        self._install_screen_tracking()

    def _create_inspector(self, capture_excluded: bool) -> None:
        """Layer 4: created lazily-on-demand but kept alive once built so tab
        state and the camera feed survive closing/reopening."""
        try:
            self.inspector = _LayerWindow(
                "InspectorWindow.qml", click_through=False,
                capture_excluded=False, focusable=True, dark=self._system_dark,
                initial_properties={"bridge": self.bridge,
                                    "reducedMotion": self.reduced_motion,
                                    "themeData": self.theme,
                                    "props": self._inspector_props()})
            if self.inspector.load_error:
                print("[Controller] ⚠️ inspector QML did not load; "
                      "the Inspector will be unavailable")
                self.inspector.close()
                self.inspector = None
            else:
                self.inspector.hide()
        except Exception as exc:
            print(f"[Controller] ⚠️ inspector unavailable: {exc}")
            self.inspector = None
        self.bridge.inspectorChanged.connect(self._sync_inspector)

    def _inspector_props(self) -> dict:
        bridge = self.bridge
        return {
            "tab": bridge.inspectorTab or "camera",
            "cameraSource": self.images.url("vision")
                            if bridge.visionPreviewOpen else "",
            "arcadeSource": self.images.url("arcade") if bridge.arcadeOpen else "",
            "arcadeGame": bridge.arcadeGame,
            "arcadeScore": bridge.arcadeScore,
            "arcadeLives": bridge.arcadeLives,
            "arcadeMessage": bridge.arcadeMessage,
            "visionSignal": bridge.visionSignal,
            "wakePhrase": "Hey Friday",
            "wakeStatus": getattr(self, "_wake_status", "Engine idle"),
            "wakeActive": bool(getattr(self, "_wake_active", False)),
            "personaBadge": bridge.personaBadge,
            "theme": self._theme_name,
            "reducedMotion": self.reduced_motion,
            "transparency": self.transparency,
            "alwaysOnTop": True,
            "captureExcluded": True,
            "diagnostics": self.bridge.diagnostics(),
        }

    def _sync_inspector(self) -> None:
        if self.inspector is None:
            return
        try:
            self.inspector.set_root_property("props", self._inspector_props())
            if self.bridge.inspectorOpen:
                screen = self.active_screen()
                if screen is not None:
                    avail = screen.availableGeometry()
                    w = min(int(760 * self._scale), avail.width() - 40)
                    h = min(int(560 * self._scale), avail.height() - 40)
                    self.inspector.setGeometry(QRect(
                        avail.x() + (avail.width() - w) // 2,
                        avail.y() + (avail.height() - h) // 2, w, h))
                self.inspector.show()
                self.inspector.requestActivate()
            else:
                self.inspector.hide()
        except Exception as exc:
            print(f"[Controller] ⚠️ inspector sync: {exc}")

    def _install_screen_tracking(self) -> None:
        try:
            self.app.screenAdded.connect(lambda _s: self._refresh_monitors())
            self.app.screenRemoved.connect(lambda _s: self._refresh_monitors())
            for screen in self.app.screens():
                screen.geometryChanged.connect(lambda *_a: self._layout_windows())
                screen.availableGeometryChanged.connect(lambda *_a: self._layout_windows())
        except Exception as exc:
            print(f"[Controller] ℹ️ screen tracking partial: {exc}")

    # ── geometry ─────────────────────────────────────────────────────────────
    def _refresh_monitors(self) -> None:
        self._monitors = w32.enumerate_monitors()
        self._layout_windows()

    def active_screen(self) -> QScreen | None:
        try:
            return self.app.screenAt(self._anchor_point()) or self.app.primaryScreen()
        except Exception:
            return self.app.primaryScreen()

    def _anchor_point(self) -> QPoint:
        if self.anchor is not None:
            geo = self.anchor.geometry()
            return geo.center()
        return QPoint(0, 0)

    def _remember_anchor(self) -> None:
        """Persist the pill position (per user, per machine)."""
        if self.anchor is None:
            return
        try:
            from . import __init__ as pkg  # noqa: F401

            cfg = QML_DIR.parent.parent / "config"
            cfg.mkdir(parents=True, exist_ok=True)
            geo = self.anchor.geometry()
            (cfg / "ui_anchor.json").write_text(
                f'{{"x": {geo.x()}, "y": {geo.y()}}}', encoding="utf-8")
        except Exception:
            pass

    def _load_anchor(self, screen: QScreen) -> QPoint:
        avail = screen.availableGeometry()
        default = QPoint(avail.x() + (avail.width() - 460) // 2,
                         avail.y() + 16)
        try:
            import json

            path = QML_DIR.parent.parent / "config" / "ui_anchor.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                x, y = int(data.get("x", default.x())), int(data.get("y", default.y()))
                # Keep it on a real monitor after a display change.
                for mon in self._monitors:
                    if mon.contains(x, y):
                        return QPoint(x, y)
        except Exception:
            pass
        return default

    def _layout_windows(self) -> None:
        try:
            screen = self.active_screen()
            if screen is None:
                return
            geo: QRect = screen.geometry()
            avail: QRect = screen.availableGeometry()

            if self.overlay is not None:
                self.overlay.setGeometry(geo)
                if not self.overlay.isVisible() and not self._hidden:
                    self.overlay.show()

            if self.anchor is not None:
                pill_w = int(456 * self._scale)
                # Tall enough for the pill + one card, and never shrunk on
                # relayout: QML needs stable bounds while animating.
                pill_h = max(int(240 * self._scale),
                             self.anchor.height() if self.anchor.isVisible() else 0)
                pos = self._load_anchor(screen) if not self.anchor.isVisible() \
                    else self.anchor.geometry().topLeft()
                self.anchor.setGeometry(QRect(pos.x(), pos.y(), pill_w, pill_h))
                if not self.anchor.isVisible() and not self._hidden:
                    self.anchor.show()
        except Exception as exc:
            print(f"[Controller] ⚠️ layout failed: {exc}")

    def _resize_anchor(self) -> None:
        """Grow/shrink the anchor window to fit the stack (stable, no flicker)."""
        if self.anchor is None:
            return
        root = self.anchor.rootObject()
        if root is None:
            return
        try:
            # `contentHeight` is a QML *function* on the root. property() hands
            # back a bound-method wrapper (never 0), so float() on it raises and
            # the whole resize silently no-ops — the stack would then be clipped
            # by the fixed initial height. Call it directly.
            content = float(root.contentHeight())
            wanted = int(max(110 * self._scale, content + 24 * self._scale))
            current = self.anchor.height()
            if wanted > current + 6:
                self.anchor.setHeight(wanted)
            elif wanted < current - 40:
                # Shrink only with hysteresis, so a card expiring mid-scroll
                # does not cause a visible jump.
                self.anchor.setHeight(max(int(240 * self._scale), wanted))
        except Exception:
            pass

    def _sync_overlay_geometry(self, *_args) -> None:
        if self.overlay is None:
            return
        try:
            screen = self.active_screen()
            if screen is not None and self.overlay.geometry() != screen.geometry():
                self.overlay.setGeometry(screen.geometry())
        except Exception:
            pass

    # ── public window ops (legacy tray parity) ───────────────────────────────
    def show(self) -> None:
        self._hidden = False
        for win in (self.overlay, self.anchor):
            if win is not None:
                win.show()

    def hide(self) -> None:
        self._hidden = True
        for win in (self.overlay, self.anchor):
            if win is not None:
                win.hide()

    def toggle_visibility(self) -> None:
        if self._hidden:
            self.show()
        else:
            self.hide()

    @property
    def visible(self) -> bool:
        return not self._hidden

    def open_inspector(self, tab: str = "camera") -> None:
        self.bridge.openInspector(tab)

    def close_inspector(self) -> None:
        self.bridge.closeInspector()

    def focus_input(self) -> None:
        """Expand the pill and put the caret in the text field."""
        if self.anchor is not None:
            self.anchor.show()
            self.anchor.requestActivate()
        try:
            root = self.anchor.rootObject() if self.anchor else None
            if root is not None:
                item = root.findChild(QQuickItem, "input")
                if item is not None:
                    item.forceActiveFocus()
        except Exception:
            pass

    # ── theme ────────────────────────────────────────────────────────────────
    def apply_theme(self, name: str) -> None:
        if name not in THEMES:
            return
        self._theme_name = name
        self.theme = build_theme(name, light=not self.system_dark,
                                 reduced_motion=self.reduced_motion,
                                 transparency=self.transparency, scale=self._scale)
        self.bridge.apply_theme(self.theme)   # QML is bound to bridge.theme
        for win in (self.overlay, self.anchor, self.inspector):
            if win is not None:
                win.set_root_property("themeData", self.theme)
        self.reloadTheme.emit(self.theme)

    def cycle_theme(self) -> str:
        idx = THEME_NAMES.index(self._theme_name) if self._theme_name in THEME_NAMES else 0
        nxt = THEME_NAMES[(idx + 1) % len(THEME_NAMES)]
        self.apply_theme(nxt)
        return nxt

    # ── frame ingestion (thread-safe) ────────────────────────────────────────
    def push_frame(self, stream: str, frame: Any) -> bool:
        """Store the newest camera/arcade frame. Callable from any thread.

        The pixels go into the provider lock-free from the caller's thread; the
        QML-facing revision bump *must* happen on the GUI thread, so it is
        routed through the dispatcher. A 30 fps stream therefore costs one
        queued callable per frame, not a signal per frame.
        """
        ok = self.images.push(stream, frame)
        if ok and (stream == "vision" or stream == "arcade"):
            try:
                self.dispatcher.call(self.bridge.notify_frames)
            except Exception:
                pass
        return ok

    def frame_url(self, stream: str) -> str:
        return self.images.url(stream)

    # ── timers ───────────────────────────────────────────────────────────────
    def _on_drain(self) -> None:
        try:
            self.bus.drain(max_items=512)
        except Exception as exc:  # pragma: no cover
            print(f"[Controller] ⚠️ drain: {exc}")

    def _on_tick(self) -> None:
        try:
            self.state.tick()
            self.bridge.tick_clock()
            self.bridge.expire_content()
        except Exception as exc:  # pragma: no cover
            print(f"[Controller] ⚠️ tick: {exc}")

    def _sync_setup_visibility(self) -> None:
        self._setup_visible = bool(self.bridge.setupVisible)
        if self.anchor is not None:
            self.anchor.set_root_property("setupVisible", self._setup_visible)
            self._resize_anchor()

    def _on_api_key_submitted(self, key: str) -> None:
        """QML handed us a key. Persist it and unblock the voice worker."""
        self.apiKeySubmitted.emit(str(key or ""))

    def set_wake_status(self, active: bool, status: str = "") -> None:
        """Called by ui.py when the wake-word engine state is known."""
        self._wake_active = bool(active)
        self._wake_status = status or ("Listening" if active else "Engine idle")
        if self.bridge.inspectorOpen:
            self._sync_inspector()

    def _on_slow_tick(self) -> None:
        try:
            dnd = w32.do_not_disturb()
            if dnd != self._dnd:
                self._dnd = dnd
                self.bridge.set_dnd(dnd)
            if self._monitors == []:
                self._refresh_monitors()
        except Exception:
            pass

    # ── state machine reactions ──────────────────────────────────────────────
    def _on_state(self, tr) -> None:
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

    # ── shutdown ─────────────────────────────────────────────────────────────
    def shutdown(self) -> None:
        for timer in (self._tick, self._slow, self._drain):
            try:
                timer.stop()
            except Exception:
                pass
        try:
            self.dispatcher.stop()
        except Exception:
            pass
        self._remember_anchor()
        try:
            self.audio.stop()
        except Exception:
            pass
        for win in (self.inspector, self.anchor, self.overlay):
            try:
                if win is not None:
                    win.close()
            except Exception:
                pass

    # ── diagnostics ──────────────────────────────────────────────────────────
    def diagnostics(self) -> dict:
        return {
            "theme": self._theme_name,
            "scale": self._scale,
            "reducedMotion": self.reduced_motion,
            "transparency": self.transparency,
            "dark": self.system_dark,
            "dnd": self._dnd,
            "degraded": self._degraded,
            "windows": {
                "overlay": self.overlay is not None,
                "anchor": self.anchor is not None,
            },
            "images": self.images.status(),
            "bus": self.bus.stats(),
            "dispatcher": self.dispatcher.stats(),
            "win32": w32.summary(),
        }


__all__ = ["UiController", "THEMES", "THEME_NAMES", "build_theme", "QML_DIR"]
