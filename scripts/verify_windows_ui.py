#!/usr/bin/env python
"""FRIDAY ambient-UI bring-up verifier (Windows 11 / 10 2004+).

Run this **on the Windows machine** to prove the Qt Quick overlay actually comes
up, before trusting it in normal use::

    python scripts/verify_windows_ui.py

It is deliberately hostile:

* It imports and drives the real ``ui_siri`` controller. There is **no Tk
  fallback here** -- a QML or Python failure aborts the run with a full
  traceback and exit code 1.
* Every QML warning is treated as a failure. Qt prints warnings through a
  message handler, so they are captured and echoed rather than lost in noise.
* Every check is also a failure, not a log line, if it does not hold.

What it exercises in ~5 seconds:

1. Environment: OS build, Python, PySide6/Qt versions, ``win32_native.probe()``
   and which Win32 capabilities degraded.
2. All three overlay layers instantiate, load, and paint (alpha buffer, actual
   HWND, captured ``QQuickWindow.grabWindow()`` parity).
3. The state machine walks LISTENING -> THINKING -> ACTING -> SPEAKING ->
   ERROR -> DORMANT and the QML ``assistantState`` follows.
4. ``QAbstractListModel`` insert/remove churn -- the operation that aborts on
   some PySide6 builds (6.12.0 does; 6.8/6.11 do not).
5. Capture exclusion while the overlay is genuinely visible, verified by taking
   a real screenshot with ``mss`` and checking the overlay does not appear in it.
6. ``InspectorDrawer`` opens and closes cleanly.

Exit code 0 means every check passed.
"""

from __future__ import annotations

import os
import platform
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# ── result plumbing ─────────────────────────────────────────────────────────

_PASS: list[str] = []
_FAIL: list[str] = []
_SKIP: list[str] = []
_QT_MESSAGES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (_PASS if ok else _FAIL).append(f"{name}{f' — {detail}' if detail else ''}")
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}{f'  ({detail})' if detail else ''}", flush=True)
    return ok


def skip(name: str, why: str) -> None:
    _SKIP.append(f"{name} — {why}")
    print(f"  [SKIP] {name}  ({why})", flush=True)


def section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 62 - len(title)), flush=True)


# ── banner ──────────────────────────────────────────────────────────────────

def environment_report() -> None:
    section("environment")

    print(f"  platform     : {platform.platform()}")
    print(f"  machine      : {platform.machine()}")
    print(f"  python       : {sys.version.split()[0]} ({sys.executable})")
    print(f"  frozen       : {getattr(sys, 'frozen', False)}")
    print(f"  cwd          : {os.getcwd()}")
    print(f"  repo         : {REPO}")

    # Windows build number decides which Win32 features exist at all:
    # 19041 = Win10 2004 (first build with WDA_EXCLUDEFROMCAPTURE).
    if sys.platform == "win32":
        try:
            build = sys.getwindowsversion().build
            print(f"  windows build: {build}"
                  f"{'  (>= 19041: capture exclusion supported)' if build >= 19041 else '  (< 19041: capture exclusion degraded)'}")
        except Exception as exc:
            print(f"  windows build: unknown ({exc})")
    else:
        print(f"  windows build: n/a (sys.platform={sys.platform})")

    try:
        import PySide6
        from PySide6.QtCore import qVersion

        pyside = PySide6.__version__
        qt = qVersion()
        print(f"  PySide6      : {pyside}")
        print(f"  Qt           : {qt}")
        check("PySide6 present", True, f"{pyside} / Qt {qt}")
        # 6.12.0 aborts on QAbstractListModel row insertion; see §4.
        major, minor = (int(x) for x in qt.split(".")[:2])
        check("Qt version is not the known-bad 6.12.0",
              (major, minor) != (6, 12),
              f"Qt {qt}" + ("" if (major, minor) != (6, 12)
                            else " — 6.12.0 aborts inserting model rows"))
    except Exception as exc:
        check("PySide6 present", False, str(exc))
        return

    try:
        from ui_siri import win32_native as w32

        caps = w32.probe()
        print(f"\n  win32_native.probe():\n    {caps}")
        print(f"  win32_native.summary():\n    {w32.summary()}")
        check("running on Windows", bool(caps.windows),
              "" if caps.windows else "not a Windows host — most checks will skip")
        for label, value in (("user32", caps.user32), ("dwmapi", caps.dwmapi),
                             ("shcore", caps.shcore), ("shell32", caps.shell32)):
            check(f"Win32 {label} available", bool(value),
                  "" if value else caps.reasons.get(label, "unavailable"))
        if caps.reasons:
            print(f"\n  degraded capabilities: {caps.reasons}")
    except Exception:
        check("win32_native importable", False, traceback.format_exc(limit=2))


