# actions/code_helper.py
# AI-powered code assistant — write, edit, review, test, run and debug safely.

from __future__ import annotations

import ast
import difflib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from api_keys import generate_with_retry
from config.models import MODEL_FLASH, MODEL_FLASH_LITE

try:
    from google.genai import types as genai_types
except Exception:                                    # optional until screen_debug
    genai_types = None


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
DESKTOP = Path.home() / "Desktop"
MAX_BUILD_ATTEMPTS = 3
MAX_OUTPUT = 8000
_UNDO_STACK: dict[str, Path] = {}


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")
    return bool(value) if value is not None else default


def _clamp(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _is_quota_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(token in message for token in ("429", "quota", "resource_exhausted"))


def _generate(prompt: str, contents=None, config=None):
    """Call Gemini through the shared retry client with a lite-tier fallback."""
    payload = contents if contents is not None else prompt
    options = config or {"temperature": 0.2}
    try:
        return generate_with_retry(model=MODEL_FLASH, contents=payload, config=options)
    except Exception as exc:
        if not _is_quota_error(exc):
            raise
        return generate_with_retry(model=MODEL_FLASH_LITE, contents=payload, config=options)


def _clean_code(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _resolve_save_path(output_path: str, language: str) -> Path:
    ext_map = {
        "python": ".py", "py": ".py", "javascript": ".js", "js": ".js",
        "typescript": ".ts", "ts": ".ts", "html": ".html", "css": ".css",
        "java": ".java", "cpp": ".cpp", "c": ".c", "bash": ".sh", "shell": ".sh",
        "powershell": ".ps1", "sql": ".sql", "json": ".json", "rust": ".rs", "go": ".go",
    }
    if output_path:
        path = Path(output_path).expanduser()
        return path if path.is_absolute() else DESKTOP / path
    return DESKTOP / ("friday_code" + ext_map.get((language or "python").lower(), ".py"))


def _read_file(file_path: str) -> tuple[str, str]:
    if not file_path:
        return "", "No file path provided."
    path = Path(file_path).expanduser()
    if not path.exists():
        return "", f"File not found: {file_path}"
    try:
        return path.read_text(encoding="utf-8"), ""
    except Exception as exc:
        return "", f"Could not read file: {exc}"


def _save_file(path: Path, content: str) -> str:
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except Exception:
                pass
            raise
        return f"Saved to: {path}"
    except Exception as exc:
        return f"Could not save: {exc}"


def _check_syntax(path: Path, code: str | None = None) -> str:
    """Return '' when the file is syntactically valid, else a precise message."""
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        source = code if code is not None else Path(path).read_text(encoding="utf-8", errors="replace")
        try:
            ast.parse(source, filename=str(path))
            return ""
        except SyntaxError as exc:
            return f"SyntaxError in {path} at line {exc.lineno}, column {exc.offset}: {exc.msg}"
        except Exception as exc:
            return f"Syntax check failed for {path}: {exc}"
    if suffix in (".js", ".mjs") and shutil.which("node"):
        try:
            result = subprocess.run(["node", "--check", str(path)],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                return f"JavaScript syntax error in {path}: {result.stderr[:400]}"
        except Exception:
            pass
    return ""


def _preview(code: str, lines: int = 10) -> str:
    all_lines = code.splitlines()
    preview = "\n".join(all_lines[:lines])
    suffix = f"\n... ({len(all_lines) - lines} more lines)" if len(all_lines) > lines else ""
    return preview + suffix


def _has_error(output: str) -> bool:
    signals = [
        r"\btraceback\b", r"\bsyntaxerror\b", r"\bnameerror\b", r"\btypeerror\b",
        r"\bvalueerror\b", r"\bkeyerror\b", r"\bimporterror\b", r"\bfilenotfounderror\b",
        r"\bcrash\b", r"\bfailed\b", r"\berror:\b", r"\bexception\b", r"\bassert\b",
    ]
    return any(re.search(pattern, output, re.IGNORECASE) for pattern in signals)


def _take_screenshot() -> Path | None:
    try:
        from actions.screen_processor import _capture_screenshot
        data = _capture_screenshot()
        if data:
            target = BASE_DIR / "data" / "screenshots" / f"debug_{int(time.time())}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return target
    except Exception:
        pass
    try:
        import pyautogui
        target = Path.home() / "Desktop" / f"friday_debug_{int(time.time())}.png"
        pyautogui.screenshot().save(str(target))
        return target
    except Exception as exc:
        print(f"[Code] ⚠️ Screenshot failed: {exc}")
        return None


def _image_to_base64(path: Path) -> str:
    import base64
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _detect_intent(description: str, file_path: str, code: str) -> str:
    desc = (description or "").lower()
    screen_kw = ["ekrandaki", "screen", "ekranda", "bu hatayı", "why am i getting",
                 "neden hata", "what's wrong", "ne yanlış", "screenshot", "görüntü"]
    if any(keyword in desc for keyword in screen_kw):
        return "screen_debug"
    if any(k in desc for k in ("review", "audit", "code review", "security check")):
        return "review"
    if any(k in desc for k in ("test", "pytest", "unit test")) and (file_path or code):
        return "generate_tests"
    optimize_kw = ["optimize", "refactor", "clean up", "improve", "temizle",
                   "iyileştir", "daha iyi", "make it better", "hızlandır"]
    if any(k in desc for k in optimize_kw) and (code or file_path):
        return "refactor" if "refactor" in desc else "optimize"
    if file_path:
        path = Path(file_path)
        edit_kw = ["edit", "update", "modify", "change", "add", "remove",
                   "refactor", "fix", "rename", "replace", "düzenle", "değiştir"]
        run_kw = ["run", "execute", "launch", "start", "çalıştır"]
        build_kw = ["build", "make it work", "try", "attempt"]
        if path.exists() and any(k in desc for k in edit_kw):
            return "edit"
        if path.exists() and any(k in desc for k in run_kw):
            return "run"
        if any(k in desc for k in build_kw):
            return "build"
        if path.exists():
            return "explain"
    explain_kw = ["explain", "what does", "describe", "analyze", "açıkla", "ne yapıyor"]
    if any(k in desc for k in explain_kw) and (code or file_path):
        return "explain"
    if any(k in desc for k in ("build", "make it work", "try and", "attempt")):
        return "build"
    return "write"


def _write(description: str, language: str, output_path: str, player=None) -> tuple[str, Path]:
    language = language or "python"
    prompt = f"""You are an expert {language} developer.
Write clean, working, well-commented {language} code for the description below.
Output ONLY the code — no explanation, no markdown, no backticks.

Description: {description}

Code:"""
    code = _clean_code(_generate(prompt).text)
    path = _resolve_save_path(output_path, language)
    _save_file(path, code)
    return code, path


def _fix_code(code: str, error_output: str, description: str) -> str:
    prompt = f"""You are an expert debugger. Fix the broken code below.
Return ONLY corrected code — no explanation, no markdown, no backticks.

Goal: {description}
Error:
{error_output[:2000]}

Broken code:
{code}

Fixed code:"""
    return _clean_code(_generate(prompt).text)


def _run_file(path: Path, args, timeout: int) -> str:
    path = Path(path).expanduser()
    if not path.is_file():
        return f"File not found: {path}"
    timeout = _clamp(timeout, 1, 120, 30)

    syntax_error = _check_syntax(path)
    if syntax_error:
        return syntax_error

    suffix = path.suffix.lower()
    if suffix == ".py":
        interpreter = [sys.executable]
    elif suffix in (".js", ".mjs"):
        interpreter = ["node"]
    elif suffix == ".ts":
        interpreter = ["tsx"] if shutil.which("tsx") else ["ts-node"]
    elif suffix == ".ps1":
        interpreter = ["powershell", "-File"]
    elif suffix == ".sh":
        interpreter = ["bash"]
    elif suffix == ".go":
        interpreter = ["go", "run"]
    elif suffix == ".rb":
        interpreter = ["ruby"]
    elif suffix == ".php":
        interpreter = ["php"]
    else:
        return f"No interpreter for {path.suffix}."

    if isinstance(args, str):
        arg_list = shlex.split(args) if args.strip() else []
    elif isinstance(args, (list, tuple)):
        arg_list = [str(arg) for arg in args]
    else:
        arg_list = []

    try:
        result = subprocess.run(
            interpreter + [str(path)] + arg_list,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(path.parent), shell=False,
        )
        parts = []
        if result.stdout.strip():
            parts.append("Output:\n" + result.stdout.strip()[:MAX_OUTPUT])
        if result.stderr.strip():
            parts.append("Stderr:\n" + result.stderr.strip()[:MAX_OUTPUT])
        return "\n\n".join(parts) if parts else "Executed with no output."
    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s."
    except FileNotFoundError:
        return f"Interpreter not found: {interpreter[0]}."
    except Exception as exc:
        return f"Execution error: {exc}"


def _build(description, language, output_path, args, timeout, speak=None, player=None) -> str:
    if not description:
        return "Please describe what you want me to build, sir."
    if player:
        player.write_log("[Code] Build started...")
    language = language or "python"
    try:
        code, path = _write(description, language, output_path, player)
        print(f"[Code] ✅ Written: {path}")
    except Exception as exc:
        message = f"Could not write initial code: {exc}"
        if speak:
            speak(message)
        return message

    last_output = ""
    for attempt in range(1, MAX_BUILD_ATTEMPTS + 1):
        print(f"[Code] 🔄 Attempt {attempt}/{MAX_BUILD_ATTEMPTS}")
        syntax_error = _check_syntax(path)
        if syntax_error:
            try:
                code = _fix_code(code, syntax_error, description)
                _save_file(path, code)
            except Exception as exc:
                return f"Could not fix syntax error: {exc}"
            syntax_error = _check_syntax(path, code)
            if syntax_error:
                return f"Syntax gate blocked execution: {syntax_error}"
        last_output = _run_file(path, args, timeout)
        if not _has_error(last_output):
            message = (f"Build complete, sir. The code is working after {attempt} "
                       f"attempt{'s' if attempt > 1 else ''}. Saved to {path}.")
            if speak:
                speak(message)
            return f"{message}\n\nOutput:\n{last_output}"
        try:
            code = _fix_code(code, last_output, description)
            _save_file(path, code)
        except Exception as exc:
            message = f"Could not fix code on attempt {attempt}: {exc}"
            if speak:
                speak(message)
            return message
    message = f"Unable to build a working version after {MAX_BUILD_ATTEMPTS} attempts, sir. Last error: {last_output[:200]}"
    if speak:
        speak(message)
    return f"{message}\n\nLast code saved to: {path}"


def _diff_summary(original: str, updated: str, path: Path) -> str:
    diff = list(difflib.unified_diff(
        original.splitlines(), updated.splitlines(),
        fromfile=str(path), tofile=str(path) + " (new)", lineterm=""))
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    preview = "\n".join(diff[:60])
    return f"Diff for {path}: +{added}/-{removed} lines\n{preview}"


def _apply_edit(file_path: str, updated: str, dry_run: bool = False) -> str:
    path = Path(file_path).expanduser().resolve()
    original, error = _read_file(str(path))
    if error:
        return error
    report = _diff_summary(original, updated, path)
    if dry_run:
        return "Dry run — no changes written.\n" + report
    backup = path.with_name(path.name + ".friday.bak")
    backup.write_text(original, encoding="utf-8")
    _UNDO_STACK[str(path)] = backup
    saved = _save_file(path, updated)
    return f"{saved}\n{report}"


def _undo_action(file_path: str) -> str:
    if not file_path:
        return "Please provide the file path to undo, sir."
    path = str(Path(file_path).expanduser().resolve())
    backup = _UNDO_STACK.get(path)
    if backup is None or not backup.exists():
        return f"No backup available for {path}."
    restored = backup.read_text(encoding="utf-8")
    _save_file(Path(path), restored)
    _UNDO_STACK.pop(path, None)
    return f"Restored previous version of {path}."


def _write_action(description, language, output_path, player) -> str:
    if not description:
        return "Please describe what you want me to write, sir."
    if player:
        player.write_log("[Code] Writing code...")
    try:
        code, path = _write(description, language, output_path, player)
        return f"Code written. Saved to: {path}\n\nPreview:\n{_preview(code)}"
    except Exception as exc:
        return f"Could not generate code: {exc}"


def _edit_action(file_path, instruction, player, dry_run=False) -> str:
    if not file_path:
        return "Please provide a file path to edit, sir."
    if not instruction:
        return "Please describe what change to make, sir."
    content, error = _read_file(file_path)
    if error:
        return error
    if player:
        player.write_log("[Code] Editing file...")
    prompt = f"""You are an expert code editor. Apply the requested change.
Return ONLY the complete updated code — no explanation, no markdown.

Change: {instruction}

Original code:
{content}

Updated code:"""
    try:
        updated = _clean_code(_generate(prompt).text)
    except Exception as exc:
        return f"Could not edit code: {exc}"
    return _apply_edit(file_path, updated, dry_run)


def _explain_action(file_path, code, player) -> str:
    if file_path and not code:
        code, error = _read_file(file_path)
        if error:
            return error
    if not code:
        return "Please provide code or a file path to explain, sir."
    if player:
        player.write_log("[Code] Analyzing code...")
    prompt = ("Explain what this code does in 3-6 clear sentences. "
              f"Code:\n{code[:4000]}\n\nExplanation:")
    try:
        return _generate(prompt).text.strip()
    except Exception as exc:
        return f"Could not explain code: {exc}"


def _run_action(file_path, args, timeout, player) -> str:
    if not file_path:
        return "Please provide a file path to run, sir."
    if player:
        player.write_log(f"[Code] Running {Path(file_path).name}...")
    return _run_file(Path(file_path).expanduser(), args, timeout)


def _optimize_action(file_path, code, language, output_path, player, dry_run=False,
                     instruction="") -> str:
    if file_path and not code:
        code, error = _read_file(file_path)
        if error:
            return error
    if not code:
        return "Please provide code or a file path to optimize, sir."
    if player:
        player.write_log("[Code] Optimizing code...")
    language = language or "python"
    prompt = f"""You are an expert {language} engineer.
{instruction or 'Optimize this code for performance, readability and best practices.'}
Return ONLY the optimized code — no explanation, no markdown.

Original code:
{code[:6000]}

Optimized code:"""
    try:
        updated = _clean_code(_generate(prompt).text)
    except Exception as exc:
        return f"Could not optimize code: {exc}"
    if file_path:
        return _apply_edit(file_path, updated, dry_run)
    target = _resolve_save_path(output_path, language)
    if dry_run:
        return "Dry run — no changes written.\n" + _diff_summary(code, updated, target)
    status = _save_file(target, updated)
    return f"Code optimized. {status}\n\n{_diff_summary(code, updated, target)}"


def _review_action(file_path, code, player) -> str:
    if file_path and not code:
        code, error = _read_file(file_path)
        if error:
            return error
    if not code:
        return "Please provide code or a file path to review, sir."
    local = []
    if file_path and Path(file_path).suffix.lower() == ".py":
        issue = _check_syntax(Path(file_path), code)
        local.append(issue or "Python AST: OK")
        try:
            result = subprocess.run([sys.executable, "-m", "pyflakes", str(file_path)],
                                    capture_output=True, text=True, timeout=15)
            local.append(result.stdout.strip() or "pyflakes: clean")
        except Exception:
            local.append("pyflakes: unavailable")
    prompt = ("Review this code for bugs, security risks, edge cases and complexity. "
              "Be concise and actionable.\n\n" + code[:6000])
    try:
        model_review = _generate(prompt).text.strip()
    except Exception as exc:
        model_review = f"Model review unavailable: {exc}"
    return "Local checks:\n" + "\n".join(local) + "\n\nReview:\n" + model_review


def _generate_tests_action(file_path, code, player, save: bool, run_tests: bool) -> str:
    if file_path and not code:
        code, error = _read_file(file_path)
        if error:
            return error
    if not code:
        return "Please provide a file path or code to generate tests, sir."
    prompt = ("Generate a concise pytest test module for this Python code. "
              "Output ONLY valid Python test code — no markdown.\n\n" + code[:6000])
    try:
        tests = _clean_code(_generate(prompt).text)
    except Exception as exc:
        return f"Could not generate tests: {exc}"
    if not save:
        return f"Generated tests:\n{_preview(tests, 40)}"
    source = Path(file_path) if file_path else BASE_DIR / "module.py"
    target_dir = BASE_DIR / "tests" if (BASE_DIR / "tests").is_dir() else source.parent
    target = target_dir / f"test_{source.stem}.py"
    status = _save_file(target, tests)
    result = f"{status}\n{_preview(tests, 20)}"
    if run_tests:
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", str(target)],
                capture_output=True, text=True, timeout=120)
            result += "\n\n" + (completed.stdout + completed.stderr)[-MAX_OUTPUT:]
        except Exception as exc:
            result += f"\nTest run failed: {exc}"
    return result


def _screen_debug_action(description, file_path, player, speak=None) -> str:
    if player:
        player.write_log("[Code] Capturing screen for analysis...")
    screenshot = _take_screenshot()
    if not screenshot:
        return "Could not take a screenshot, sir. Please check PyAutoGUI/mss."
    file_content = ""
    if file_path:
        file_content, _ = _read_file(file_path)
    try:
        from google.genai import types
        image_bytes = screenshot.read_bytes()
        context = "Related code:\n" + file_content[:4000] if file_content else ""
        question = description or "What error is visible and how do I fix it?"
        prompt = f"""Analyze this screenshot as an expert debugger.
Question: {question}
{context}
Identify the problem and give a concrete fix."""
        contents = [types.Part.from_bytes(data=image_bytes, mime_type="image/png"), prompt]
        analysis = _generate("", contents=contents).text.strip()
        try:
            screenshot.unlink()
        except Exception:
            pass
        if file_content:
            match = re.search(r"```[a-zA-Z]*\n(.*?)```", analysis, re.DOTALL)
            if match:
                _save_file(Path(file_path).expanduser(), match.group(1).strip())
                analysis += f"\n\n✅ Fixed code saved to: {file_path}"
        return analysis
    except Exception as exc:
        try:
            screenshot.unlink()
        except Exception:
            pass
        return f"Screen analysis failed: {exc}"


def code_helper(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None
) -> str:
    """Dispatched from main.py — all actions are parameter-driven."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action", "auto") or "auto").strip().lower().replace("-", "_")
    description = str(params.get("description", "") or "").strip()
    language = str(params.get("language", "python") or "python").strip()
    output_path = str(params.get("output_path", "") or "").strip()
    file_path = str(params.get("file_path", "") or "").strip()
    code = str(params.get("code", "") or "").strip()
    args = params.get("args", [])
    timeout = _clamp(params.get("timeout", 30), 1, 120, 30)
    dry_run = _as_bool(params.get("dry_run"), False)

    if action == "auto":
        action = _detect_intent(description, file_path, code)
        print(f"[Code] 🤖 Auto-detected: {action}")

    if action == "write":
        return _write_action(description, language, output_path, player)
    if action == "edit":
        return _edit_action(file_path, description or params.get("instruction", ""), player, dry_run)
    if action in ("explain",):
        return _explain_action(file_path, code, player)
    if action == "run":
        return _run_action(file_path, args, timeout, player)
    if action == "build":
        return _build(description, language, output_path, args, timeout, speak, player)
    if action in ("optimize", "refactor"):
        instruction = str(params.get("instruction", "") or "")
        if action == "refactor" and not instruction:
            instruction = "Refactor for clarity and maintainability without changing behaviour."
        return _optimize_action(file_path, code, language, output_path, player,
                                dry_run, instruction)
    if action == "undo_edit":
        return _undo_action(file_path or output_path)
    if action in ("review", "audit"):
        return _review_action(file_path, code, player)
    if action in ("test", "generate_tests"):
        return _generate_tests_action(file_path, code, player,
                                      _as_bool(params.get("save"), False),
                                      _as_bool(params.get("run_tests"), False))
    if action == "screen_debug":
        return _screen_debug_action(description, file_path, player, speak)
    return ("Unknown code_helper action. Use write, edit, explain, run, build, optimize, "
            "refactor, undo_edit, review, test, generate_tests or screen_debug.")
