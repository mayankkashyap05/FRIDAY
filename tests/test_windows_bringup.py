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
    assert "WindowTransparentForInput" not in source and ".setMask(" not in source
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

