"""Legacy UI compatibility contract.

These tests are the executable form of ``docs/UI_AUDIT_CONTRACT.md`` §3. They
assert that the ambient Siri implementation exposes the full legacy-compatible
surface, so the migration cannot silently break ``main.py`` or ``actions/``.
There is one implementation — the Electron Siri overlay in ``ui/`` — and no
fallback of any kind.

The facade is imported and introspected, but never instantiated here (that
would spawn Electron), which is why the surface assertions are static.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import threading

import ui
from ui import FridayUI

REPO = Path(__file__).resolve().parent.parent


# ── The contract, transcribed from the audit ────────────────────────────────

REQUIRED_METHODS = (
    # state / connection / persona
    "set_state", "set_connection", "set_connecting", "set_persona",
    "start_speaking", "stop_speaking",
    # transcript
    "write_log",
    # mute / text-only  (private names are part of the contract)
    "_toggle_mute", "_draw_mute_button", "_toggle_silent_mode", "_safe_ui",
    # theme
    "set_theme", "cycle_theme",
    # vision / arcade
    "set_vision_signal", "set_vision_detections", "set_vision_frame",
    "open_vision_preview", "set_arcade_frame", "set_arcade_signal",
    "set_arcade_pose", "open_vision_arcade",
    # window / loop
    "show", "hide", "quit", "run",
    # api key
    "_api_keys_exist", "wait_for_api_key",
)

REQUIRED_ATTRIBUTES = (
    "root", "on_text_command", "muted", "silent_mode", "speaking",
    "connected", "connecting", "vision_preview_open", "arcade_open",
    "vision_mode", "vision_signal", "persona_mode", "persona_mood",
    "persona_voice", "last_event",
)

REQUIRED_MODULE_GLOBALS = (
    "API_FILE", "CONFIG_DIR", "BASE_DIR", "THEME_FILE", "THEMES",
    "SYSTEM_NAME", "MODEL_BADGE", "get_base_dir", "_load_theme_name",
    "_save_theme_name", "FridayUI",
)


# ── Module surface ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", REQUIRED_MODULE_GLOBALS)
def test_module_global_present(name):
    assert hasattr(ui, name), f"ui.{name} disappeared — callers depend on it"


def test_themes_are_siri_only():
    assert ui.THEME_NAMES == ("siri",)
    assert "siri" in ui.THEMES


def test_api_file_is_reassignable(tmp_path, monkeypatch):
    """tests/test_p0_security.py monkeypatches ui.API_FILE."""
    original = ui.API_FILE
    try:
        ui.API_FILE = tmp_path / "api_keys.json"
        assert ui.API_FILE != original
    finally:
        ui.API_FILE = original


def test_api_keys_exist_works_on_a_bare_stub(tmp_path):
    """Bound unbound to a Stub() instance (no __init__ ever ran)."""
    from types import MethodType

    original = ui.API_FILE
    try:
        ui.API_FILE = tmp_path / "api_keys.json"
        stub = type("Stub", (), {})()
        fn = MethodType(ui.FridayUI._api_keys_exist, stub)
        assert fn() is False
        ui.API_FILE.write_text('{"gemini_api_key": "k"}', encoding="utf-8")
        assert fn() is True
        ui.API_FILE.write_text("{not json", encoding="utf-8")
        assert fn() is False
    finally:
        ui.API_FILE = original


# ── Class surface (static — no window needed) ───────────────────────────────

@pytest.mark.parametrize("name", REQUIRED_METHODS)
def test_ambient_class_has_method(name):
    assert callable(getattr(ui.FridayUI, name, None)), \
        f"FridayUI.{name} missing or not callable"


def test_constructor_signature_is_unchanged():
    sig = inspect.signature(ui.FridayUI.__init__)
    params = list(sig.parameters)
    assert params[:3] == ["self", "face_path", "size"], params
    assert sig.parameters["size"].default is None


def test_ambient_class_declares_parity_attributes_via_source():
    """Attributes are set in __init__, so check the source declares them."""
    src = inspect.getsource(ui.FridayUI.__init__)
    for name in REQUIRED_ATTRIBUTES:
        if name == "root":
            assert "self.root" in src
            continue
        assert re.search(rf"self\.{re.escape(name)}\s*=", src), \
            f"FridayUI.__init__ no longer sets self.{name}"


def test_no_legacy_fallback_remains():
    """No Tk module, no Qt/QML module, and no escape hatch: Siri (Electron) is
    the sole UI implementation, so nothing can silently fall back."""
    assert not hasattr(ui, "_load_legacy_class")
    assert not hasattr(ui, "_legacy_requested")
    assert not hasattr(ui, "_strict_requested")
    import importlib.util

    assert importlib.util.find_spec("ui_legacy") is None
    for removed in ("image_provider", "qml"):
        assert importlib.util.find_spec(f"ui.{removed}") is None


# ── root duck-type contract ─────────────────────────────────────────────────

class _Recorder:
    """Stand-in for the controller: records dispatches instead of drawing."""

    def __init__(self):
        self.calls = []

    def call(self, fn, *args, **kwargs):
        self.calls.append((getattr(fn, "__name__", fn), args))
        return True


def _adapter():
    """Build a root adapter over a minimal fake FridayUI."""

    class FakeUI:
        visible = True

        def __init__(self):
            self.calls = []

        def _dispatch(self, fn, *a, **k):
            self.calls.append((fn, a))
            fn(*a)

        def show(self):
            self.visible = True

        def hide(self):
            self.visible = False

        def raise_window(self):
            self.calls.append(("raise",))

        def quit(self):
            self.calls.append(("quit",))

        def run(self):
            return 0

        def screen_size(self):
            return (1920, 1080)

        def window_size(self):
            return (456, 216)

    fake = FakeUI()
    return ui._RootAdapter(fake), fake


def test_root_after_runs_the_callback():
    root, fake = _adapter()
    hits = []
    root.after(0, hits.append, 1)
    assert hits == [1]


def test_root_after_is_thread_safe():
    """main.py calls root.after(0, cb) from the pystray / keyboard threads."""
    import threading

    root, _ = _adapter()
    hits = []
    def post(value):
        root.after(0, hits.append, value)

    threads = [threading.Thread(target=post, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(hits) == list(range(20))


def test_root_window_state_round_trip():
    root, fake = _adapter()
    assert root.state() == "normal"
    root.withdraw()
    assert root.state() == "withdrawn"
    root.deiconify()
    assert root.state() == "normal"
    root.lift()
    assert ("raise",) in fake.calls


def test_root_geometry_and_screen_queries():
    root, _ = _adapter()
    assert root.winfo_screenwidth() == 1920
    assert root.winfo_screenheight() == 1080
    assert root.geometry() == "456x216"


def test_root_destroy_maps_to_quit():
    root, fake = _adapter()
    root.destroy()
    assert ("quit",) in fake.calls


def test_root_unknown_winfo_is_a_noop_callable():
    root, _ = _adapter()
    root.winfo_exists()          # must not raise
    with pytest.raises(AttributeError):
        root.definitely_not_a_method


# ── set_state literal coverage ──────────────────────────────────────────────

def test_ui_state_coercion_covers_every_literal_main_py_uses():
    """Guard against main.py gaining a state string the UI drops."""
    from ui.event_bus import UiState

    source = (REPO / "main.py").read_text(encoding="utf-8")
    literals = set(re.findall(r'set_state\("([A-Za-z_-]+)"\)', source))
    assert literals, "no set_state literals found — did main.py change?"
    assert literals <= {"LISTENING", "THINKING", "SPEAKING", "MUTED",
                        "PROCESSING", "ONLINE"}
    for literal in literals:
        assert isinstance(UiState.coerce(literal), UiState)


def test_write_log_call_sites_all_use_a_string():
    """`player.write_log(...)` must never be passed a non-str at the call site."""
    offenders = []
    for path in (REPO / "actions").glob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ".write_log(" not in line:
                continue
            if "json" in line or "self._write_log" in line:
                continue
            if 'f"' in line or '"' in line or "'" in line:
                continue
            offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "write_log called with a non-literal:\n" + "\n".join(offenders)


# ── startup burst control ───────────────────────────────────────────────────

class _RecordingBridge:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def ingest(self, payload) -> None:
        self.seen.append(payload)


def _bare_facade():
    """A FridayUI without the Qt constructor (which needs libGL)."""
    obj = FridayUI.__new__(FridayUI)
    obj._pending_cards = []
    obj._MAX_PENDING_LOGS = 200
    obj._loop_entered = False
    obj.bridge = _RecordingBridge()
    obj._closed = threading.Event()
    obj._dispatch = lambda fn, *a, **k: fn(*a, **k)
    obj._ingest = lambda payload: obj.bridge.ingest(payload)
    return obj


def test_logs_before_the_event_loop_are_coalesced_not_queued():
    """Every backend thread logs at once on startup; replaying all of it into
    the UI in the first frame is pointless. Only the tail is kept."""
    facade = _bare_facade()
    for i in range(1000):
        FridayUI.write_log(facade, f"[SYS] boot line {i}")

    assert facade.bridge.seen == [], "nothing may touch the UI before exec()"
    assert 0 < len(facade._pending_cards) <= facade._MAX_PENDING_LOGS
    # The *tail* survives, which is what a user actually needs to see.
    assert facade._pending_cards[-1] == "[SYS] boot line 999"


def test_flush_delivers_each_early_log_exactly_once():
    facade = _bare_facade()
    for i in range(50):
        FridayUI.write_log(facade, f"line {i}")

    facade._flush_pending_logs()
    delivered = len(facade.bridge.seen)
    assert delivered == 50
    assert facade._pending_cards == []

    facade._flush_pending_logs()          # idempotent
    assert len(facade.bridge.seen) == delivered


def test_logs_after_the_loop_go_straight_through():
    facade = _bare_facade()
    facade._loop_entered = True
    FridayUI.write_log(facade, "live line")
    assert facade.bridge.seen == ["live line"]
    assert facade._pending_cards == []