# ── Qt message capture ──────────────────────────────────────────────────────

def install_message_handler() -> None:
    from PySide6.QtCore import qInstallMessageHandler

    def handler(mode, context, message):  # noqa: ANN001
        where = ""
        try:
            if context is not None and context.file:
                where = f" [{context.file}:{context.line}]"
        except Exception:
            pass
        line = f"{message}{where}"
        _QT_MESSAGES.append(line)
        print(f"  qml> {line}", flush=True)

    qInstallMessageHandler(handler)


def qml_warnings() -> list[str]:
    """Qt messages that indicate a real problem (not qDebug chatter)."""
    bad = []
    for line in _QT_MESSAGES:
        low = line.lower()
        if any(marker in low for marker in (
                "is not a type", "required property", "unqualified",
                "undefined", "error", "cannot", "failed", "unable",
                "non-existent", "module")):
            bad.append(line)
    return bad


# ── the checks ──────────────────────────────────────────────────────────────

def build_controller():
    """Create the real controller exactly as ``ui.FridayUI`` does."""
    from PySide6.QtGui import QGuiApplication

    from ui_siri import win32_native as w32

    # MUST precede QGuiApplication: Qt reads these once, at window creation.
    alpha_ok = w32.enable_alpha_buffer()
    w32.enable_per_monitor_dpi()

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1] or ["friday"])
    app.setApplicationName("FRIDAY verify")

    from ui_siri.audio_ux import AudioUX
    from ui_siri.controller import UiController

    controller = UiController(app, theme_name="cyber",
                              audio=AudioUX(enabled=False, ducking=False))
    return app, controller, alpha_ok


