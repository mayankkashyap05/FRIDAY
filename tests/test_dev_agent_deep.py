"""Offline deep tests for the dev_agent build/fix/run upgrade."""

import sys
from unittest.mock import MagicMock

import pytest

from actions import dev_agent as da


def test_no_legacy_gemini_import_or_hardcoded_model(tmp_path):
    source = (da.BASE_DIR / "actions" / "dev_agent.py").read_text(encoding="utf-8")
    assert "google.generativeai" not in source
    assert "gemini-2.5-flash" not in source
    assert "generate_with_retry(MODEL_FLASH" in source


def test_topological_sort_is_deterministic_and_cycles_fall_back():
    files = [
        {"path": "main.py", "imports": ["utils.helpers"]},
        {"path": "utils/helpers.py", "imports": []},
        {"path": "core/a.py", "imports": ["core.b"]},
        {"path": "core/b.py", "imports": ["core.a"]},
    ]
    order = [f["path"] for f in da._topological_order(files)]
    assert len(order) == 4 and len(set(order)) == 4
    assert order.index("utils/helpers.py") < order.index("main.py")
    assert set(order[-2:]) == {"core/a.py", "core/b.py"}
    assert order == [f["path"] for f in da._topological_order(list(reversed(files)))]


def test_plan_write_and_fix_use_shared_model(monkeypatch, tmp_path):
    prompts = []

    def fake_response(prompt):
        prompts.append(prompt)
        if "senior software architect" in prompt:
            return ('{"project_name":"demo","entry_point":"main.py",'
                    '"files":[{"path":"main.py","description":"entry","imports":[]}],'
                    '"run_command":"python main.py","dependencies":["requests"]}')
        if "debugger" in prompt:
            return "print('fixed')"
        return "print('hello')"

    monkeypatch.setattr(da, "_model_response", fake_response)
    plan = da._plan_project("print hello", "python")
    assert plan["entry_point"] == "main.py"

    code = da._write_file(
        {"path": "main.py", "description": "entry", "imports": []},
        "print hello", [{"path": "main.py", "description": "entry", "imports": []}],
        "python", tmp_path, {},
    )
    assert code == "print('hello')"
    assert (tmp_path / "main.py").read_text() == "print('hello')"

    fixed = da._fix_files(
        "Traceback (most recent call last):\n  File \"main.py\", line 1\nNameError: bad",
        "print hello", [{"path": "main.py", "description": "entry"}],
        {"main.py": code}, "python", tmp_path, "main.py",
    )
    assert fixed["main.py"] == "print('fixed')"
    assert len(prompts) == 3


def test_scaffolding_adds_package_markers_and_requirements(tmp_path):
    (tmp_path / "utils").mkdir()
    (tmp_path / "utils" / "helpers.py").write_text("x = 1\n")
    da._scaffold_project(tmp_path, [{"path": "utils/helpers.py"}], ["requests"])
    assert (tmp_path / "utils" / "__init__.py").exists()
    assert (tmp_path / "requirements.txt").read_text().strip() == "requests"


def test_run_project_rejects_dangerous_arguments_and_path_escapes(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(da.subprocess, "run",
                        lambda *a, **kw: calls.append((a[0], kw)) or MagicMock(
                            stdout="ok", stderr="", returncode=0))
    script = tmp_path / "main.py"
    script.write_text("print('ok')")

    assert "Blocked" in da._run_project("python -c 'print(1)'", tmp_path)
    assert "Blocked" in da._run_project("python -e evil", tmp_path)
    assert "Blocked" in da._run_project("python ../outside.py", tmp_path)
    assert "allowed" in da._run_project("node main.js", tmp_path)
    assert "ok" in da._run_project("python main.py", tmp_path)
    assert calls[-1][0][1] == str(script.resolve())
    assert "Blocked" in da._run_project("python -m pytest", tmp_path)
    assert "ok" in da._run_project("python -m pytest", tmp_path, run_tests=True)


def test_syntax_gate_feeds_fixer_before_running(monkeypatch, tmp_path):
    project = tmp_path / "projects"
    monkeypatch.setattr(da, "PROJECTS_DIR", project)
    plan = {
        "project_name": "demo", "entry_point": "main.py",
        "files": [{"path": "main.py", "description": "entry", "imports": []}],
        "run_command": "python main.py", "dependencies": [],
    }
    monkeypatch.setattr(da, "_plan_project", lambda desc, lang: plan)
    monkeypatch.setattr(da, "_write_file", lambda **kw: "def broken(:\n", raising=False)
    monkeypatch.setattr(da, "_install_dependencies", lambda *a: "ok")
    monkeypatch.setattr(da, "_open_vscode", lambda *a, **kw: True)
    events = []

    def fake_fix(**kwargs):
        events.append("fix")
        assert "SyntaxError" in kwargs["error_output"]
        (project / "demo" / "main.py").write_text("print('ok')")
        return {"main.py": "print('ok')"}

    def fake_run(*args, **kwargs):
        events.append("run")
        return "Ran with no output."

    monkeypatch.setattr(da, "_fix_files", fake_fix)
    monkeypatch.setattr(da, "_run_project", fake_run)
    result = da.dev_agent({"description": "demo project", "open_editor": "false"})
    assert events[:2] == ["fix", "run"]
    assert "working" in result


def test_open_vscode_is_gated_and_never_uses_shell(monkeypatch, tmp_path):
    launches = []
    monkeypatch.setattr(da.shutil, "which", lambda name: "/usr/bin/code" if name == "code" else None)
    monkeypatch.setattr(da.subprocess, "Popen",
                        lambda args, **kw: launches.append((args, kw)) or MagicMock())
    assert da._open_vscode(tmp_path, enabled=False) is False
    assert launches == []
    assert da._open_vscode(tmp_path, enabled=True) is True
    args, kwargs = launches[0]
    assert args == ["/usr/bin/code", str(tmp_path)]
    assert kwargs["shell"] is False


def test_dev_agent_actions_dispatch_offline(monkeypatch):
    calls = []
    monkeypatch.setattr(da, "_list_projects", lambda: "Friday projects:\n demo")
    monkeypatch.setattr(da, "_run_existing_project", lambda n, t, r: calls.append(("run", n, r)) or "ran")
    monkeypatch.setattr(da, "_fix_existing_project", lambda n, t, r: calls.append(("fix", n, r)) or "fixed")
    assert "demo" in da.dev_agent({"action": "list_projects"})
    assert da.dev_agent({"action": "run_project", "project_name": "demo", "run_tests": "true"}) == "ran"
    assert da.dev_agent({"action": "fix_project", "project_name": "demo"}) == "fixed"
    assert calls == [("run", "demo", True), ("fix", "demo", False)]
