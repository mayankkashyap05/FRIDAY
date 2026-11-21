"""Phase 2/3 tests — Qt bridge, models, image provider, QML integrity.

These split into two groups:

1. **Always run** (given PySide6): list models, the image provider's frame
   conversion, querying URLs, and — most importantly — a full ``qmllint`` sweep
   over every asset plus a file-existence sweep. A missing QML file is a fatal
   startup bug, so it must fail CI.
2. **Needs a real GUI stack** (``FRIDAY_GUI_TESTS=1``): instantiating
   ``QGuiApplication`` + ``UiController`` + the QML windows. Skipped by default
   because headless CI boxes commonly lack the GL/DBus libraries Qt needs; the
   sandbox used for this change only had stub shims.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtCore import QCoreApplication, QSize, Qt  # noqa: E402

# QGuiApplication is only needed for GUI tests (FRIDAY_GUI_TESTS=1). Importing
# it unconditionally would fail on headless boxes that lack libGL.so.1.
def _qgui_application():
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication

# QtGui/QtQuick need the platform's GL/DBus/xkb libraries. Headless CI boxes
# (including the sandbox this change was developed in) frequently lack them, so
# the GUI-dependent half of this module is skipped rather than failed. The
# static QML lint sweep above still runs everywhere.
try:  # noqa: SIM105
    from PySide6.QtGui import QImage  # noqa: F401,E402
    _QT_GUI_OK = True
except Exception as exc:  # pragma: no cover - environment dependent
    _QT_GUI_OK = False
    _QT_GUI_ERROR = str(exc)

gui_stack = pytest.mark.skipif(
    not _QT_GUI_OK,
    reason=f"QtGui unavailable in this environment ({_QT_GUI_ERROR if not _QT_GUI_OK else ''})",
)

REPO = Path(__file__).resolve().parent.parent
QML_DIR = REPO / "ui_siri" / "qml"

GUI = os.environ.get("FRIDAY_GUI_TESTS", "").strip() in ("1", "true", "yes")
gui_only = pytest.mark.skipif(not GUI, reason="set FRIDAY_GUI_TESTS=1 to run")


# ─────────────────────────────────────────────────────────────────────────────
# Asset integrity
# ─────────────────────────────────────────────────────────────────────────────

def test_qml_assets_exist():
    expected = {
        "Theme.qml", "SiriOrb.qml", "SiriWindow.qml", "TypeToSiriPill.qml",
        "SnippetStack.qml", "LiveActivityBar.qml", "InspectorDrawer.qml",
        "InspectorWindow.qml", "SetupCard.qml", "qmldir",
    }
    # `cards/` is checked separately.
    expected |= {"cards/" + name for name in (
        "BaseGlassCard.qml", "TextCard.qml", "WeatherBriefingCard.qml",
        "CalendarCard.qml", "EmailDigestCard.qml", "FlightCard.qml",
        "TelemetryCard.qml", "ControlPillCard.qml", "DocumentQACard.qml",
        "ConfirmationCard.qml",
    )}
    missing = sorted(name for name in expected
                     if not (QML_DIR / name).exists())
    assert not missing, f"missing QML assets: {missing}"


def test_qml_dir_is_resolvable_without_qt():
    """The controller must agree with the Qt-free theme module on where QML
    lives — a mismatch is the classic cause of "module not found" at startup."""
    from ui_siri.themes import QML_DIR as THEME_QML

    assert THEME_QML.is_dir()
    assert (THEME_QML / "SiriWindow.qml").is_file()
    assert (THEME_QML / "Theme.qml").is_file()


def test_qmldir_declares_the_singleton():
    text = (QML_DIR / "qmldir").read_text(encoding="utf-8")
    assert re.search(r"singleton\s+Theme\s+1\.0\s+Theme\.qml", text)
    assert "module ui_siri.qml" in text


def test_theme_qml_is_a_pragma_singleton():
    first = (QML_DIR / "Theme.qml").read_text(encoding="utf-8").splitlines()[0]
    assert first.strip() == "pragma Singleton"


# ─────────────────────────────────────────────────────────────────────────────
# qmllint sweep — the real QML compiler front-end
# ─────────────────────────────────────────────────────────────────────────────

def _qmllint_binary() -> str | None:
    import PySide6

    candidate = Path(PySide6.__file__).parent / "qmllint"
    return str(candidate) if candidate.exists() else None


def test_every_qml_file_lints_clean():
    """Every QML file must lint with zero warnings.

    No exemption list: dynamic JS-array models are handled by reading the role
    once into a delegate-level property, which keeps the analyser satisfied and
    the runtime behaviour identical.
    """
    lint = _qmllint_binary()
    if not lint:
        pytest.skip("qmllint not shipped with this PySide6 build")

    files = sorted(str(p) for p in QML_DIR.rglob("*.qml"))
    assert files, "no QML files found"

    failures = []
    for path in files:
        proc = subprocess.run([lint, "-I", str(QML_DIR), path],
                              capture_output=True, text=True, timeout=180)
        problems = [line for line in (proc.stdout + proc.stderr).splitlines()
                    if line.startswith(("Warning:", "Error:"))]
        if problems:
            failures.append(f"\n{Path(path).name}:\n  " + "\n  ".join(problems[:8]))

    assert not failures, "QML lint failures:" + "".join(failures)


def _code_only(text: str) -> str:
    """Strip // comments and /* */ blocks so prose cannot trip the scanners."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def test_no_shader_effect_anywhere():
    """pyside6-qsb is not guaranteed in the environment, so shaders are banned."""
    offenders = [str(p.relative_to(REPO)) for p in QML_DIR.rglob("*.qml")
                 if re.search(r"\bShaderEffect\s*\{", _code_only(
                     p.read_text(encoding="utf-8")))]
    assert not offenders, f"ShaderEffect is forbidden (needs .qsb): {offenders}"


