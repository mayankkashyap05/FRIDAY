import ast
import heapq
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from api_keys import generate_with_retry
from config.models import MODEL_FLASH


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
API_CONFIG_PATH  = BASE_DIR / "config" / "api_keys.json"
MAX_FIX_ATTEMPTS = 5
MODEL_PLANNER    = MODEL_FLASH
MODEL_WRITER     = MODEL_FLASH


def _projects_dir() -> Path:
    """Projects live next to the real (possibly OneDrive-redirected) Desktop."""
    try:
        from actions.desktop import _get_desktop
        return Path(_get_desktop()) / "FridayProjects"
    except Exception:
        return Path.home() / "Desktop" / "FridayProjects"


PROJECTS_DIR = _projects_dir()
METADATA_NAME = "friday_project.json"


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")
    return bool(value) if value is not None else default


def _model_response(prompt: str) -> str:
    """One Gemini call through the shared, key-rotating, retrying client."""
    response = generate_with_retry(MODEL_FLASH, prompt, config={"temperature": 0.2})
    return str(getattr(response, "text", "") or "")


def _safe_project_path(project_dir: Path, rel_path: str) -> Path | None:
    """Resolve a project-relative path and refuse any escape (.., absolute,
    symlink) outside project_dir."""
    try:
        project_root = project_dir.resolve()
        full = (project_root / rel_path).resolve()
        if full == project_root or project_root in full.parents:
            return full
    except Exception:
        pass
    print(f"[DevAgent] ⛔ Blocked path escape: {rel_path}")
    return None


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text.strip()


