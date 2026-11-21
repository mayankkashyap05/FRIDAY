"""Legacy UI compatibility contract.

These tests are the executable form of ``docs/UI_AUDIT_CONTRACT.md`` §3. They
assert that *both* the ambient implementation and the legacy Tk implementation
expose the same surface, so the migration cannot silently break ``main.py`` or
``actions/``.

The ambient façade is imported and introspected but never instantiated with a
real window here (a GUI session is not available in CI), which is why the
surface assertions are static.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import ui

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


def test_themes_keep_the_four_classic_names():
    for name in ("cyber", "ocean", "aurora", "sunset"):
        assert name in ui.THEMES


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


def test_legacy_class_still_available_and_matches_contract():
    """The Tk fallback must keep satisfying the same contract."""
    pytest.importorskip("tkinter", reason="Tk not installed on this host")
    legacy = ui._load_legacy_class()
    for name in REQUIRED_METHODS:
        if name in ("show", "hide", "quit", "run", "raise_window"):
            # Tk implements these through `root`; the adapter handles the rest.
            continue
        assert callable(getattr(legacy, name, None)), f"legacy FridayUI.{name} missing"


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
    return ui._QtRootAdapter(fake), fake


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
    from ui_siri.event_bus import UiState

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


# ── legacy switch ──────────────────────────────────────────────────────────

def test_legacy_env_switch_is_parsed(monkeypatch):
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("FRIDAY_LEGACY_UI", value)
        assert ui._legacy_requested() is True
    for value in ("0", "false", "", "no", "off"):
        monkeypatch.setenv("FRIDAY_LEGACY_UI", value)
        assert ui._legacy_requested() is False