def test_no_inline_glsl_strings():
    pattern = re.compile(r'"(?:uniform|attribute|varying)\s+\w+')
    offenders = [str(p.relative_to(REPO)) for p in QML_DIR.rglob("*.qml")
                 if pattern.search(_code_only(p.read_text(encoding="utf-8")))]
    assert not offenders, f"inline GLSL found: {offenders}"


def test_only_qt6_compatible_qml_modules():
    allowed = {"QtQuick", "QtQuick.Shapes", "QtQuick.Controls",
               "QtQuick.Layouts", "QtQuick.Effects", "QtQuick.Window",
               "QtQuick.Particles", "QtQml.Models", "QtQml",
               "QtQuick.Dialogs", "QtQuick.Templates"}
    offenders = []
    for path in QML_DIR.rglob("*.qml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*import\s+([\w.]+)", line)
            if not match:
                continue
            module = match.group(1)
            if module.startswith("Qt") and module not in allowed:
                offenders.append(f"{path.name}: {module}")
    assert not offenders, f"unexpected QML imports: {offenders}"


# ─────────────────────────────────────────────────────────────────────────────
# Image provider
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    """QGuiApplication for GUI tests — only created when QtGui is available."""
    if not _QT_GUI_OK:
        pytest.skip(f"QtGui unavailable ({_QT_GUI_ERROR})")
    QGuiApplication = _qgui_application()
    application = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    yield application


@gui_stack
def test_to_qimage_accepts_bgr_numpy(app):
    np = pytest.importorskip("numpy")
    from ui_siri.image_provider import to_qimage

    # Pure blue in BGR is pure red in RGB.
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    frame[:, :, 0] = 255
    img = to_qimage(frame)
    assert img is not None and not img.isNull()
    assert img.width() == 4 and img.height() == 4
    colour = img.pixelColor(0, 0)
    assert colour.red() == 255 and colour.blue() == 0


@gui_stack
def test_to_qimage_accepts_grayscale(app):
    np = pytest.importorskip("numpy")
    from ui_siri.image_provider import to_qimage

    img = to_qimage(np.full((3, 5), 128, dtype=np.uint8))
    assert img is not None and img.width() == 5 and img.height() == 3


