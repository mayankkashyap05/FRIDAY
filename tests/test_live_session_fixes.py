"""Regressions for the post-launch live-session fixes.

Three problems were only visible once ``main.py`` was actually running against
the live Gemini API:

1. ``gemini-2.5-flash-lite`` was retired server-side
   (``404 NOT_FOUND ... no longer available to new users``). The id was
   copy-pasted across nine files, so it is now a single constant.
2. ``[FRIDAY] ⏱ Speaking watchdog fired — force-cleared`` appeared on ordinary
   replies, because the player released on a single ``queue.empty()`` sample
   while the watchdog used a fixed 25 s ceiling -- and the muted path never
   published a release at all, which left audio ducking engaged.
3. ``Ctrl+C`` in the Qt loop surfaced as a ``KeyboardInterrupt`` from inside
   ``UiDispatcher.drain``, skipping all teardown (ducked audio stayed ducked and
   the tray message loop survived).
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ─────────────────────────────────────────────────────────────────────────────
# 1. Retired model id
# ─────────────────────────────────────────────────────────────────────────────

#: Files that talked to the retired model directly.
MODEL_CALLERS = [
    "actions/computer_control.py",
    "actions/computer_settings.py",
    "actions/flight_finder.py",
    "actions/pdf_reader.py",
    "actions/web_search.py",
    "agent/error_handler.py",
    "agent/executor.py",
    "agent/planner.py",
    "memory/memory_manager.py",
]


def _py_files():
    excluded_dirs = {".git", "tests", ".venv", "venv", "node_modules", "__pycache__"}
    files = []
    for root, dirs, names in os.walk(REPO):
        dirs[:] = [name for name in dirs if name not in excluded_dirs]
        files.extend(Path(root) / name for name in names if name.endswith(".py"))
    return files


def test_py_files_excludes_non_project_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    included = [tmp_path / "main.py", tmp_path / "src" / "venv_helper.py"]
    excluded = [tmp_path / name / "nested" / "installed.py"
                for name in (".git", "tests", ".venv", "venv", "node_modules", "__pycache__")]
    for path in included + excluded:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("gemini-2.5-flash-lite", encoding="utf-8")

    assert set(_py_files()) == set(included)


def test_no_live_code_references_the_retired_lite_model():
    """`gemini-2.5-flash-lite` returns 404 for new users."""
    offenders = []
    for path in _py_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "gemini-2.5-flash-lite" not in line:
                continue
            # config/models.py documents *why* the id was retired; that is the
            # one place the string is allowed to appear.
            if path.name == "models.py" or lineno and "#" in line.split("gemini-2.5-flash-lite")[0]:
                continue
            if path == REPO / "config" / "models.py":
                continue
            offenders.append(f"{path.relative_to(REPO)}:{lineno}")
    assert not offenders, (
        "retired model id still referenced (use config.models.MODEL_FLASH_LITE): "
        f"{offenders}")


def test_lite_model_constant_is_the_current_id():
    from config.models import MODEL_FLASH_LITE

    assert MODEL_FLASH_LITE == "gemini-3.5-flash-lite"


def test_every_model_caller_imports_the_shared_constant():
    """No call site may hard-code the lite id again."""
    for rel in MODEL_CALLERS:
        text = (REPO / rel).read_text(encoding="utf-8-sig")
        assert "MODEL_FLASH_LITE" in text, f"{rel} does not use the shared constant"
        assert "from config.models import" in text, f"{rel} does not import it"
        # The literal must be gone from the call sites themselves.
        for line in text.splitlines():
            if "gemini-3.5-flash-lite" in line:
                pytest.fail(f"{rel} hard-codes the model id: {line.strip()}")


def test_model_callers_use_the_name_not_a_literal():
    """Every `GenerativeModel(...)`/`generate_content(model=...)` uses the name."""
    for rel in MODEL_CALLERS:
        # utf-8-sig: several legacy modules carry a UTF-8 BOM, which Python
        # accepts at runtime but ast.parse() rejects.
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "model" and isinstance(kw.value, ast.Constant):
                    if "flash-lite" in str(kw.value.value):
                        pytest.fail(
                            f"{rel}:{node.lineno} passes a literal model id")
            # positional form: GenerativeModel("gemini-...")
            if isinstance(node.func, ast.Attribute) and node.func.attr == "GenerativeModel":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and "flash-lite" in str(arg.value):
                        pytest.fail(
                            f"{rel}:{node.lineno} passes a literal model id")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Speaking release / ducking
# ─────────────────────────────────────────────────────────────────────────────

def test_release_decision_uses_the_grace_period():
    """The player and the watchdog must share one release predicate."""
    from config.audio import AUDIO_END_GRACE_S, SPEAKING_WATCHDOG_S

    # A generous grace stops a transient empty queue cutting a reply short.
    assert AUDIO_END_GRACE_S >= 0.2
    # The watchdog must be a backstop, not the normal release path: it has to be
    # comfortably longer than the grace the player uses.
    assert SPEAKING_WATCHDOG_S > AUDIO_END_GRACE_S


def test_playback_loop_does_not_release_on_a_single_empty_sample():
    """A bare `if queue.empty(): release` truncates speech mid-sentence."""
    src = (REPO / "main.py").read_text(encoding="utf-8")
    assert "_speech_should_release" in src
    # The old one-shot condition must be gone.
    assert "if self.audio_in_queue.empty() and self._turn_complete:" not in src, (
        "single-sample empty() check is back; it releases between chunks")


def test_watchdog_measures_silence_from_the_last_chunk_written():
    """It must not fire off the last chunk that *arrived* from the network."""
    src = (REPO / "main.py").read_text(encoding="utf-8")
    assert "_last_audio_write_ts" in src
    block = src.split("async def _watch_speaking")[1].split("async def _receive_audio")[0]
    assert "_last_audio_write_ts" in block, (
        "watchdog still keys off _last_play_ts (arrival time), so it fires "
        "whenever a tool call keeps the model quiet for a while")
    assert "audio_in_queue.empty()" in block, (
        "watchdog must confirm the queue is drained before releasing")


def test_state_machine_release_speech_always_emits():
    """Muted must not swallow the release: stop_speaking transitions cleanly."""
    import ui

    src = (REPO / "ui" / "__init__.py").read_text(encoding="utf-8")
    assert "def stop_speaking(self):" in src
    block = src.split("def stop_speaking(self):")[1].split("def ")[0]
    assert "MUTED" in block and "LISTENING" in block


def test_ducking_is_restored_on_release_including_muted():
    """set_muted(True/False) and stop_speaking() emit proper state transitions."""
    src = (REPO / "ui" / "__init__.py").read_text(encoding="utf-8")
    assert "def set_muted" in src or "muted" in src
    assert "def stop_speaking(self):" in src
    assert hasattr(ui_mod := __import__("ui"), "FridayUI")
    assert hasattr(ui_mod.FridayUI, "stop_speaking")


def test_set_speaking_false_releases_even_when_muted():
    """`_set_phase(LISTENING)` is a no-op when muted, so main.py must not rely on it."""
    src = (REPO / "main.py").read_text(encoding="utf-8")
    block = src.split("def set_speaking(self, value: bool):")[1].split("def _persona_info")[0]
    assert "stop_speaking()" in block, (
        "set_speaking(False) only calls _set_phase, which does nothing while "
        "muted -- the machine then stays on SPEAKING forever")
    ui_src = (REPO / "ui" / "__init__.py").read_text(encoding="utf-8")
    assert "def stop_speaking(self):" in ui_src


# ─────────────────────────────────────────────────────────────────────────────
# 3. Graceful Ctrl+C
# ─────────────────────────────────────────────────────────────────────────────

def test_controller_installs_a_sigint_handler():
    src = (REPO / "main.py").read_text(encoding="utf-8")
    assert "KeyboardInterrupt" in src
    assert "ui.quit()" in src


def test_sigint_handler_is_installed_before_the_event_loop():
    """main.py catches KeyboardInterrupt and cancels the voice task cleanly."""
    src = (REPO / "main.py").read_text(encoding="utf-8")
    assert "KeyboardInterrupt" in src
    assert "finally:" in src


def test_shutdown_restores_audio_and_stops_the_tray():
    main_src = (REPO / "main.py").read_text(encoding="utf-8")
    assert "system_tray" in main_src and "stop()" in main_src
    ctrl_src = (REPO / "ui" / "bridge" / "controller.py").read_text(encoding="utf-8")
    assert "def shutdown(self" in ctrl_src


def test_shutdown_is_idempotent():
    """UiController.shutdown() is idempotent via self._closed."""
    src = (REPO / "ui" / "bridge" / "controller.py").read_text(encoding="utf-8")
    block = src.split("def shutdown(self")[1][:400]
    assert "if self._closed:" in block
    assert "return" in block


def test_sigint_handler_never_leaves_a_traceback_in_drain():
    """UiDispatcher._drain() catches Exception so a bad callback never kills the UI loop."""
    bridge = (REPO / "ui" / "bridge" / "bridge.py").read_text(encoding="utf-8")
    drain = bridge.split("def _drain(self")[1].split("def ")[0]
    assert "except Exception" in drain


# ─────────────────────────────────────────────────────────────────────────────
# Behavioural check of the real player loop (not just source inspection)
# ─────────────────────────────────────────────────────────────────────────────

class _FakeStream:
    """Stand-in for a RawOutputStream (no PortAudio in CI)."""

    def __init__(self, **_kwargs):
        self.written = 0

    def start(self):
        pass

    def write(self, _chunk):
        import time
        time.sleep(0.005)
        self.written += 1

    def stop(self):
        pass

    def close(self):
        pass


@pytest.fixture()
def live_player(monkeypatch):
    """A FridayLive whose OutputStream is faked, so _play_audio() really runs."""
    types_mod = __import__("types")
    fake_sd = types_mod.ModuleType("sounddevice")
    fake_sd.RawOutputStream = _FakeStream
    fake_sd.InputStream = _FakeStream
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    # main.py imports optional action modules (playwright, ...) at construction.
    main = pytest.importorskip("main")
    monkeypatch.setattr(main, "sd", fake_sd)
    monkeypatch.setattr(main.FridayLive, "_build_tool_handlers",
                        lambda self: {}, raising=True)
    return main


@pytest.mark.parametrize("muted", [False, True])
def test_player_releases_speaking_when_the_turn_ends(live_player, muted):
    """The old loop never released while muted and never released between turns.

    On the live run this surfaced as the 25s watchdog "force-clearing" a flag
    that should have dropped as soon as playback finished, with audio ducking
    engaged the whole time (ducking is released on the SPEAKING transition,
    which never happened).
    """
    asyncio = __import__("asyncio")
    from unittest.mock import MagicMock

    main = live_player
    ui = MagicMock()
    ui.silent_mode = False
    ui.muted = muted

    async def scenario():
        live = main.FridayLive(ui)
        live.audio_in_queue = asyncio.Queue()
        task = asyncio.create_task(live._play_audio())
        await asyncio.sleep(0.02)
        try:
            for _ in range(3):
                live.audio_in_queue.put_nowait(b"\x00" * 1024)
                await asyncio.sleep(0.02)
            assert live._is_speaking, "must be speaking while chunks are playing"

            live._turn_complete = True      # server closed the turn
            deadline = asyncio.get_running_loop().time() + 2.0
            while live._is_speaking and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)
            assert not live._is_speaking, (
                "speaking never released -- the watchdog is doing the work")
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())
    assert ui.stop_speaking.call_count >= 1, (
        "set_speaking(False) must publish a release; otherwise ducking stays on")
