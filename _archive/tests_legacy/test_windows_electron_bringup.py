"""Retired Electron-specific Windows bringup checks."""
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]

# ── Error 1: the overlay must never be started through a shell shim ─────────

def test_electron_is_spawned_from_the_real_binary_not_a_shim():
    """Windows' ``electron.cmd`` is a cmd.exe batch file: terminating it leaves
    the actual Electron process running. The controller must spawn
    ``node_modules/electron/dist/electron.exe`` directly."""
    source = (REPO / "ui" / "controller.py").read_text(encoding="utf-8")
    assert "electron.exe" in source
    # No string literal may name a batch shim (prose in the docstring may
    # mention it; an executable path may not).
    import ast

    literals = [node.value for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    offenders = [value for value in literals
                 if value.lower().endswith((".cmd", ".bat"))]
    assert not offenders, f"never launch Electron through a shim: {offenders}"
    spawn = source[source.index("self._electron = subprocess.Popen("):]
    spawn = spawn[: spawn.index("\n            )")]
    assert "[str(exe), str(self.app_dir.resolve())]" in spawn, (
        "spawn [exe, app_dir] with no shell and no cmd.exe")
    assert "shell=True" not in source


def test_renderer_and_electron_are_verified_before_launch():
    """A missing build must fail loudly with the fix commands, not open a
    blank window."""
    source = (REPO / "ui" / "controller.py").read_text(encoding="utf-8")
    assert "renderer_built" in source
    assert "npm install" in source and "npm run build" in source