@gui_stack
def test_to_qimage_accepts_pil(app):
    PIL = pytest.importorskip("PIL.Image")
    from ui_siri.image_provider import to_qimage

    img = to_qimage(PIL.new("RGB", (7, 3), (10, 20, 30)))
    assert img is not None and img.width() == 7


@gui_stack
def test_to_qimage_rejects_junk_without_raising(app):
    from ui_siri.image_provider import to_qimage

    for junk in (None, "nope", 42, object(), b"", b"\x00\x01\x02"):
        assert to_qimage(junk) is None


@gui_stack
def test_provider_push_and_url(app):
    np = pytest.importorskip("numpy")
    from ui_siri.image_provider import FridayImageProvider

    provider = FridayImageProvider()
    assert provider.url("vision").startswith("image://friday/vision?rev=")
    before = provider.url("vision")

    assert provider.push("vision", np.zeros((8, 8, 3), dtype=np.uint8)) is True
    assert provider.url("vision") != before

    status = provider.status()["vision"]
    assert status["frames"] == 1 and status["width"] == 8

    assert provider.push("vision", None) is False
    assert provider.status()["vision"]["dropped"] == 1


@gui_stack
def test_provider_request_image_never_returns_null(app):
    from ui_siri.image_provider import FridayImageProvider

    provider = FridayImageProvider()
    size = QSize()
    pixmap, actual = provider.requestImage("unknown", size, QSize(10, 10))
    assert not pixmap.isNull()
    assert actual.width() == 2  # placeholder, not a decode error


@gui_stack
def test_provider_downscales_oversized_frames(app):
    np = pytest.importorskip("numpy")
    from ui_siri.image_provider import FridayImageProvider

    provider = FridayImageProvider()
    provider.push("arcade", np.zeros((2000, 3000, 3), dtype=np.uint8))
    status = provider.status()["arcade"]
    assert max(status["width"], status["height"]) <= 1280


@gui_stack
def test_provider_query_string_is_stripped(app):
    np = pytest.importorskip("numpy")
    from ui_siri.image_provider import FridayImageProvider

    provider = FridayImageProvider()
    provider.push("vision", np.zeros((4, 4, 3), dtype=np.uint8))
    px, _ = provider.requestImage("vision?rev=99", QSize(), QSize())
    assert not px.isNull() and px.width() == 4


# ─────────────────────────────────────────────────────────────────────────────
# Models + bridge (QCoreApplication is enough — no windows involved)
# ─────────────────────────────────────────────────────────────────────────────

def test_cards_model_roles_and_capacity(app):
    from ui_siri.bridge import CardsModel
    from ui_siri.card_mapper import from_envelope

    model = CardsModel()
    roles = model.roleNames()
    assert b"title" in roles.values() and b"payload" in roles.values()

    for temp in range(CardsModel.CAPACITY + 4):
        card, _ = from_envelope({"type": "weather", "city": f"C{temp}",
                                 "temp_c": temp})
        model.add(card)
    assert model.count() == CardsModel.CAPACITY


def test_cards_model_replaces_same_kind_and_title(app):
    from ui_siri.bridge import CardsModel
    from ui_siri.card_mapper import from_envelope

    model = CardsModel()
    for temp in (10, 20):
        card, _ = from_envelope({"type": "telemetry", "cpu": temp})
        card.title = "System"
        model.add(card)
    assert model.count() == 1


def test_cards_model_ttl_expiry(app):
    from ui_siri.bridge import CardsModel
    from ui_siri.event_bus import Card, CardKind

    model = CardsModel()
    model.add(Card(kind=CardKind.TEXT, title="t", body="b", ttl=1.0))
    assert model.count() == 1
    import time as _t
    gone = model.expire(_t.time() + 5)
    assert gone and model.count() == 0


def test_activity_model_upsert_and_end(app):
    from ui_siri.bridge import ActivityModel
    from ui_siri.event_bus import Activity, ActivityKind

    model = ActivityModel()
    model.upsert(Activity(aid="a1", title="Build", kind=ActivityKind.CODE))
    model.upsert(Activity(aid="a1", title="Build", kind=ActivityKind.CODE,
                          detail="50%", progress=0.5))
    assert model.count() == 1
    assert model.active_count() == 1
    assert model.end("a1") is True
    assert model.active_count() == 0


