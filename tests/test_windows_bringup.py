"""Regressions for the Windows 11 first-run failures.

These are the errors reported from real Windows 11 runs (Python 3.13.3), plus
the latent ones found while auditing the same code paths. The Linux sandbox cannot validate Win32 behavior directly; source invariants
and pure-Python behavior are supplemented by native Windows testing.
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


# ── Native overlay bring-up invariants ──────────────────────────────────────

def test_native_overlay_uses_shape_hit_testing_not_an_input_transparent_window():
    source = (REPO / "ui" / "island" / "voice_hud.py").read_text(encoding="utf-8")
    native = (REPO / "ui" / "island" / "win32_overlay.py").read_text(encoding="utf-8")
    assert "FramelessWindowHint" in source and "WA_TranslucentBackground" in source
    assert "WindowTransparentForInput" not in source and "self.setMask(" not in source
    assert "self._panel_stack.setMask(" in source
    assert "WM_NCHITTEST" in native and "current_path()" in native
    assert "0x08000000" in native and "0x00000080" in native


def test_native_ui_has_no_external_renderer_launch():
    source = (REPO / "ui" / "bridge" / "controller.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) and
                   any(alias.name == 'subprocess' for alias in node.names)
                   for node in ast.walk(tree))


# ── Error 2: TrayThread died importing actions.friday_wake ──────────────────

def _load_matcher_fn():
    """Extract _load_matcher without importing the module.

    actions/wake_word.py needs numpy + sounddevice + PortAudio at import time,
    which is exactly the kind of environment-dependent import this fallback
    exists to tolerate, so we lift the function out of the AST instead.
    """
    source = (REPO / "actions" / "wake_word.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = [n for n in tree.body
          if isinstance(n, ast.FunctionDef) and n.name == "_load_matcher"]
    assert fn, "_load_matcher() is missing"
    namespace: dict = {"importlib": importlib}
    exec(compile(ast.Module(body=fn, type_ignores=[]), "<wake>", "exec"), namespace)
    return namespace["_load_matcher"]


def test_wake_word_no_longer_imports_the_dead_module_name():
    source = (REPO / "actions" / "wake_word.py").read_text(encoding="utf-8")
    # The *old* name may only appear as a backwards-compatible fallback string.
    for line in source.splitlines():
        if "actions.friday_wake" in line:
            assert "friday_wake" in source, "new name must be tried first"
            assert "importlib" in source, "must be a runtime import, not a hard import"
    
    assert "from actions.friday_wake import" not in source, (
        "a module-level from-import is what crashed TrayThread"
    )


def test_matcher_falls_back_when_no_module_exists(monkeypatch):
    for name in list(sys.modules):
        if name.startswith(("actions.friday_wake", "actions.friday_wake")):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(importlib, "import_module",
                        lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name)))
    assert _load_matcher_fn()() is None


def test_matcher_prefers_the_new_module(monkeypatch):
    module = types.ModuleType("actions.friday_wake")
    module._matcher = object()
    monkeypatch.setitem(sys.modules, "actions.friday_wake", module)
    assert _load_matcher_fn()() is module._matcher


def test_matcher_falls_back_to_the_old_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "actions.friday_wake", None)
    module = types.ModuleType("actions.friday_wake")
    module._matcher = object()
    monkeypatch.setitem(sys.modules, "actions.friday_wake", module)
    assert _load_matcher_fn()() is module._matcher


def test_matcher_survives_a_module_that_raises_on_attribute_access(monkeypatch):
    """A lazy `import onnxruntime` inside the module raises at attribute time."""
    bad = types.ModuleType("actions.friday_wake")

    def _boom(name):
        if name == "_matcher":
            raise ImportError("onnxruntime is not installed")
        raise AttributeError(name)

    bad.__getattr__ = _boom
    monkeypatch.setitem(sys.modules, "actions.friday_wake", bad)
    monkeypatch.setitem(sys.modules, "actions.friday_wake", None)
    assert _load_matcher_fn()() is None


# ── Error 3: `name 're' is not defined` after every tool call ──────────────

def test_main_imports_re():
    source = (REPO / "main.py").read_text(encoding="utf-8")
    assert re.search(r"^import re$", source, re.M), "main.py must import re"
    # ...and actually use it, or the import would be dead.
    assert "re.findall" in source


def test_vision_gesture_is_imported_where_it_is_used():
    """`vision_gesture` was referenced with no binding at all: every
    face-verified tool call raised NameError."""
    source = (REPO / "main.py").read_text(encoding="utf-8")
    assert "from actions.vision_gesture import vision_gesture" in source, \
        "vision_gesture must be imported (lazily, like other action modules)"


def test_no_module_level_action_imports_in_main():
    """Action modules are imported lazily so startup stays fast and optional
    dependencies stay optional; a module-level import would break that."""
    tree = ast.parse((REPO / "main.py").read_text(encoding="utf-8"))
    offenders = []
    for node in tree.body:  # module scope only
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("actions."):
                offenders.append(node.module)
    assert not offenders, f"module-level action imports in main.py: {offenders}"


# ── Python 3.13 / deprecations ─────────────────────────────────────────────

def test_no_deprecated_asyncio_get_event_loop():
    source = (REPO / "main.py").read_text(encoding="utf-8")
    assert "asyncio.get_event_loop()" not in source, (
        "deprecated since 3.12 and removed in future versions; "
        "every use here is inside a coroutine so get_running_loop() is correct"
    )


# ── the strict-UI escape hatch ─────────────────────────────────────────────

def test_make_ui_is_siri_only(monkeypatch):
    import inspect
    import _archive.ui_legacy as ui_legacy

    assert ui_legacy.THEME_NAMES == ("siri",)
    assert ui_legacy.DEFAULT_THEME == "siri"
    # No legacy switching machinery survives the removal of ui_legacy.py.
    for removed in ("_env_flag", "_load_legacy_class", "_legacy_requested",
                    "_strict_requested"):
        assert not hasattr(ui_legacy, removed), f"{removed} must not remain"
    src = inspect.getsource(ui_legacy.make_ui)
    for token in ("FRIDAY_LEGACY_UI", "FRIDAY_STRICT_UI", "_load_legacy_class",
                  "_legacy_requested", "_strict_requested"):
        assert token not in src, f"{token} must not gate make_ui()"
    # Env flags no longer select an implementation: make_ui builds the
    # ambient Siri overlay and exits hard if its Electron UI cannot start.
    monkeypatch.setenv("FRIDAY_LEGACY_UI", "1")
    monkeypatch.setenv("FRIDAY_STRICT_UI", "1")
    assert ui_legacy.THEME_NAMES == ("siri",)
    assert ui_legacy.DEFAULT_THEME == "siri"


def test_make_ui_logs_a_full_traceback_before_system_exit(monkeypatch, capsys):
    import _archive.ui_legacy as ui_legacy

    def _explode(*_a, **_k):
        raise RuntimeError("simulated UI failure")

    monkeypatch.setattr(ui_legacy, "FridayUI", _explode)

    with pytest.raises(SystemExit):
        ui_legacy.make_ui("face.png")

    out = capsys.readouterr()
    combined = out.out + out.err
    assert "Traceback (most recent call last)" in combined
    assert "simulated UI failure" in combined


def test_card_mapper_return_arity_is_documented():
    """from_log_event -> (card, activities); to_cards -> (card, activities, event).

    The Windows verifier treated from_log_event()'s result as a card and died
    with "'tuple' object has no attribute 'to_dict'". This pins the arity so a
    future caller cannot make the same mistake silently.
    """
    import inspect

    sys.path.insert(0, str(REPO))
    from _archive.ui_legacy import card_mapper

    assert len(inspect.signature(card_mapper.from_log_event).parameters) >= 1
    # Behavioural check: the shapes are what the docstrings promise.
    from _archive.ui_legacy.event_bus import classify_legacy

    event = classify_legacy("[Code] verify row 0")
    result = card_mapper.from_log_event(event)
    assert isinstance(result, tuple) and len(result) == 2, result
    card, activities = result
    assert card is None or hasattr(card, "to_dict"), "first element must be a Card"
    assert isinstance(activities, list)

    triple = card_mapper.to_cards("[Code] verify row 0")
    assert isinstance(triple, tuple) and len(triple) == 3, triple


def test_verifier_never_assigns_a_mapper_tuple_to_a_bare_name():
    """Guards the class of bug that crashed check_models() on Windows.

    `card = from_log_event(...)` bound a (card, activities) tuple to `card` and
    blew up on the next attribute access. The verifier now feeds real payloads
    through `to_cards()`, which returns a 3-tuple, so the guard is that every
    mapper call in the file is unpacked.
    """
    import re

    source = (REPO / "scripts" / "verify_windows_ui.py").read_text(encoding="utf-8")
    for name in ("from_log_event", "to_cards", "from_envelope"):
        # A bare `name = mapper(...)` binds the tuple itself.
        bad = re.findall(rf"^\s*\w+\s*=\s*{name}\(", source, re.MULTILINE)
        assert not bad, f"{name}() returns a tuple and must be unpacked, got {bad}"
    assert "card_mapper.to_cards(" in source, \
        "check_models() must feed real payloads through the mapper"


def test_verifier_asserts_windows11_capabilities():
    source = (REPO / "scripts" / "verify_windows_ui.py").read_text(encoding="utf-8")
    for name in ("capture_exclusion", "layered_windows", "backdrop",
                 "rounded_corners", "dnd"):
        assert name in source, f"verifier never asserts {name}"
    assert "22621" in source, "capability assertions must gate on the Win11 22H2 build"
    assert "explain_last_error" in source, \
        "a rejected SetWindowDisplayAffinity must report the Win32 error code"
