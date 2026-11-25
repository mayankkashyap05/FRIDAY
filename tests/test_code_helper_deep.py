"""100%-offline deep tests for code_helper's Gemini migration and safety gates."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from actions import code_helper as ch
from config.models import MODEL_FLASH


class FakeResponse:
    def __init__(self, text):
        self.text = text


@pytest.fixture(autouse=True)
def fake_generate(monkeypatch):
    calls = []

    def fake(model=None, contents=None, config=None):
        calls.append((model, contents, config))
        return FakeResponse("print('generated')\n")

    monkeypatch.setattr(ch, "generate_with_retry", fake)
    ch._UNDO_STACK.clear()
    return calls


def test_no_legacy_sdk_or_retired_models():
    source = Path(ch.__file__).read_text(encoding="utf-8")
    assert "google.generativeai" not in source
    assert "gemini-2.5" not in source and "gemini-2.0" not in source
    assert "MODEL_FLASH" in source and "generate_with_retry" in source


def test_generate_uses_flash_and_falls_back_to_lite(monkeypatch, fake_generate):
    assert ch._generate("prompt").text.strip() == "print('generated')"
    assert fake_generate[0][0] == MODEL_FLASH

    def quota(model, contents, config=None):
        if model == MODEL_FLASH:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return FakeResponse("fallback")
    monkeypatch.setattr(ch, "generate_with_retry", quota)
    assert ch._generate("prompt").text == "fallback"


def test_fenced_code_is_cleaned():
    assert ch._clean_code("```python\nprint(1)\n```") == "print(1)"


def test_syntax_gate_blocks_bad_python_before_run(tmp_path, monkeypatch):
    good = tmp_path / "good.py"
    bad = tmp_path / "bad.py"
    good.write_text("x = 1\n")
    bad.write_text("def x(:\n")
    assert ch._check_syntax(good, good.read_text()) == ""
    assert "SyntaxError" in ch._check_syntax(bad, bad.read_text())
    assert "SyntaxError" in ch._run_file(bad, [], 10)
    monkeypatch.setattr(ch.subprocess, "run", MagicMock(side_effect=AssertionError("must not run")))
    result = ch.code_helper({"action": "write", "description": "x", "output_path": str(good)})
    assert "Code written" in result
    assert ch._check_syntax(good, good.read_text()) == ""


def test_write_explain_and_unknown_action(tmp_path, fake_generate):
    result = ch.code_helper({"action": "write", "description": "hello",
                             "output_path": str(tmp_path / "x.py")})
    assert "Saved" in result
    assert (tmp_path / "x.py").read_text().strip() == "print('generated')"
    assert fake_generate[0][0] == MODEL_FLASH
    assert isinstance(ch.code_helper({"action": "explain", "code": "print(1)"}), str)
    assert "Unknown" in ch.code_helper({"action": "teleport"})


def test_atomic_edit_diff_backup_and_undo(tmp_path, fake_generate):
    path = tmp_path / "mod.py"
    path.write_text("a = 1\n")
    preview = ch.code_helper({"action": "edit", "file_path": str(path),
                              "description": "change a", "dry_run": "true"})
    assert "Dry run" in preview and "a = 1" in preview
    assert path.read_text() == "a = 1\n"
    result = ch.code_helper({"action": "edit", "file_path": str(path),
                             "description": "change a"})
    assert "Diff" in result and "Saved" in result
    assert path.read_text() == "print('generated')"
    assert Path(str(path) + ".friday.bak").read_text() == "a = 1\n"
    assert "Restored" in ch.code_helper({"action": "undo_edit", "file_path": str(path)})
    assert path.read_text() == "a = 1\n"


def test_run_file_multi_language_and_timeout(tmp_path, monkeypatch):
    def fake_run(argv, **kwargs):
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == 30
        return subprocess.CompletedProcess(argv, 0, stdout="hi", stderr="")
    monkeypatch.setattr(ch.subprocess, "run", fake_run)
    for name in ("script.py", "script.js", "script.ps1", "script.sh"):
        script = tmp_path / name
        script.write_text("ok\n")
        assert "hi" in ch._run_file(script, [], 30)
    assert "not found" in ch._run_file(tmp_path / "x.py", [], 30).lower() or "File not found" in ch._run_file(
        tmp_path / "x.py", [], 30)
    (tmp_path / "x.java").write_text("class X {}\n")
    assert "No interpreter" in ch._run_file(tmp_path / "x.java", [], 30)

    def boom(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)
    monkeypatch.setattr(ch.subprocess, "run", boom)
    assert "Timed out" in ch._run_file(tmp_path / "script.py", [], 5)


def test_build_syntax_repair_loop(tmp_path, monkeypatch, fake_generate):
    target = tmp_path / "built.py"
    def fake_generate_local(model=None, contents=None, config=None):
        prompt = contents if isinstance(contents, str) else ""
        text = "if True:\n" if "Fix the broken code" not in prompt else "print('ok')\n"
        return FakeResponse(text)
    monkeypatch.setattr(ch, "generate_with_retry", fake_generate_local)
    monkeypatch.setattr(ch, "_run_file", lambda *a, **k: "No output.")
    result = ch.code_helper({"action": "build", "description": "make",
                             "output_path": str(target)})
    assert "Build complete" in result
    assert target.read_text().strip() == "print('ok')"


def test_build_reports_failure_after_attempts(tmp_path, monkeypatch, fake_generate):
    monkeypatch.setattr(ch, "_run_file", lambda *a, **k: "Traceback: still broken")
    result = ch.code_helper({"action": "build", "description": "make",
                             "output_path": str(tmp_path / "b.py")})
    assert "Unable" in result and "Traceback" in result


def test_review_tests_and_screen_debug_offline(tmp_path, monkeypatch, fake_generate):
    path = tmp_path / "review.py"
    path.write_text("x = 1\n")
    assert "Review" in ch.code_helper({"action": "review", "file_path": str(path)})
    assert "pytest" in ch.code_helper({"action": "test", "file_path": str(path)}).lower() or "Generated tests" in ch.code_helper(
        {"action": "test", "file_path": str(path)})
    monkeypatch.setattr(ch, "_take_screenshot", lambda: None)
    assert "Could not take a screenshot" in ch.code_helper({"action": "screen_debug",
                                                            "description": "error"})


def test_screen_debug_with_fake_image(tmp_path, monkeypatch):
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(ch, "_take_screenshot", lambda: image)
    fake_part = MagicMock()
    monkeypatch.setattr(ch.genai_types, "Part", MagicMock(from_bytes=MagicMock(return_value=fake_part)),
                        raising=False)
    seen = {}

    def capture(model, contents, config=None):
        seen["contents"] = contents
        return FakeResponse("The error is a missing import; add it.")
    monkeypatch.setattr(ch, "generate_with_retry", capture)
    result = ch.code_helper({"action": "screen_debug", "description": "what is wrong?"})
    assert "missing import" in result
    assert seen["contents"][-1].startswith("Analyze this screenshot")
    assert not image.exists()


def test_intent_detection_contracts(tmp_path):
    assert ch._detect_intent("ekrandaki hatayı göster", "", "") == "screen_debug"
    assert ch._detect_intent("review this code", "x.py", "print(1)") == "review"
    assert ch._detect_intent("write tests", "x.py", "print(1)") == "generate_tests"
    assert ch._detect_intent("refactor this", "x.py", "print(1)") == "refactor"
    script = tmp_path / "x.py"
    script.write_text("print(1)\n")
    assert ch._detect_intent("run it", str(script), "") == "run"
    assert ch._detect_intent("build me a script", "", "") == "build"
    assert ch._detect_intent("hello world", "", "") == "write"


def test_error_detector_and_none_params(no_shell_history=None):
    assert ch._has_error("Traceback (most recent call last): ...")
    assert ch._has_error("ERROR: something failed")
    assert not ch._has_error("All tests passed")
    assert isinstance(ch.code_helper(None), str)
    assert isinstance(ch.code_helper({}), str)