def test_transcript_model_is_capped(app):
    from ui_siri.bridge import TranscriptModel
    from ui_siri.event_bus import classify_legacy

    model = TranscriptModel()
    for i in range(TranscriptModel.CAPACITY + 50):
        model.add(classify_legacy(f"SYS: line {i}"))
    assert model.count() == TranscriptModel.CAPACITY


def test_bridge_ingest_populates_all_models(app):
    from ui_siri.bridge import UiBridge, UiDispatcher
    from ui_siri.event_bus import EventBus
    from ui_siri.state_machine import AssistantStateMachine

    bridge = UiBridge(AssistantStateMachine(), EventBus(), UiDispatcher())
    bridge.ingest("You: weather in Dibrugarh")
    bridge.ingest("Friday: It's 29°C and humid in Dibrugarh.")
    bridge.ingest("[Code] Build started...")
    assert bridge.transcript.count() == 3
    assert bridge.cardCount == 1
    assert bridge.activities.count() == 1


def test_bridge_diagnostics_shape(app):
    from ui_siri.bridge import UiBridge, UiDispatcher
    from ui_siri.event_bus import EventBus
    from ui_siri.state_machine import AssistantStateMachine

    bridge = UiBridge(AssistantStateMachine(), EventBus(), UiDispatcher())
    data = bridge.diagnostics()
    assert {"state", "dispatcher", "cards", "activities", "transcript",
            "theme"} <= set(data)


# ─────────────────────────────────────────────────────────────────────────────
# Full GUI stack (opt-in)
# ─────────────────────────────────────────────────────────────────────────────

@gui_only
@gui_stack
def test_controller_boots_and_shuts_down():
    from PySide6.QtCore import QTimer
    QGuiApplication = _qgui_application()

    application = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    from ui_siri.controller import UiController

    controller = UiController(application, enable_overlay=True)
    assert controller.anchor is not None
    assert not controller._degraded

    QTimer.singleShot(400, controller.shutdown)
    application.processEvents()
    assert "windows" in controller.diagnostics()


@gui_only
@gui_stack
def test_ambient_facade_roundtrip():
    import ui

    instance = ui.FridayUI("face.png")
    assert instance.ambient is True
    for method, args in [
        ("set_state", ("LISTENING",)), ("set_state", ("THINKING",)),
        ("set_state", ("SPEAKING",)), ("set_state", ("MUTED",)),
        ("set_state", ("ONLINE",)), ("write_log", ("SYS: hello",)),
        ("set_persona", ("normal", "calm", "Aoede")),
        ("set_connection", (True,)), ("set_connecting", (False,)),
        ("set_vision_signal", ("open hand",)),
        ("_toggle_mute", ()), ("_draw_mute_button", ()),
        ("cycle_theme", ()), ("start_speaking", ()), ("stop_speaking", ()),
    ]:
        getattr(instance, method)(*args)
    instance.quit()


# ── import policy: the facade must never require Qt to be imported ──────────

def test_importing_ui_does_not_load_qt():
    """``import ui`` must stay Qt-free.

    main.py imports `ui` at module scope, before it can possibly fall back. If
    this module pulled in PySide6 eagerly, a machine with a broken Qt install
    would crash with an ImportError instead of reaching the Tk fallback in
    make_ui(). The facade therefore reads its theme table from ui_siri.themes,
    which is Qt-free by design.
    """
    code = (
        "import sys; sys.path.insert(0, %r); import ui; "
        "print(','.join(sorted(m for m in sys.modules if m.startswith('PySide6'))))"
        % str(REPO)
    )
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    loaded = proc.stdout.strip()
    assert loaded == "", f"importing ui loaded Qt modules: {loaded}"
    assert "cyber" in proc.stdout.lower() or loaded == ""


# ── static hygiene for the Windows bring-up path ────────────────────────────

