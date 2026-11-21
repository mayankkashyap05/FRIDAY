"""Regressions for the Windows 11 first-run failures.

These are the three errors reported from a real Windows 11 run (Python 3.13.3,
PySide6 6.11.3), plus the latent ones found while auditing the same code paths.
The Linux sandbox cannot execute QtGui/QML, so each bug is pinned at the level
where it *is* observable here: source invariants and pure-Python behaviour.
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


# ── Error 1: `name 'qml_dir' is not defined` killed the whole overlay ───────

def test_controller_has_no_undefined_qml_dir():
    source = (REPO / "ui_siri" / "controller.py").read_text(encoding="utf-8")
    assert "qml_dir" not in source, (
        "`qml_dir` was the NameError that forced the Tk fallback; the Qt-free "
        "theme module exports QML_DIR instead"
    )
    assert "QML_DIR" in source


def test_singleton_registration_uses_a_local_file_url():
    """Windows paths are C:\\..., so the URL must come from fromLocalFile."""
    source = (REPO / "ui_siri" / "controller.py").read_text(encoding="utf-8")
    block = source[source.index("def _register_singleton"):]
    block = block[: block.index("\nclass ") if "\nclass " in block else len(block)]
    assert "QUrl.fromLocalFile" in block
    assert ".resolve()" in block, "paths handed to Qt must be absolute+resolved"
    assert 'f"file://' not in block


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
            assert "kaizumi_wake" in source, "new name must be tried first"
            assert "importlib" in source, "must be a runtime import, not a hard import"
    
    assert "from actions.friday_wake import" not in source, (
        "a module-level from-import is what crashed TrayThread"
    )


def test_matcher_falls_back_when_no_module_exists(monkeypatch):
    for name in list(sys.modules):
        if name.startswith(("actions.kaizumi_wake", "actions.friday_wake")):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(importlib, "import_module",
                        lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name)))
    assert _load_matcher_fn()() is None


def test_matcher_prefers_the_new_module(monkeypatch):
    module = types.ModuleType("actions.kaizumi_wake")
    module._matcher = object()
    monkeypatch.setitem(sys.modules, "actions.kaizumi_wake", module)
    assert _load_matcher_fn()() is module._matcher


def test_matcher_falls_back_to_the_old_module(monkeypatch):
    monkeypatch.delitem(sys.modules, "actions.kaizumi_wake", raising=False)
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
    monkeypatch.delitem(sys.modules, "actions.kaizumi_wake", raising=False)
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

def test_make_ui_honours_strict_and_legacy_flags(monkeypatch):
    import ui

    assert ui._env_flag("FRIDAY_LEGACY_UI") is False
    monkeypatch.setenv("FRIDAY_LEGACY_UI", "1")
    assert ui._legacy_requested() is True
    assert ui._strict_requested() is False
    monkeypatch.setenv("FRIDAY_STRICT_UI", "true")
    assert ui._strict_requested() is True
    monkeypatch.setenv("FRIDAY_STRICT_UI", "0")
    assert ui._strict_requested() is False


def test_make_ui_raises_instead_of_falling_back_when_strict(monkeypatch):
    import ui

    def _explode(*_a, **_k):
        raise RuntimeError("simulated QML failure")

    monkeypatch.setenv("FRIDAY_STRICT_UI", "1")
    monkeypatch.delenv("FRIDAY_LEGACY_UI", raising=False)
    monkeypatch.setattr(ui, "FridayUI", _explode)
    with pytest.raises(RuntimeError, match="simulated QML failure"):
        ui.make_ui("face.png")


def test_make_ui_logs_a_full_traceback_before_falling_back(monkeypatch, capsys):
    import ui

    def _explode(*_a, **_k):
        raise RuntimeError("simulated QML failure")

    def _no_tk(*_a, **_k):
        raise ModuleNotFoundError("no tkinter either")

    monkeypatch.delenv("FRIDAY_STRICT_UI", raising=False)
    monkeypatch.delenv("FRIDAY_LEGACY_UI", raising=False)
    monkeypatch.setattr(ui, "FridayUI", _explode)
    monkeypatch.setattr(ui, "_load_legacy_class", _no_tk)

    with pytest.raises(SystemExit):
        ui.make_ui("face.png")

    out = capsys.readouterr()
    combined = out.out + out.err
    assert "Traceback (most recent call last)" in combined
    assert "simulated QML failure" in combined
