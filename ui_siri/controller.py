"""FRIDAY ambient UI — multi-window orchestrator.

Owns:

- the four overlay layers (perimeter/screen-effects, anchor pill + stack +
  activity bar, inspector drawer) as separate top-level windows so each can
  carry different window flags;
- monitor tracking (the pill follows the monitor the user is working on);
- Win32 polish hooks (click-through, capture exclusion, DWM corners);
- the 4 Hz UI tick (timers, card TTL, clock, DND polling);
- the QML engine, theme table and the QML-side signal surface.

Everything here runs on the **GUI thread**. Producers reach it through
:class:`ui_siri.bridge.UiDispatcher` (wired up by ``ui.py``), or through the
ambient methods further down which are individually thread-safe.

Failure policy: if QML fails to load (corrupt install, missing QML modules),
the controller falls back to a *degraded* one-window mode that still satisfies
the legacy contract. It never raises into ``main.py``.
"""

from __future__ import annotations

import os
import signal
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
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QScreen
from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, qmlRegisterSingletonType
from PySide6.QtQuick import QQuickImageProvider, QQuickItem, QQuickView

from . import win32_native as w32
from .audio_ux import AudioUX
from .bridge import UiBridge, UiDispatcher
from .event_bus import CardKind, EventBus, Topics, UiState
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
                 dwm_naked: bool = False,
                 initial_properties: dict[str, Any] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._dark = bool(dark)
        self._dwm_naked = bool(dwm_naked)
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
        self._closing = False
        self._root: QQuickItem | None = None

        self.statusChanged.connect(self._on_status)
        self.load_error = ""
        self.load_errors: list[tuple[str, int, int, str]] = []

        # `required property` bindings must exist BEFORE the component is
        # instantiated: setting them afterwards means Qt warns "Required
        # property ... was not initialized" and the QML that binds to them
        # never re-evaluates.
        #
        # setInitialProperties + setSource, NOT loadFromModule: the latter
        # resolves the type out of the `ui_siri.qml` module, and on Windows it
        # failed with:
        #     Module "ui_siri.qml" contains no type named "OverlayWindow"
        # setSource takes a direct file URL, so the windows do not have to be
        # exported as module types at all. The module still exists for the
        # `Theme` singleton, which every file resolves through qmldir.
        self.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        if initial_properties:
            self.setInitialProperties(initial_properties)
        self.setSource(QUrl.fromLocalFile(str((QML_DIR / qml_file).resolve())))

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

    def prepare_close(self) -> None:
        self._closing = True

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closing = True
        super().closeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        self._closing = False
        super().showEvent(event)
        if not self._polished:
            self._polished = True
            QTimer.singleShot(0, self.apply_native)

    def apply_native(self) -> None:
        """Apply Win32 behaviour once the HWND exists (and after show)."""
        if self._closing or not self.isVisible():
            return
        try:
            hwnd = int(self.winId())
        except Exception:
            hwnd = 0
        if not hwnd:
            return
        if self._click_through:
            w32.make_click_through(hwnd, True)
        # WS_EX_NOACTIVATE: voice-summoned overlays must never steal focus.
        w32.keep_on_top(hwnd, True, no_activate=True)
        if self._capture_excluded:
            w32.exclude_from_capture(hwnd, True)
        # `dark` follows the OS theme; rounded corners are skipped for the
        # click-through overlay (DWM refuses on layered tool windows).
        #
        # SiriWindow is created with dwm_naked=True: we do NOT apply any DWM
        # polish (no dark mode, no rounded corners, no backdrop). Instead we
        # explicitly set DWMWCP_DONOTROUND + DWMWA_COLOR_NONE so Windows 11
        # draws ZERO visible border, shadow, or rounded-rectangle outline
        # around the transparent HWND — even when the orb is dormant.
        if self._dwm_naked:
            w32.apply_dwm_naked(hwnd)
        elif not self._click_through:
            w32.apply_dwm_polish_no_backdrop(hwnd, dark=self._dark,
                                             rounded=True)

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
        theme_name: str = "siri",
        audio: AudioUX | None = None,
        enable_overlay: bool = True,
        enable_capture_exclusion: bool = True,
        start_hidden: bool = False,
    ) -> None:
        super().__init__()
        self.app = app or QGuiApplication.instance()
        if self.app is None:
            raise RuntimeError("UiController requires a QGuiApplication")

        # Interrupt state must exist before any handler can run.
        self._interrupted = False
        self._interrupt_installed = False
        self._shutdown_done = False

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
        self._dpi = self._detect_scale()
        self._scale = self._design_scale(self._dpi)
        self._system_dark = self.system_dark

        self._theme_name = theme_name if theme_name in THEMES else DEFAULT_THEME
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
        self.siri: _LayerWindow | None = None
        self.overlay: _LayerWindow | None = None
        self.anchor: _LayerWindow | None = None
        self.inspector: _LayerWindow | None = None
        self._degraded = False
        self._hidden = bool(start_hidden)
        self._pill_expanded = False
        self._setup_visible = False
        # Last applied stage rect; re-positioning identical geometry every
        # layout pass would make Qt re-upload the whole transparent surface.
        self._stage_geometry: QRect | None = None
        # Input footprint: the stage is click-through until the pointer is over
        # something interactive (or the user is typing).
        self._siri_interactive: bool | None = None
        self._pointer_inside = False
        # Pending invoked state applied once the Siri QML has finished loading
        # (set_root_property silently fails if rootObject() is None before load).
        self._pending_invoked = False
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
        self._desktop_input = QTimer(self)
        self._desktop_input.setInterval(45)
        self._desktop_input.timeout.connect(self._poll_desktop_input)
        self._escape_was_down = False

        self.bridge.setupChanged.connect(self._sync_setup_visibility)
        self.bridge.liveTranscriptChanged.connect(self._reschedule_dismissal)

        # Bus drain (event_bus -> QML models).
        self._drain = QTimer(self)
        self._drain.setInterval(33)
        self._drain.timeout.connect(self._on_drain)
        self._drain.start()
        # Dispatcher drain (cross-thread legacy calls -> GUI thread).
        self.dispatcher.start()

        self.bridge.apply_theme(self.theme)
        self.reloadTheme.emit(self.theme)

        # ── auto-dismissal grace ──────────────────────────────────────────
        # When Siri settles back into LISTENING with no cards or live
        # activities, the stage dissolves to invisibility after a reading
        # grace period. Any new card, activity, or interaction resets it.
        self._dismissTimer = QTimer(self)
        self._dismissTimer.setSingleShot(True)
        self._dismissalGraceMs = 5000
        self._dismissTimer.setInterval(self._dismissalGraceMs)
        self._dismissTimer.timeout.connect(self._on_dismiss_due)

        self.bridge.cardsChanged.connect(self._reschedule_dismissal)
        self.bridge.activityChanged.connect(self._reschedule_dismissal)
        self.state.add_listener(self._on_state_for_grace)

    def qml_errors(self) -> list[tuple[str, str, int, int, str]]:
        """Every window that failed to load: (layer, url, line, column, message).

        ``make_ui()`` treats a non-empty result as a startup failure and logs
        it unconditionally. A window that failed to load but still "exists" is
        the worst case: the app runs, shows nothing, and looks hung.
        """
        found: list[tuple[str, str, int, int, str]] = []
        seen: set[int] = set()
        # ``anchor`` is a pure alias of ``siri`` (same _LayerWindow object),
        # so deduplicate by identity to avoid reporting the same load error
        # twice (once for [siri], once for [anchor]).
        for layer, win in (("siri", self.siri), ("anchor", self.anchor),
                           ("inspector", self.inspector)):
            if win is None or id(win) in seen:
                continue
            seen.add(id(win))
            for url, line, column, message in getattr(win, "load_errors", []) or []:
                found.append((layer, url, line, column, message))
        return found

    # ── construction ─────────────────────────────────────────────────────────
    def _detect_scale(self) -> float:
        """Device pixel ratio of the primary screen (clamped)."""
        try:
            screen = self.app.primaryScreen()
            if screen is not None:
                dpr = float(screen.devicePixelRatio())
                # Clamp: 2.0+ would make the pill enormous on 4K at 200%.
                return max(0.85, min(2.0, dpr))
        except Exception:
            pass
        return 1.0

    def _design_scale(self, dpi: float) -> float:
        """QML design points -> screen-absolute pixels.

        Qt maps QML coordinates to physical pixels through the window's DPR,
        and it *keeps* that ratio at 125 % / 150 % / 175 % scaling (it rounds
        the ratio to a nice fraction and renders the frame buffer at the device
        resolution). So a `88 * uiScale` orb is drawn at 1.5 * 88 = 132 physical
        pixels at 150 % scaling — physically *smaller* than at 100 % and, more
        importantly, not the size the design intends.

        Dividing the design multiplier by the DPR cancels Qt's mapping exactly:
        the final physical size is `uiScale * DPR` = the design point value,
        so the orb is the same physical size on every display and is rasterised
        at the native device resolution (crisp, never upscaled).
        """
        try:
            dpi = float(dpi) or 1.0
        except (TypeError, ValueError):
            dpi = 1.0
        return max(0.6, min(1.25, 1.0 / dpi))

    def _create_windows(self, capture_excluded: bool) -> None:
        try:
            self.siri = _LayerWindow(
                "SiriWindow.qml", click_through=True,
                capture_excluded=False, focusable=False,
                dark=self._system_dark, dwm_naked=True,
                initial_properties={"bridge": self.bridge,
                                    "reducedMotion": self.reduced_motion,
                                    "themeData": self.theme})
            # Legacy-compat alias: ui.py and tests address the stage as `anchor`.
            self.anchor = self.siri
            root = self.siri.rootObject()
            if root is not None:
                root.apiKeySubmitted.connect(self._on_api_key_submitted)
                root.dismissed.connect(self.windowDismissed)
                # Every explicit interaction (hover, click, card reply) pushes
                # the reading grace back, so Siri never dissolves under the
                # user's cursor.
                root.interaction.connect(self.on_interaction)
                root.typingChanged.connect(self._on_typing_changed)
            # Re-apply pending invoked state once the QML finishes loading.
            self.siri.statusChanged.connect(self._on_siri_status_changed)
            if self.siri.load_error:
                print("[Controller] ⚠️ Siri QML did not load; the ambient "
                      "overlay cannot be shown")
                self.siri.hide()
                self._degraded = True
        except Exception as exc:
            print(f"[Controller] ⚠️ Siri window unavailable: {exc}")
            self.siri = None
            self.anchor = None
            self._degraded = True

        self._create_inspector(capture_excluded)
        self._layout_windows()
        self._install_screen_tracking()

    def _on_siri_status_changed(self, _status) -> None:
        """Apply pending invoked state once the Siri QML has finished loading.

        ``set_root_property("invoked", True)`` silently fails if
        ``rootObject()`` is None because the QML hasn't loaded yet. When the
        status reaches Ready, re-apply the pending invoked state so the orb
        springs into view even if show() was called before load completed.
        """
        if self._pending_invoked and self.siri is not None:
            from PySide6.QtQuick import QQuickView
            if self.siri.status() == QQuickView.Status.Ready:
                self._pending_invoked = False
                self.siri.set_root_property("invoked", True)

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
            if screen is None or self.siri is None:
                return
            avail: QRect = screen.availableGeometry()

            # Apple Siri placement: bottom-centre of the available screen
            # geometry, centred horizontally with a clean margin above the
            # taskbar — never wall-to-wall, never covering the user's focus.
            #
            # The width is *maximised* rather than fixed, and that is a
            # rendering decision, not a layout one: the stage reaches from
            # screen edge to screen edge so the cards' feathered edge shadow
            # can never be clipped by the window boundary. The window itself
            # stays 100 % transparent and only the small centre column paints,
            # so nothing of it is visible or hit-testable.
            g = screen.geometry()
            x = g.x()
            stage_w = max(int(420 * self._scale), g.width())
            # Tall enough for the three-card stack plus orb, transcript, live
            # activity capsule and the typing pill. A shorter window clips the
            # top card mid-glyph, which is the one thing the stack must never
            # do; the height is capped to the available desktop so it can never
            # spill under a taskbar on a short screen.
            stage_h = min(int(620 * self._scale), max(int(320 * self._scale),
                                                      avail.height() - int(24 * self._scale)))
            x = g.x() + (g.width() - stage_w) // 2
            y = avail.y() + avail.height() - stage_h - int(12 * self._scale)
            geometry = QRect(x, y, stage_w, stage_h)
            if geometry != self._stage_geometry:
                self._stage_geometry = geometry
                self.siri.setGeometry(geometry)
            if not self.siri.isVisible() and not self._hidden:
                self.siri.show()
        except Exception as exc:
            print(f"[Controller] ⚠️ layout failed: {exc}")

    # ── input footprint ──────────────────────────────────────────────────────
    def _update_click_through(self, force_interactive: bool = False) -> None:
        """Make the transparent stage click-through unless something is live.

        The stage window is screen-wide so its feathered card shadows cannot be
        clipped; while Siri is dormant or showing only the orb there is nothing
        to click, so the window must pass every mouse event to the desktop
        underneath instead of swallowing it over an invisible band.
        """
        if self.siri is None:
            return
        interactive = bool(force_interactive) or self._pointer_inside
        if self._siri_interactive == interactive:
            return
        self._siri_interactive = interactive
        self.siri.set_click_through(not interactive)

    def _cursor_over_siri_surface(self, position: QPoint) -> bool:
        """Hit-test only visible QML surfaces, never the transparent stage."""
        if self.siri is None or self.siri.rootObject() is None:
            return False

        try:
            raw = self.siri.rootObject().property("interactiveRegions") or []
            if hasattr(raw, "toVariant"):
                raw = raw.toVariant()
            origin = self.siri.geometry().topLeft()

            for region in raw:
                if hasattr(region, "toVariant"):
                    region = region.toVariant()
                if not isinstance(region, dict):
                    continue

                x = origin.x() + float(region.get("x", 0))
                y = origin.y() + float(region.get("y", 0))
                w = max(0.0, float(region.get("width", 0)))
                h = max(0.0, float(region.get("height", 0)))
                if w <= 0 or h <= 0:
                    continue

                if region.get("shape") == "ellipse":
                    dx = (position.x() - (x + w / 2.0)) / (w / 2.0)
                    dy = (position.y() - (y + h / 2.0)) / (h / 2.0)
                    if dx * dx + dy * dy <= 1.0:
                        return True
                elif x <= position.x() < x + w and y <= position.y() < y + h:
                    return True
        except Exception:
            pass
        return False

    def _poll_desktop_input(self) -> None:
        if self.siri is None or self._hidden or not self.siri.isVisible():
            return
        try:
            inside = self._cursor_over_siri_surface(QCursor.pos())
            if inside != self._pointer_inside:
                self._pointer_inside = inside
                self._reschedule_dismissal()
            self._update_click_through()

            # The Siri window deliberately does not accept focus on voice
            # summon, so Escape cannot rely solely on QML Keys handlers.
            escape_down = w32.key_is_down(w32.VK_ESCAPE)
            if escape_down and not self._escape_was_down:
                self.state.on_user_cancelled()
                self.hide()
            self._escape_was_down = escape_down
        except Exception:
            pass

    def on_interaction(self) -> None:
        self._poll_desktop_input()
        self._reschedule_dismissal()

    # ── public window ops (tray / hotkeys / invocation / dismissal) ────────
    def show(self) -> None:
        """Show the Siri stage (invocation). The orb springs into view."""
        was_hidden = self._hidden
        self._hidden = False
        self._cancel_dismissal()
        if was_hidden:
            # The invocation earcon is fired on the rising edge only, so the
            # acoustic cue stays locked to the visual bloom instead of
            # re-triggering every time a state re-asserts visibility.
            try:
                self.audio.play("wake")
            except Exception:
                pass
        if self.siri is not None:
            self.siri.show()
            if not self.siri.set_root_property("invoked", True):
                # QML hasn't loaded yet (rootObject() is None); re-apply once
                # it finishes loading via _on_siri_status_changed.
                self._pending_invoked = True
            self._layout_windows()
            self._update_click_through()
            self._escape_was_down = False
            self._desktop_input.start()
            self._reschedule_dismissal()

    def hide(self) -> None:
        """Dismiss Siri: the orb contracts and fades, then the window hides."""
        self._hidden = True
        self._cancel_dismissal()
        self._pointer_inside = False
        self._update_click_through(force_interactive=False)
        if self.siri is not None:
            self.siri.set_root_property("typing", False)
            self.siri.set_root_property("invoked", False)

    @Slot()
    def windowDismissed(self) -> None:
        """Called by QML once the dismissal animation has finished."""
        if self.siri is None:
            return
        try:
            invoked = bool(self.siri.rootObject().property("invoked"))
        except Exception:
            invoked = False
        if not invoked and self.siri.isVisible():
            if not self._hidden:
                # The user pressed Escape: the dismissal cue belongs here,
                # after the contraction, not on the teardown path.
                self._hidden = True
                try:
                    self.audio.play("done")
                except Exception:
                    pass
            self.siri.hide()
            self._desktop_input.stop()
            self._pointer_inside = False

    def invoke(self) -> None:
        """Explicit invocation (wake word / hotkey / tray / programmatic)."""
        self.show()

    def dismiss(self) -> None:
        """Graceful dismissal back to zero desktop footprint."""
        self.hide()

    def toggle_visibility(self) -> None:
        if self._hidden:
            self.show()
        else:
            self.hide()

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
        if self._pointer_inside:
            return True
        # Do not dissolve while the user is actively speaking.
        if self.bridge.micAmplitude > 0.055:
            return True

        try:
            root = self.anchor.rootObject() if self.anchor is not None else None
            if root is not None and bool(root.property("typing")):
                return True
        except Exception:
            pass
        return False

    def _reading_grace_ms(self) -> int:
        """Scale the quiet reading period with visible response length."""
        characters = len(self.bridge.lastEvent or "")
        try:
            for card in self.bridge.cards.items():
                if card.get("kind") == CardKind.CONFIRMATION.value:
                    continue
                characters += len(str(card.get("title") or ""))
                characters += len(str(card.get("body") or ""))
                characters += len(str(card.get("footer") or ""))
        except Exception:
            pass
        return max(4500, min(14000, 3200 + characters * 38))

    def _reschedule_dismissal(self) -> None:
        self._dismissTimer.stop()
        if self._hidden or self.siri is None or self._busy():
            return
        self._dismissTimer.setInterval(self._reading_grace_ms())
        self._dismissTimer.start()

    def _cancel_dismissal(self) -> None:
        self._dismissTimer.stop()

    def _on_dismiss_due(self) -> None:
        """Grace elapsed: dismiss, unless the user came back first."""
        if not self._busy():
            self.hide()

    def _on_state_for_grace(self, _tr) -> None:
        """State transitions re-arm or cancel the grace immediately.

        ``_on_state`` already schedules the *show*; this listener only keeps
        the dismissal timer honest so leaving SPEAKING arms the grace without
        waiting for the next card/activity change.
        """
        self._reschedule_dismissal()

    @property
    def visible(self) -> bool:
        return not self._hidden

    def open_inspector(self, tab: str = "camera") -> None:
        self.bridge.openInspector(tab)

    def close_inspector(self) -> None:
        self.bridge.closeInspector()

    def focus_input(self) -> None:
        """Open the Type-to-Siri pill and put the caret in its field.

        The Siri window carries ``Qt.WindowDoesNotAcceptFocus`` so voice
        summoning never steals focus. When the user explicitly asks to type we
        only hand keyboard focus to the field item itself: the window stays
        non-activating (no ``requestActivate()``), which is exactly how Apple's
        type-to-Siri behaves over a foreground app.
        """
        self.show()
        try:
            root = self.anchor.rootObject() if self.anchor else None
            if root is not None:
                root.setProperty("typing", True)
                item = root.findChild(QQuickItem, "siriInput")
                if item is not None:
                    item.forceActiveFocus()
        except Exception:
            pass

    def _on_typing_changed(self) -> None:
        self._poll_desktop_input()
        self._reschedule_dismissal()

    # ── theme ────────────────────────────────────────────────────────────────
    def apply_theme(self, name: str) -> None:
        if name not in THEMES:
            return
        self._theme_name = name
        # Re-derive the design scale: the system DPI can change while the app
        # runs (moving the window between monitors, or a display-scale change).
        self._dpi = self._detect_scale()
        self._scale = self._design_scale(self._dpi)
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
        if self.siri is not None:
            self.siri.set_root_property("setupVisible", self._setup_visible)
        if self._setup_visible:
            self.show()
        self._reschedule_dismissal()

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
        # Show the Siri UI whenever Friday enters an active conversational or
        # work state so the orb is visible during the turn, not hidden.
        if tr.current in (UiState.THINKING, UiState.PROCESSING, UiState.SPEAKING):
            self.dispatcher.call(self.show)
        self._reschedule_dismissal()

    # ── shutdown ─────────────────────────────────────────────────────────────
    # ── graceful interrupt ───────────────────────────────────────────────────
    def install_interrupt_handler(self) -> bool:
        """Route Ctrl+C to a clean shutdown instead of a KeyboardInterrupt.

        Qt's event loop is C++ and does not return to the Python interpreter
        between events, so a SIGINT that arrives while QML is running is only
        noticed when some Python frame next executes -- which, in practice, is
        inside :meth:`UiDispatcher.drain`, producing a bare ``KeyboardInterrupt``
        traceback from a timer callback and skipping every piece of teardown.
        That matters here: ducked audio sessions stay at 25% and the tray icon
        keeps a Win32 message loop alive, so the process had to be killed.

        The signal handler runs on the interpreter's main thread as soon as the
        loop yields, so it only sets a flag and posts a queued quit; the actual
        teardown (timers, ducking restore, windows, tray) then happens on the GUI
        thread via the normal shutdown path.

        Safe to call more than once, and safe when not on the main thread / not
        on Windows -- it degrades to ``False`` rather than raising.
        """
        try:
            signal.signal(signal.SIGINT, self._on_sigint)
        except (ValueError, OSError, AttributeError):
            # Not the main thread, or an embedded interpreter without signals.
            return False
        self._interrupt_installed = True
        return True

    def _on_sigint(self, _signum=None, _frame=None) -> None:
        """Signal handler: keep it tiny and never raise.

        Prints the shutdown notice from here so the message is ordered before
        teardown, then defers the real work to the GUI thread.
        """
        if self._interrupted:
            # Second Ctrl+C: the user wants out now, teardown or not.
            print("[FRIDAY] 🛑 Second interrupt — exiting immediately.")
            os._exit(130)
        self._interrupted = True
        print("\n[FRIDAY] 🛑 Ctrl+C — shutting down cleanly...")
        # request_quit() posts to the GUI thread and cannot raise; if the
        # dispatcher is already gone it runs the teardown inline.
        self.request_quit()

    def request_quit(self) -> None:
        """Thread-safe "please exit": post the teardown to the GUI thread."""
        def _quit() -> None:
            print("[FRIDAY] 👋 Tray stopped, audio restored, windows closed.")
            self.shutdown()
            try:
                self.app.quit()
            except Exception:
                pass

        try:
            self.dispatcher.call(_quit)
        except Exception:
            # Dispatcher already torn down or never started: do it inline.
            _quit()

    def shutdown(self) -> None:
        # Idempotent: the SIGINT path, the tray "Quit" item and the event loop's
        # finally-block can all arrive here. Tearing down twice would restore
        # audio that was never ducked and close already-deleted windows.
        if self._shutdown_done:
            return
        self._shutdown_done = True

        for timer in (
            getattr(self, "_tick", None),
            getattr(self, "_slow", None),
            getattr(self, "_drain", None),
            getattr(self, "_desktop_input", None),
            getattr(self, "_dismissTimer", None),
        ):
            if timer is None:
                continue
            try:
                timer.stop()
            except Exception:
                pass
        try:
            self.dispatcher.stop()
        except Exception:
            pass
        self._remember_anchor()
        # Restore any ducked audio session *before* the process can exit; if the
        # machine was interrupted mid-reply this is the only thing that puts
        # other apps' volume back.
        try:
            self.audio.stop()
        except Exception:
            pass
        # The tray owns a Win32 message loop; stop() is the only way it ends.
        try:
            from actions import system_tray

            system_tray.stop()
        except Exception:
            pass
        seen: set[int] = set()
        for win in (self.inspector, self.anchor, self.siri, self.overlay):
            if win is None or id(win) in seen:
                continue
            seen.add(id(win))
            try:
                if hasattr(win, "prepare_close"):
                    win.prepare_close()
                win.hide()
                win.close()
            except Exception:
                pass

    # ── diagnostics ──────────────────────────────────────────────────────────
    def diagnostics(self) -> dict:
        return {
            "theme": self._theme_name,
            "scale": self._scale,
            "dpi": self._dpi,
            "reducedMotion": self.reduced_motion,
            "transparency": self.transparency,
            "dark": self.system_dark,
            "dnd": self._dnd,
            "degraded": self._degraded,
            "windows": {
                "siri": self.siri is not None,
                "anchor": self.anchor is not None,
                "inspector": self.inspector is not None,
            },
            "images": self.images.status(),
            "bus": self.bus.stats(),
            "dispatcher": self.dispatcher.stats(),
            "win32": w32.summary(),
        }


__all__ = ["UiController", "THEMES", "THEME_NAMES", "build_theme", "QML_DIR"]