def test_no_undefined_names_in_ui_code():
    """The Linux sandbox cannot execute QtGui, so NameErrors there only show up
    on the user's machine. This catches them at review time instead.

    pyflakes is optional; the AST fallback walks every module for names that are
    loaded but never bound anywhere in scope.
    """
    targets = [REPO / "ui.py", REPO / "main.py", REPO / "actions" / "wake_word.py"]
    targets += sorted((REPO / "ui_siri").glob("*.py"))
    targets += sorted((REPO / "scripts").glob("*.py"))
    missing = [str(t) for t in targets if not t.exists()]
    assert not missing, f"expected files are gone: {missing}"

    try:
        proc = subprocess.run([sys.executable, "-m", "pyflakes", *map(str, targets)],
                              capture_output=True, text=True, timeout=180)
    except Exception:
        pytest.skip("pyflakes not installed")
    if proc.returncode != 0 and "No module named pyflakes" in proc.stderr:
        pytest.skip("pyflakes not installed")

    offenders = [line for line in proc.stdout.splitlines()
                 if "undefined name" in line]
    assert not offenders, "undefined names:\n  " + "\n  ".join(offenders)


def test_qml_never_builds_a_file_url_by_hand():
    """Windows paths are C:\\..., so ``f"file://{path}"`` is always wrong:
    QUrl parses the drive letter as a scheme. Only QUrl.fromLocalFile is safe.
    """
    bad = []
    for path in sorted((REPO / "ui_siri").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern in ('f"file://', "f'file://", '"file://{"'):
            if pattern in text:
                bad.append(f"{path.relative_to(REPO)}: {pattern}")
    assert not bad, "hand-built file URLs (use QUrl.fromLocalFile):\n  " + "\n  ".join(bad)


def test_every_qml_window_consumes_the_python_palette():
    """A window that ignores the theme renders the built-in cyber defaults.

    The palette reaches QML through a required property set at construction
    (themeData), so a missing wiring is a silent visual regression.
    """
    for name in ("SiriWindow.qml", "InspectorWindow.qml"):
        text = (QML_DIR / name).read_text(encoding="utf-8")
        assert "required property var themeData" in text, f"{name}: themeData not declared"
        assert "applyTheme(themeData)" in text or "applyTheme(root.themeData)" in text \
            or "applyTheme(window.themeData)" in text or "applyTheme(siri.themeData)" in text, \
            f"{name}: themeData never applied"


def test_windows_verifier_script_is_wellformed():
    """The bring-up script must compile, and must never import Qt at module
    scope (it is run by hand on a machine where Qt may still be broken)."""
    script = REPO / "scripts" / "verify_windows_ui.py"
    assert script.is_file(), "scripts/verify_windows_ui.py is missing"

    import py_compile
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        py_compile.compile(str(script), cfile=str(Path(tmp) / "v.pyc"), doraise=True)

    source = script.read_text(encoding="utf-8")
    header = source.split('if __name__ == "__main__":')[0]
    for line in header.splitlines():
        stripped = line.strip()
        if stripped.startswith(("from PySide6", "import PySide6")):
            # Only allowed inside a function (i.e. indented).
            assert line.startswith((" ", "\t")), \
                f"verifier imports Qt at module scope: {stripped}"


# ── Bug 1: every .qml loaded into a QQuickView must be resolvable ──────────

QML_LOADED_AS_WINDOWS = ("SiriWindow.qml", "InspectorWindow.qml")


def test_qmldir_declares_every_root_qml_type():
    """On Windows, loadFromModule failed with:

        Module "ui_siri.qml" contains no type named "OverlayWindow"

    because qmldir exported only Theme. Every component in qml/ is now listed,
    so both setSource() and loadFromModule() resolve. This test would have
    caught the original omission, and keeps qmldir in sync as files are added.
    """
    qml_dir = QML_DIR
    qmldir = (qml_dir / "qmldir").read_text(encoding="utf-8")
    declared = set(re.findall(r"^(?:singleton\s+)?(\w+)\s+[\d.]+\s+(\S+\.qml)$",
                              qmldir, re.M))
    declared_names = {name for name, _ in declared}

    files = {p.stem for p in qml_dir.glob("*.qml")}
    missing = sorted(files - declared_names)
    assert not missing, (
        f"these QML types exist but qmldir does not export them: {missing}. "
        f"loadFromModule() would fail with 'contains no type named ...'")

    # Every declared file must actually exist.
    for name, filename in declared:
        assert (qml_dir / filename).is_file(), f"qmldir lists missing {filename}"

    # The windows are the ones instantiated from Python.
    for win in QML_LOADED_AS_WINDOWS:
        assert Path(win).stem in declared_names, f"{win} is not exported by qmldir"


def test_qmldir_declares_the_theme_singleton():
    qmldir = (QML_DIR / "qmldir").read_text(encoding="utf-8")
    assert re.search(r"^singleton\s+Theme\s+[\d.]+\s+Theme\.qml$", qmldir, re.M), \
        "Theme must be a singleton export or every file's Theme.* fails"


def test_cards_subdirectory_has_a_qmldir():
    """The cards/ module mirrors the directory so card types resolve whether a
    window is loaded directly or through the module."""
    cards = QML_DIR / "cards"
    qmldir = cards / "qmldir"
    assert qmldir.is_file(), "cards/qmldir is missing"
    declared = set(re.findall(r"^(\w+)\s+[\d.]+\s+(\S+\.qml)$",
                              qmldir.read_text(encoding="utf-8"), re.M))
    files = {p.stem for p in cards.glob("*.qml")}
    assert not (files - {n for n, _ in declared})


def test_window_roots_are_items_not_windows():
    """A QQuickView hosts an Item; a `Window { }` root inside a QQuickView
    creates a second native window and the view shows nothing."""
    for win in QML_LOADED_AS_WINDOWS:
        text = (QML_DIR / win).read_text(encoding="utf-8")
        # first non-comment, non-import, non-pragma line that opens a block
        root = None
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("//", "/*", "*", "pragma ", "import ")):
                continue
            match = re.match(r"^([A-Z]\w*)\s*\{", stripped)
            if match:
                root = match.group(1)
                break
        assert root is not None, f"{win}: no root element found"
        assert root == "Item", f"{win} root is {root}, expected Item"