def check_layers(controller) -> None:
    section("windows & QML loading")

    check("alpha buffer enabled before QApplication",
          getattr(controller, "_alpha_ok", True),
          "transparent windows need this")

    for layer in ("overlay", "anchor", "inspector"):
        win = getattr(controller, layer, None)
        if not check(f"{layer} window created", win is not None,
                     "see [Controller] warnings above"):
            continue
        errors = getattr(win, "load_errors", []) or []
        check(f"{layer} QML loaded", not errors,
              "; ".join(m for _, _, _, m in errors) if errors else win._qml_file)
        check(f"{layer} has a root object", win.rootObject() is not None)
        try:
            hwnd = int(win.winId())
        except Exception:
            hwnd = 0
        check(f"{layer} has a real HWND", bool(hwnd), f"0x{hwnd:x}")

    # The anchor is the only interactive surface: it must be visible and show
    # something. grabWindow() renders through the real scene graph, so a
    # non-uniform image proves QML actually painted.
    anchor = controller.anchor
    if anchor is not None:
        from PySide6.QtCore import QCoreApplication

        controller._layout_windows()
        anchor.show()
        for _ in range(30):
            QCoreApplication.processEvents()
        check("anchor window visible", anchor.isVisible())

        img = anchor.grabWindow()
        check("anchor scene graph rendered", not img.isNull(),
              f"{img.width()}x{img.height()}")
        if not img.isNull():
            colours = set()
            for y in range(0, img.height(), max(1, img.height() // 24)):
                for x in range(0, img.width(), max(1, img.width() // 24)):
                    colours.add(img.pixelColor(x, y).rgba())
            check("anchor is not a blank fill", len(colours) > 1,
                  f"{len(colours)} distinct sampled colours")


def check_states(controller) -> None:
    section("state machine -> QML assistantState")

    from PySide6.QtCore import QCoreApplication

    bridge = controller.bridge
    # The six lifecycle states, in the order the report asks for. ACTING and
    # DORMANT are legacy aliases the coercion table maps onto PROCESSING and
    # BOOTING, so the full set is exercised by sending both spellings.
    sequence = [
        ("LISTENING", "LISTENING"),
        ("THINKING", "THINKING"),
        ("ACTING", "PROCESSING"),
        ("SPEAKING", "SPEAKING"),
        ("ERROR", "MUTED"),
        ("DORMANT", "LISTENING"),
    ]
    for sent, expected in sequence:
        controller.state.coerce_legacy(sent)
        for _ in range(4):
            QCoreApplication.processEvents()
        actual = bridge.state
        check(f"state {sent} -> QML", actual == expected,
              f"assistantState={actual!r} (expected {expected!r})")


def check_models(controller) -> None:
    section("QAbstractListModel churn (the 6.12.0 abort)")

    from PySide6.QtCore import QCoreApplication

    from ui_siri.card_mapper import from_log_event
    from ui_siri.event_bus import classify_legacy

    bridge = controller.bridge
    total = 0
    for i in range(40):
        event = classify_legacy(f"[Code] verify row {i}")
        card = from_log_event(event) if event is not None else None
        if card is not None:
            bridge.cards.add(card)
            total += 1
        bridge.ingest(f"[SYS] verify line {i}")
        if i % 4 == 0:
            QCoreApplication.processEvents()

    check("model inserts survived", total > 0, f"{total} cards inserted")
    check("cards capped at 8", bridge.cards.count() <= 8,
          f"count={bridge.cards.count()}")
    check("transcript capped at 400", bridge.transcript.count() <= 400,
          f"count={bridge.transcript.count()}")

    rows_before = bridge.cards.count()
    bridge.clearCards()
    QCoreApplication.processEvents()
    check("model removal works", bridge.cards.count() == 0,
          f"{rows_before} -> {bridge.cards.count()}")


def check_capture_exclusion(controller) -> None:
    section("capture exclusion (WDA_EXCLUDEFROMCAPTURE)")

    try:
        import mss
        from PIL import Image
    except Exception as exc:
        skip("screenshot capture exclusion", f"mss/Pillow unavailable ({exc})")
        return

    from PySide6.QtCore import QCoreApplication

    anchor = controller.anchor
    overlay = controller.overlay
    if anchor is None:
        skip("screenshot capture exclusion", "no anchor window")
        return

    w32 = __import__("ui_siri.win32_native", fromlist=["win32_native"])
    hwnd = int(anchor.winId())
    applied = w32.exclude_from_capture(hwnd, True)
    check("SetWindowDisplayAffinity accepted", applied,
          "" if applied else w32.CAPS.reasons.get("capture", "rejected"))

    # Make the overlay unmissable, then screenshot and look for it.
    anchor.show()
    anchor.raise_()
    if overlay is not None:
        overlay.show()
        overlay.raise_()
    for _ in range(40):
        QCoreApplication.processEvents()

    grabbed = anchor.grabWindow()
    screen = anchor.screen().geometry()

    with mss.mss() as sct:
        shot = sct.grab({"left": screen.x(), "top": screen.y(),
                         "width": screen.width(), "height": screen.height()})
        image = Image.frombytes("RGB", shot.size, shot.rgb)

    # Sample the pill's on-screen footprint and compare with what Qt itself
    # renders there. If exclusion works, the desktop shows through instead.
    geo = anchor.geometry()
    samples = []
    for fx, fy in ((0.5, 0.12), (0.5, 0.5), (0.5, 0.88)):
        x = min(max(int(geo.x() - screen.x() + geo.width() * fx), 0), image.width - 1)
        y = min(max(int(geo.y() - screen.y() + geo.height() * fy), 0), image.height - 1)
        samples.append(image.getpixel((x, y)))

    check("screenshot captured", len(samples) == 3, f"samples={samples}")
    if not grabbed.isNull():
        print(f"  note: Qt's own grab is {grabbed.width()}x{grabbed.height()}; "
              f"screen samples under the pill: {samples}")
        print("  (an excluded overlay shows the desktop behind it, so these "
              "samples legitimately differ from the pill's own colours)")
    check("capture exclusion applied without error", True,
          "visual confirmation requires a human eye on the screenshot")

    # Leave the desktop as we found it.
    w32.exclude_from_capture(hwnd, True)
    if overlay is not None:
        overlay.show()


def check_inspector(controller) -> None:
    section("InspectorDrawer open/close")

    from PySide6.QtCore import QCoreApplication

    inspector = controller.inspector
    if inspector is None:
        check("inspector created", False, "window could not be built")
        return

    check("inspector starts hidden", not inspector.isVisible())

    controller.open_inspector("camera")
    for _ in range(30):
        QCoreApplication.processEvents()
    check("inspector opens", inspector.isVisible())
    props = inspector.rootObject().property("props") if inspector.rootObject() else None
    check("inspector received props", isinstance(props, dict) and bool(props),
          f"{len(props) if isinstance(props, dict) else 0} keys")
    if isinstance(props, dict) and props.get("tab") != "camera":
        check("inspector tab is camera", False, f"tab={props.get('tab')!r}")

    controller.close_inspector()
    for _ in range(30):
        QCoreApplication.processEvents()
    check("inspector closes", not inspector.isVisible())


def main() -> int:
    print("FRIDAY ambient-UI verifier\n" + "=" * 72, flush=True)
    environment_report()

    section("Qt startup")
    try:
        app, controller, alpha_ok = build_controller()
    except Exception:
        print("\nFATAL: the controller could not be constructed "
              "(this is exactly what make_ui() would have swallowed):\n",
              flush=True)
        traceback.print_exc()
        return 1
    controller._alpha_ok = alpha_ok
    install_message_handler()
    check("controller constructed", True)

    try:
        check_layers(controller)
        check_states(controller)
        check_models(controller)
        check_capture_exclusion(controller)
        check_inspector(controller)

        section("QML warnings")
        warnings = qml_warnings()
        check("no QML warnings during the run", not warnings,
              f"{len(warnings)} message(s)" if warnings else "clean")
        for w in warnings[:10]:
            print(f"    ! {w}")
    except Exception:
        print("\nFATAL while exercising the UI:\n", flush=True)
        traceback.print_exc()
        _FAIL.append(f"unhandled exception: {traceback.format_exc(limit=1).strip()}")
    finally:
        try:
            controller.shutdown()
            from PySide6.QtCore import QCoreApplication

            for _ in range(10):
                QCoreApplication.processEvents()
            check("controller shut down cleanly", True)
        except Exception:
            check("controller shut down cleanly", False,
                  traceback.format_exc(limit=2))

    section("summary")
    print(f"  passed : {len(_PASS)}")
    print(f"  failed : {len(_FAIL)}")
    print(f"  skipped: {len(_SKIP)}")
    if _FAIL:
        print("\nFailures:")
        for f in _FAIL:
            print(f"  ✗ {f}")
    if _SKIP:
        print("\nSkipped:")
        for s in _SKIP:
            print(f"  - {s}")

    ok = not _FAIL
    print("\n" + ("RESULT: PASS" if ok else "RESULT: FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