def _clean_file_path(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("./").strip()


def _topological_order(files: list[dict]) -> list[dict]:
    """Deterministic Kahn sort of plan files; cycles fall back to alpha order."""
    by_key = {_clean_file_path(f.get("path", "")): f for f in files
              if _clean_file_path(f.get("path", ""))}
    keys = sorted(by_key)
    deps_for: dict[str, set[str]] = {key: set() for key in keys}
    for key in keys:
        for imported in by_key[key].get("imports", []) or []:
            dotted = str(imported).replace("/", ".").removesuffix(".py")
            candidates = (
                dotted.replace(".", "/") + ".py",
                dotted.replace(".", "/") + "/__init__.py",
                _clean_file_path(dotted),
            )
            for candidate in candidates:
                dep = _clean_file_path(candidate)
                if dep in by_key and dep != key:
                    deps_for[key].add(dep)
                    break
    dependents: dict[str, list[str]] = {key: [] for key in keys}
    for key, deps in deps_for.items():
        for dep in deps:
            dependents[dep].append(key)
    indegree = {key: len(deps_for[key]) for key in keys}  # dependencies first

    ready = [key for key in keys if indegree[key] == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        key = heapq.heappop(ready)
        ordered.append(by_key[key])
        for candidate in dependents[key]:
            indegree[candidate] -= 1
            if indegree[candidate] == 0:
                heapq.heappush(ready, candidate)
    if len(ordered) < len(keys):
        seen = {_clean_file_path(f.get("path", "")) for f in ordered}
        ordered.extend(by_key[key] for key in keys if key not in seen)
    return ordered


def _scaffold_project(project_dir: Path, files: list[dict], dependencies: list[str]) -> None:
    """Add package markers and a requirements file so generated code imports cleanly."""
    for file_info in files:
        rel = _clean_file_path(file_info.get("path", ""))
        if "/" not in rel:
            continue
        parent = project_dir / rel.rsplit("/", 1)[0]
        marker = parent / "__init__.py"
        if parent.is_dir() and not marker.exists():
            marker.write_text('"""Generated package."""\n', encoding="utf-8")
    if dependencies:
        requirements = project_dir / "requirements.txt"
        if not requirements.exists():
            requirements.write_text(
                "\n".join(str(dep) for dep in dependencies) + "\n", encoding="utf-8")


def _save_metadata(project_dir: Path, payload: dict) -> None:
    try:
        target = project_dir / METADATA_NAME
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    except Exception as e:
        print(f"[DevAgent] ⚠️ Could not save metadata: {e}")


def _load_metadata(project_dir: Path) -> dict:
    try:
        return json.loads((project_dir / METADATA_NAME).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_rate_limit(error: Exception) -> bool:
    msg = str(error).lower()
    return "429" in msg or "quota" in msg or "resource_exhausted" in msg


def _parse_traceback(output: str, project_files: list[str]) -> tuple[str | None, int | None]:

    pattern = re.compile(r'File ["\']([^"\']+\.py)["\'],\s+line\s+(\d+)', re.IGNORECASE)
    matches = pattern.findall(output)

    for raw_path, line_str in reversed(matches):
        raw_name = Path(raw_path).name
        for pf in project_files:
            if Path(pf).name == raw_name or pf == raw_path or raw_path.endswith(pf):
                return pf, int(line_str)

    return None, None


def _classify_error(output: str) -> str:

    low = output.lower()

    if any(x in low for x in ("no module named", "modulenotfounderror", "importerror")):
        return "dependency_error"

    if "syntaxerror" in low or "invalid syntax" in low:
        return "syntax_error"
    
    if "cannot import" in low or "importerror" in low:
        return "import_error"

    if any(x in low for x in (
        "traceback", "exception", "error:", "nameerror", "typeerror",
        "attributeerror", "valueerror", "keyerror", "indexerror",
        "zerodivisionerror", "filenotfounderror", "permissionerror",
    )):
        return "runtime_error"

    return "none"


def _has_error(output: str, run_command: str) -> bool:
    
    low = output.lower()

    if "timed out" in low:
        return False

    if not output.strip():
        return False

    error_type = _classify_error(output)
    return error_type != "none"

class RateLimitError(Exception):
    pass


def _plan_project(description: str, language: str) -> dict:
    prompt = f"""You are a senior software architect. Create a minimal, complete file plan for this project.

Language: {language}
Description: {description}

Return ONLY valid JSON — no markdown, no explanation:
{{
  "project_name": "snake_case_name",
  "entry_point": "main.py",
  "files": [
    {{
      "path": "main.py",
      "description": "Entry point — what it does and which modules it imports",
      "imports": ["utils.helpers", "core.engine"]
    }},
    {{
      "path": "utils/helpers.py",
      "description": "Helper utilities — what functions it exposes",
      "imports": []
    }}
  ],
  "run_command": "python main.py",
  "dependencies": ["requests"]
}}

Critical rules:
1. List files in DEPENDENCY ORDER — files with no imports come first, entry point comes last.
2. The "imports" field must list every other project module this file imports (dot-notation, e.g. "utils.helpers").
3. Keep it minimal — only files truly needed.
4. Entry point must be in the files list.
5. Use relative paths only (e.g. "utils/helpers.py", not absolute paths).
6. Standard library modules (os, sys, json, etc.) do NOT go in "dependencies".

JSON:"""

    raw = ""
    try:
        raw = _strip_fences(_model_response(prompt))
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned invalid JSON: {e}\nRaw: {raw[:300]}")
    except Exception as e:
        if _is_rate_limit(e):
            raise RateLimitError(str(e))
        raise

def _write_file(
    file_info: dict,
    project_description: str,
    all_files: list[dict],
    language: str,
    project_dir: Path,
    already_written: dict[str, str],
) -> str:
    file_path = file_info["path"]
    file_desc = file_info.get("description", "")
    file_imports = file_info.get("imports", [])

    file_list = "\n".join(
        f"  [{i+1}] {f['path']}: {f.get('description', '')}"
        for i, f in enumerate(all_files)
    )

    dependency_context = ""
    for dep_dotted in file_imports:
        dep_path = dep_dotted.replace(".", "/") + ".py"
        if dep_path in already_written:
            code_snippet = already_written[dep_path][:2000]
            dependency_context += f"\n\n--- {dep_path} (you must import from this) ---\n{code_snippet}"

    lang_rules = ""
    if language.lower() == "python":
        lang_rules = """
Python-specific rules:
- Use type hints for all function signatures.
- Add docstrings for all public functions and classes.
- Use if __name__ == "__main__": guard in the entry point.
- For relative imports within the project, use: from utils.helpers import foo  (match the project structure exactly).
- Do NOT use implicit relative imports (from . import ...) unless it's a proper package with __init__.py.
- If this is a package subdirectory, create __init__.py files where needed."""
    elif language.lower() in ("javascript", "typescript", "js", "ts"):
        lang_rules = """
JS/TS-specific rules:
- Use ES modules (import/export), not CommonJS (require).
- Add JSDoc comments for all exported functions.
- Handle promise rejections with try/catch in async functions."""

    prompt = f"""You are a senior {language} developer writing production-quality code for a real project.

Project goal: {project_description}

Complete project file structure (in dependency order):
{file_list}

{f"Dependencies this file must import from other project files:{dependency_context}" if dependency_context else ""}

Your task: Write the complete, working code for: {file_path}
Purpose of this file: {file_desc}
{f"This file imports from: {', '.join(file_imports)}" if file_imports else "This file has no project-internal imports."}

{lang_rules}

General rules:
- Output ONLY raw code. Absolutely no explanation, no markdown, no triple backticks.
- Write COMPLETE, RUNNABLE code — no placeholders, no "# TODO", no "pass" stubs.
- Every import must either be from the standard library, listed dependencies, or the project files shown above.
- Match import paths EXACTLY to the file paths in the project structure (e.g. if file is "utils/helpers.py", import as "from utils.helpers import ...").
- Use proper error handling (try/except) where I/O or network calls are made.
- The code must work correctly when the project entry point is run from the project root directory.

Code for {file_path}:"""

    try:
        code = _strip_fences(_model_response(prompt))

        full_path = _safe_project_path(project_dir, file_path)
        if full_path is None:
            return None
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(code, encoding="utf-8")

        print(f"[DevAgent] ✅ Written: {file_path} ({len(code)} chars)")
        return code

    except Exception as e:
        if _is_rate_limit(e):
            raise RateLimitError(str(e))
        raise

def _install_dependencies(dependencies: list[str], project_dir: Path) -> str:
    if not dependencies:
        return "No external dependencies."

    to_install = []
    for dep in dependencies:
        pkg_name = re.split(r"[>=<!]", dep)[0].strip()
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", pkg_name],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            to_install.append(dep)
        else:
            print(f"[DevAgent] ✓ Already installed: {pkg_name}")

    if not to_install:
        return f"All dependencies already installed: {', '.join(dependencies)}"

    print(f"[DevAgent] 📦 Installing: {to_install}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install"] + to_install,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120, cwd=str(project_dir)
        )
        if result.returncode == 0:
            return f"Installed: {', '.join(to_install)}"
        return f"Install warning (non-fatal): {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return "Dependency install timed out (non-fatal)."
    except Exception as e:
        return f"Install error (non-fatal): {e}"

def _open_vscode(project_dir: Path, enabled: bool = True) -> bool:
    """Open the project in VS Code only when enabled; never use shell=True."""
    if not enabled:
        return False
    candidates = []
    found = shutil.which("code") or shutil.which("code.cmd")
    if found:
        candidates.append(found)
    candidates.extend([
        rf"C:\Users\{Path.home().name}\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd",
        r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
    ])
    for cmd in candidates:
        if cmd not in (found,) and not Path(cmd).exists():
            continue
        try:
            subprocess.Popen(
                [cmd, str(project_dir)], shell=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print(f"[DevAgent] 💻 VSCode opened: {project_dir}")
            return True
        except Exception:
            continue
    return False

def _run_project(run_command: str, project_dir: Path, timeout: int = 30,
                 run_tests: bool = False) -> str:
    """Run a generated Python script after strict argument/path validation."""
    print(f"[DevAgent] 🚀 Running: {run_command}")
    try:
        parts = shlex.split(str(run_command or ""))
        if not parts:
            return "Empty run command."
        if parts[0].lower() not in ("python", "py"):
            return "Only `python <script>` run commands are allowed, sir."
        if any(re.match(r"^-[ceCExX](?:$|[^a-zA-Z])", arg) for arg in parts[1:]):
            return "Blocked run command: -c/-e execution is not allowed."

        root = project_dir.resolve()
        if len(parts) > 1 and parts[1] == "-m":
            module = parts[2] if len(parts) > 2 else ""
            if not run_tests or module.split(".")[0] not in ("pytest", "unittest"):
                return "Blocked run command: only python <script> is allowed."
            parts = [sys.executable, "-m", module] + parts[3:]
        else:
            if len(parts) < 2:
                return "No script specified in run command."
            script = parts[1]
            resolved = _safe_project_path(root, script)
            if resolved is None or not resolved.is_file() or resolved.suffix != ".py":
                return "Blocked run command: script must be a .py file inside the project."
            # Rebase onto this interpreter without allowing injected flags.
            parts = [sys.executable, str(resolved)] + parts[2:]

        result = subprocess.run(
            parts,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
            cwd=str(project_dir)
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        combined_parts = []
        if stdout:
            combined_parts.append(f"STDOUT:\n{stdout}")
        if stderr:
            combined_parts.append(f"STDERR:\n{stderr}")

        return "\n\n".join(combined_parts) if combined_parts else "Ran with no output."

    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s — long-running app (server/GUI) is likely working."
    except FileNotFoundError as e:
        return f"Command not found: {e}"
    except Exception as e:
        return f"Run error: {e}"

def _syntax_errors(file_codes: dict[str, str], language: str) -> list[tuple[str, int, str]]:
    """Parse generated Python files before any subprocess is started."""
    if str(language).lower() not in ("python", "py"):
        return []
    problems = []
    for path, code in file_codes.items():
        try:
            ast.parse(code or "", filename=str(path))
        except SyntaxError as exc:
            problems.append((str(path), int(exc.lineno or 1), str(exc.msg)))
        except Exception as exc:
            problems.append((str(path), 1, str(exc)))
    return problems


def _syntax_error_output(problems: list[tuple[str, int, str]]) -> str:
    return "\n".join(
        f'  File "{path}", line {line}\nSyntaxError: {message}'
        for path, line, message in problems
    )


def _try_auto_install(error_output: str, project_dir: Path) -> bool:
    """ModuleNotFoundError varsa eksik paketi otomatik kurmaya çalışır."""
    pattern = re.compile(
        r"No module named ['\"]([a-zA-Z0-9_\-\.]+)['\"]", re.IGNORECASE
    )
    match = pattern.search(error_output)
    if not match:
        return False

    pkg = match.group(1).replace("_", "-").split(".")[0]
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,64}$", pkg):
        return False
    print(f"[DevAgent] 🔧 Auto-installing missing package: {pkg}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=60, cwd=str(project_dir)
        )
        return result.returncode == 0
    except Exception:
        return False

def _fix_files(
    error_output: str,
    project_description: str,
    all_files: list[dict],
    file_codes: dict[str, str],
    language: str,
    project_dir: Path,
    entry_point: str,
) -> dict[str, str]:

    error_file, error_line = _parse_traceback(error_output, list(file_codes.keys()))
    error_type = _classify_error(error_output)

    files_to_fix: list[str] = []

    if error_file:
        files_to_fix.append(error_file)
        if error_type == "import_error":
            for fi in all_files:
                if error_file.replace("/", ".").replace(".py", "") in fi.get("imports", []):
                    p = fi["path"]
                    if p not in files_to_fix:
                        files_to_fix.append(p)
    else:
        files_to_fix.append(entry_point)

    updated_codes: dict[str, str] = {}

    for fix_path in files_to_fix:
        current_code = file_codes.get(fix_path, "")

        other_ctx = ""
        for fp, code in file_codes.items():
            if fp != fix_path and code:
                snippet = code[:1500] + ("..." if len(code) > 1500 else "")
                other_ctx += f"\n--- {fp} ---\n{snippet}\n"

        line_hint = f"\nError appears to be near line {error_line} in this file." if (
            error_line and fix_path == error_file
        ) else ""

        prompt = f"""You are an expert {language} debugger. Fix the broken file below.

Project goal: {project_description}

All project files:
{chr(10).join(f"  - {f['path']}: {f.get('description', '')}" for f in all_files)}

Other files for context (read-only — fix only the target file):
{other_ctx[:3500]}

File to fix: {fix_path}{line_hint}
Error type: {error_type}

Error output:
{error_output[:2500]}

Current (broken) code:
{current_code}

Rules:
- Output ONLY the complete fixed code. No explanation, no markdown, no backticks.
- Fix ALL errors visible in the error output.
- Keep all existing correct logic — do not remove working features.
- Ensure import paths match the actual project file structure exactly.
- Do NOT introduce new bugs or remove error handling.

Fixed code for {fix_path}:"""

        try:
            fixed = _strip_fences(_model_response(prompt))

            full_path = _safe_project_path(project_dir, fix_path)
            if full_path is None:
                continue
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(fixed, encoding="utf-8")

            updated_codes[fix_path] = fixed
            print(f"[DevAgent] 🔧 Fixed: {fix_path}")

        except Exception as e:
            if _is_rate_limit(e):
                raise RateLimitError(str(e))
            print(f"[DevAgent] ⚠️ Could not fix {fix_path}: {e}")

    return updated_codes

def _build_project(
    description: str,
    language: str,
    project_name: str,
    timeout: int,
    speak=None,
    player=None,
    open_editor: bool = True,
    run_tests: bool = False,
) -> str:

    def log(msg: str):
        print(f"[DevAgent] {msg}")
        if player:
            player.write_log(f"[DevAgent] {msg}")

    log("Planning project structure...")
    try:
        plan = _plan_project(description, language)
    except RateLimitError:
        msg = "Rate limit reached, sir. Please try again in a moment."
        if speak: speak(msg)
        return msg
    except ValueError as e:
        msg = f"Planning failed: {e}"
        if speak: speak(msg)
        return msg

    proj_name    = project_name or plan.get("project_name", "friday_project")
    proj_name    = re.sub(r"[^\w\-]", "_", proj_name)
    project_dir  = PROJECTS_DIR / proj_name
    project_dir.mkdir(parents=True, exist_ok=True)

    files        = plan.get("files", [])
    entry_point  = plan.get("entry_point", "main.py")
    run_command  = plan.get("run_command", f"python {entry_point}")
    dependencies = plan.get("dependencies", [])

    log(f"Project: {proj_name} | Files: {len(files)} | Entry: {entry_point}")

    sorted_files = _topological_order(files)

    file_codes: dict[str, str] = {}

    for file_info in sorted_files:
        file_path = file_info.get("path", "")
        if not file_path:
            continue

        log(f"Writing {file_path}...")
        for attempt in range(2):
            try:
                code = _write_file(
                    file_info=file_info,
                    project_description=description,
                    all_files=files,
                    language=language,
                    project_dir=project_dir,
                    already_written=file_codes,
                )
                if code is not None:
                    file_codes[file_path] = code
                time.sleep(0.4)
                break
            except RateLimitError:
                if attempt == 0:
                    log("Rate limit — waiting 20s...")
                    time.sleep(20)
                else:
                    log(f"Rate limit retry failed for {file_path}, skipping.")
            except Exception as e:
                log(f"Failed to write {file_path}: {e}")
                break

    if not file_codes:
        msg = "I could not write any project files, sir."
        if speak: speak(msg)
        return msg

    _scaffold_project(project_dir, files, dependencies or [])
    _save_metadata(project_dir, {
        "description": description,
        "language": language,
        "project_name": proj_name,
        "entry_point": entry_point,
        "run_command": run_command,
        "dependencies": dependencies or [],
        "files": files,
    })

    if dependencies:
        install_result = _install_dependencies(dependencies, project_dir)
        log(install_result)

    _open_vscode(project_dir, enabled=open_editor)

    # Syntax gate: never start a subprocess for code that cannot even parse.
    problems = _syntax_errors(file_codes, language)
    if problems:
        log("Syntax errors detected before run — asking the fixer...")
        try:
            updated = _fix_files(
                error_output=_syntax_error_output(problems),
                project_description=description,
                all_files=files,
                file_codes=file_codes,
                language=language,
                project_dir=project_dir,
                entry_point=entry_point,
            )
            file_codes.update(updated)
        except Exception as e:
            log(f"Syntax fix step failed: {e}")
        problems = _syntax_errors(file_codes, language)
        if problems:
            msg = (f"Generated code still has syntax errors in "
                   f"{', '.join(p for p, _, _ in problems[:3])}, sir. "
                   f"Project saved at {project_dir}.")
            if speak: speak(msg)
            return msg

    last_output   = ""
    auto_installs = 0

    for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
        log(f"Running project (attempt {attempt}/{MAX_FIX_ATTEMPTS})...")
        last_output = _run_project(run_command, project_dir, timeout, run_tests=run_tests)
        log(f"Output preview: {last_output[:150]}")

        if not _has_error(last_output, run_command):
            msg = (
                f"Project '{proj_name}' is working, sir. "
                f"Built in {attempt} attempt{'s' if attempt > 1 else ''}. "
                f"Saved to: {project_dir}"
            )
            if speak: speak(msg)
            return f"{msg}\n\nOutput:\n{last_output}"

        if attempt == MAX_FIX_ATTEMPTS:
            break

        error_type = _classify_error(last_output)
        if error_type == "dependency_error" and auto_installs < 3:
            installed = _try_auto_install(last_output, project_dir)
            if installed:
                auto_installs += 1
                log("Missing dependency installed, retrying...")
                time.sleep(1)
                continue

        log(f"Fixing errors (type: {error_type})...")
        try:
            updated = _fix_files(
                error_output=last_output,
                project_description=description,
                all_files=files,
                file_codes=file_codes,
                language=language,
                project_dir=project_dir,
                entry_point=entry_point,
            )
            file_codes.update(updated)
            time.sleep(1)
        except RateLimitError:
            msg = "Rate limit reached during fix. Project saved, check it manually in VSCode."
            if speak: speak(msg)
            return msg
        except Exception as e:
            log(f"Fix step failed: {e}")

    msg = (
        f"I couldn't fully fix '{proj_name}' after {MAX_FIX_ATTEMPTS} attempts, sir. "
        f"Project is saved at {project_dir} — open it in VSCode and check manually."
    )
    if speak: speak(msg)
    return f"{msg}\n\nLast error:\n{last_output[:600]}"

def _project_dir_for(name: str) -> Path | None:
    clean = re.sub(r"[^A-Za-z0-9_\-]", "_", str(name or "").strip())
    if not clean:
        return None
    root = PROJECTS_DIR.resolve()
    candidate = (root / clean).resolve()
    return candidate if candidate != root and root in candidate.parents else None


def _list_projects() -> str:
    try:
        if not PROJECTS_DIR.exists():
            return "No Friday projects have been built yet, sir."
        rows = []
        for folder in sorted(p for p in PROJECTS_DIR.iterdir() if p.is_dir()):
            meta = _load_metadata(folder)
            rows.append(
                f"{folder.name}: {meta.get('description', '(no description)')}"
                f" [{meta.get('language', 'python')}, "
                f"{len(meta.get('files', []))} files]"
            )
        return "Friday projects:\n" + "\n".join(rows) if rows else "No Friday projects yet, sir."
    except Exception as e:
        return f"Could not list projects: {e}"


def _run_existing_project(name: str, timeout: int, run_tests: bool = False) -> str:
    root = _project_dir_for(name)
    if root is None or not root.is_dir():
        return f"Project not found: {name}"
    meta = _load_metadata(root)
    if not meta:
        return f"'{name}' is not a Friday project (missing {METADATA_NAME})."
    return _run_project(meta.get("run_command", "python main.py"), root,
                        timeout, run_tests=run_tests)


def _fix_existing_project(name: str, timeout: int, run_tests: bool = False) -> str:
    root = _project_dir_for(name)
    if root is None or not root.is_dir():
        return f"Project not found: {name}"
    meta = _load_metadata(root)
    if not meta:
        return f"'{name}' is not a Friday project (missing {METADATA_NAME})."
    files = meta.get("files", [])
    codes = {}
    for info in files:
        rel = _clean_file_path(info.get("path", ""))
        target = _safe_project_path(root, rel)
        if target and target.is_file():
            codes[rel] = target.read_text(encoding="utf-8", errors="replace")
    output = _run_project(meta.get("run_command", "python main.py"), root,
                          timeout, run_tests=run_tests)
    if not _has_error(output, meta.get("run_command", "")):
        return f"Project '{root.name}' runs cleanly.\n\n{output}"
    problems = _syntax_errors(codes, meta.get("language", "python"))
    if problems:
        output = _syntax_error_output(problems) + "\n" + output
    try:
        updated = _fix_files(
            error_output=output,
            project_description=meta.get("description", root.name),
            all_files=files,
            file_codes=codes,
            language=meta.get("language", "python"),
            project_dir=root,
            entry_point=meta.get("entry_point", "main.py"),
        )
        return f"Fixed {len(updated)} file(s) in '{root.name}'." if updated else f"No fix was applied to '{root.name}'."
    except Exception as e:
        return f"Fix failed: {e}"


def dev_agent(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    p = parameters if isinstance(parameters, dict) else {}
    action = str(p.get("action", "build") or "build").strip().lower().replace("-", "_")
    description  = str(p.get("description", "") or "").strip()
    language     = str(p.get("language", "python") or "python").strip()
    project_name = str(p.get("project_name", "") or "").strip()
    try:
        timeout = max(1, int(p.get("timeout", 30)))
    except (TypeError, ValueError):
        timeout = 30

    if action in ("list", "list_projects", "projects"):
        return _list_projects()

    if action in ("run", "run_project"):
        target = project_name or str(p.get("name", "") or "").strip()
        if not target:
            return "Which project should I run, sir?"
        return _run_existing_project(target, timeout, _as_bool(p.get("run_tests"), False))

    if action in ("fix", "fix_project"):
        target = project_name or str(p.get("name", "") or "").strip()
        if not target:
            return "Which project should I fix, sir?"
        return _fix_existing_project(target, timeout, _as_bool(p.get("run_tests"), False))

    if action not in ("build", "create", "new"):
        description = description or action.replace("_", " ")
        action = "build"

    if not description:
        return "Please describe the project you want me to build, sir."

    return _build_project(
        description  = description,
        language     = language,
        project_name = project_name,
        timeout      = timeout,
        speak        = speak,
        player       = player,
        open_editor  = _as_bool(p.get("open_editor"), True),
        run_tests    = _as_bool(p.get("run_tests"), False),
    )