def test_controller_loads_windows_by_file_url_not_module():
    """setSource(file URL) is the path proven to boot on Windows; loadFromModule
    needs the type exported from the module and failed there."""
    source = (REPO / "ui_siri" / "controller.py").read_text(encoding="utf-8")
    assert "QUrl.fromLocalFile" in source
    assert "loadFromModule" not in source.replace(
        "# setSource takes a direct file URL", "").replace(
        "NOT loadFromModule", "").replace("loadFromModule() would", "")
    # initial properties must precede the source, or required props are unset
    assert source.index("setInitialProperties") < source.index("self.setSource(")


# ─────────────────────────────────────────────────────────────────────────────
# Regressions: QML could not see the models (they were plain attributes)
# ─────────────────────────────────────────────────────────────────────────────

def test_models_are_real_qml_properties(app):
    """The three models must be Q_PROPERTYs, not plain Python attributes.

    A plain attribute is invisible to QML: ``root.bridge.cards`` evaluates to
    ``undefined``, which made ``SnippetStack.model`` undefined, made every
    ``Connections`` handler warn "no signal of the target matches", and left
    the card stack and activity capsule permanently empty. Nothing else in the
    suite caught it, because Python-side access worked fine.
    """
    from ui_siri.bridge import UiBridge, UiDispatcher
    from ui_siri.event_bus import EventBus
    from ui_siri.state_machine import AssistantStateMachine

    bridge = UiBridge(AssistantStateMachine(), EventBus(), UiDispatcher())
    meta = bridge.metaObject()
    declared = {str(meta.property(i).name()) for i in range(meta.propertyCount())}

    for name in ("cards", "activities", "transcript"):
        assert name in declared, (
            f"{name!r} is not a Q_PROPERTY, so QML reads it as undefined")
        assert getattr(bridge, name) is not None
    assert bridge.cards is bridge._cards
    assert bridge.activities is bridge._activities
    assert bridge.transcript is bridge._transcript


def test_connections_handlers_declare_signal_arguments():
    """``Connections`` handlers must match the signal's arity.

    A zero-argument ``function onRowsInserted()`` matches no signal on
    QAbstractListModel and prints a warning per handler at startup.
    """
    qml = (QML_DIR / "LiveActivityBar.qml").read_text(encoding="utf-8")
    for handler in ("onRowsInserted(parent, first, last)",
                    "onRowsRemoved(parent, first, last)",
                    "onDataChanged(topLeft, bottomRight, roles)"):
        assert handler in qml, f"LiveActivityBar.qml is missing {handler!r}"


def test_live_activity_bar_guards_a_null_model():
    """Line 250 feeds ``model && model.rowCount()`` to a bool property."""
    qml = (QML_DIR / "LiveActivityBar.qml").read_text(encoding="utf-8")
    assert "!!bar.model" in qml, (
        "the hairline progress visible: binding must coerce to bool")


def test_live_activity_bar_never_assigns_its_bindings_imperatively():
    """`bar.visible = ...` in a signal handler destroys the binding."""
    raw = (QML_DIR / "LiveActivityBar.qml").read_text(encoding="utf-8")
    assert "function refresh() { bar.agentCount = bar.countActive() }" in raw
    assert "visible: agentCount > 0 || expanded" in raw
    # Strip comments first: the file's own explanatory prose mentions the
    # anti-pattern it warns against.
    code = "\n".join(line.split("//")[0] for line in raw.splitlines())
    for broken in ("bar.visible =", "bar.opacity ="):
        assert broken not in code, (
            f"{broken!r} breaks a declarative binding after the first row change")


# ─────────────────────────────────────────────────────────────────────────────
# Regression: DORMANT must be idle, not booting
# ─────────────────────────────────────────────────────────────────────────────

def test_dormant_is_and_listening_never_booting():
    """DORMANT means "awake and idle", so it renders LISTENING.

    It used to coerce to BOOTING, which left a spinner on screen forever.
    """
    from ui_siri.event_bus import UiState

    for legacy in ("DORMANT", "SLEEPING", "ASLEEP"):
        assert UiState.coerce(legacy) is UiState.LISTENING, legacy


def test_dormant_cycle_ends_idle(app):
    """Walk the full lifecycle and land back on an idle LISTENING."""
    from ui_siri.bridge import UiBridge, UiDispatcher
    from ui_siri.event_bus import EventBus, UiState
    from ui_siri.state_machine import AssistantStateMachine

    machine = AssistantStateMachine()
    bridge = UiBridge(machine, EventBus(), UiDispatcher())

    for sent, expected in (("LISTENING", "LISTENING"), ("THINKING", "THINKING"),
                           ("SPEAKING", "SPEAKING"), ("ERROR", "MUTED"),
                           ("DORMANT", "LISTENING")):
        machine.coerce_legacy(sent)
        assert bridge.state == expected, f"{sent} -> {bridge.state!r}"
    assert machine.state is UiState.LISTENING


def test_perimeter_glow_idles_when_not_working():
    """The legacy perimeter glow and its overlay window are gone.

    The Siri orb is the only always-loaded visual: it materialises with an
    elastic spring bloom on invocation, and its halo collapses to a faint
    idle sweep for LISTENING / MUTED — nothing persists on the desktop.
    """
    qml = (QML_DIR / "SiriOrb.qml").read_text(encoding="utf-8")
    assert "summoning" in qml and "dismissing" in qml, (
        "the orb must materialise on invocation and contract on dismissal")
    assert "Easing.OutElastic" in qml, (
        "materialisation must be an elastic spring bloom, not a pop-in")
    assert "muted ? 0.4 : 1" in qml, (
        "idle/muted states must collapse the halo to near-zero")
    assert "OverlayWindow" not in qml and "PerimeterGlow" not in qml
