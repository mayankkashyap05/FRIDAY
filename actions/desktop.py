# actions/desktop.py
# AI-powered desktop & wallpaper management
#
# Flow for unknown tasks:
#   User request → Gemini generates Python/pyautogui code → Safety check → Execute
#
# Built-in: wallpaper change, icon arrangement, desktop cleanup, organize by type

import os
import re
import sys
import json
import shutil
import subprocess
import ctypes
from pathlib import Path
from datetime import datetime


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_api_key() -> str:
    from api_keys import next_key
    return next_key()


def _get_desktop() -> Path:
    """Locate the real Desktop, honouring a redirected OneDrive folder."""
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            )
            value, _ = winreg.QueryValueEx(key, "Desktop")
            if value:
                candidate = Path(os.path.expandvars(str(value))).expanduser()
                if candidate.is_dir():
                    return candidate
        except Exception:
            pass
    desktop = Path.home() / "Desktop"
    try:
        desktop.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return desktop


def _data_dir(name: str = "wallpapers") -> Path:
    path = BASE_DIR / "data" / name
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return path


def _atomic_write_json(path: Path, payload) -> bool:
    """Write JSON via a temp file + os.replace so readers see all-or-nothing."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"[Desktop] ⚠️ Could not write {path.name}: {e}")
        return False


BLOCKED_KEYWORDS = [
    "os.remove", "shutil.rmtree", "shutil.rm",
    "subprocess.run", "subprocess.Popen", "subprocess.call",
    "os.system", "exec(", "eval(",
    "import os", "import subprocess",
    "__import__", "open(",
    "sys.exit", "quit()",
    ".unlink(", "write_text", "write_bytes",
    "ctypes", "windll", "winreg", "os.startfile",
    "__class__", "__subclasses__", "__globals__", "__builtins__",
    "__base__", "__bases__", "__mro__", "globals()", "locals()",
]


def _is_safe_code(code: str) -> tuple[bool, str]:
    # Normalize so spaced/obfuscated forms can't dodge the keyword filter.
    code_lower = re.sub(r"\s+", "", code).lower()
    for keyword in BLOCKED_KEYWORDS:
        if keyword.lower() in code_lower:
            return False, f"Blocked operation: '{keyword}'"
    return True, "OK"


def _execute_generated_code(code: str) -> str:
    """Safely executes desktop automation code."""
    safe, reason = _is_safe_code(code)
    if not safe:
        return f"⛔ Blocked for safety: {reason}"

    try:
        import pyautogui
    except ImportError:
        return "pyautogui is not installed, sir."

    allowed_globals = {
        "pyautogui": pyautogui,
        "Path": Path,
        "shutil": shutil,
        "time": __import__("time"),
        "os": type("os", (), {
            "path": os.path,
            "listdir": os.listdir,
            "getcwd": os.getcwd,
        })(),
        "__builtins__": {
            "print": print,
            "len": len,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "list": list,
            "dict": dict,
            "range": range,
            "enumerate": enumerate,
            "sorted": sorted,
            "isinstance": isinstance,
            "hasattr": hasattr,
            "getattr": getattr,
            "max": max,
            "min": min,
            "sum": sum,
        }
    }

    output_lines = []
    allowed_globals["print"] = lambda *args: output_lines.append(" ".join(str(a) for a in args))

    try:
        exec(code, allowed_globals)
        return "\n".join(output_lines) if output_lines else "Task completed successfully."
    except Exception as e:
        return f"Execution error: {e}\n\nCode attempted:\n{code[:200]}"


def _handle_desktop_task(task: str) -> str:
    """Safely dispatches high-level desktop instructions to safe built-in handlers without exec()."""
    t = task.lower().strip()
    if any(k in t for k in ("wallpaper", "background")):
        if "undo" in t or "previous" in t or "restore" in t:
            return undo_wallpaper()
        m = re.search(r'(https?://\S+)', task)
        if m:
            return set_wallpaper_from_web(m.group(1))
        m_path = re.search(r'["\']([^"\']+\.(?:jpg|jpeg|png|bmp))["\']', task, re.I)
        if m_path:
            return set_wallpaper(m_path.group(1))
        if "what" in t or "current" in t or "get" in t:
            return get_current_wallpaper()
        return "Please specify the image path or URL for the wallpaper."

    if any(k in t for k in ("organize", "sort", "group")):
        mode = "by_date" if "date" in t or "time" in t else "by_type"
        return organize_desktop(mode=mode)

    if any(k in t for k in ("clean", "clear", "tidy")):
        return clean_desktop()

    if any(k in t for k in ("list", "files", "show", "what's on")):
        return list_desktop()

    if any(k in t for k in ("stat", "space", "size", "count")):
        return get_desktop_stats()

    return (
        "Desktop control supports: wallpaper, current_wallpaper, organize, clean, list, and stats. "
        "For direct mouse or keyboard automation, use computer_control."
    )

_WALLPAPER_STYLES = {
    "fill": (10, "0"), "fit": (6, "0"), "stretch": (2, "0"),
    "tile": (0, "1"), "center": (0, "0"), "span": (22, "0"),
}
_WALLPAPER_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif", ".tif", ".tiff"}


def _wallpaper_history_file() -> Path:
    return _data_dir("wallpapers") / "history.json"


def _record_wallpaper(previous: str) -> None:
    previous = str(previous or "").replace("Current wallpaper:", "").strip().strip("'\"")
    if not previous or not Path(previous).exists():
        return
    path = _wallpaper_history_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    entries = data.get("entries", []) if isinstance(data, dict) else []
    entries.append({"path": str(previous), "at": datetime.now().isoformat(timespec="seconds")})
    _atomic_write_json(path, {"entries": entries[-20:]})


def _normalise_wallpaper_image(path: Path) -> Path:
    """Convert webp/gif/tiff to PNG — Windows cannot use those directly."""
    if path.suffix.lower() not in (".webp", ".gif", ".tif", ".tiff"):
        return path
    try:
        from PIL import Image
        with Image.open(path) as image:
            image.seek(0)
            target = _data_dir("wallpapers") / f"{path.stem}.png"
            image.convert("RGB").save(target, format="PNG")
            return target
    except Exception:
        return path


def _set_wallpaper_style(style: str) -> None:
    style = str(style or "fill").lower()
    if style not in _WALLPAPER_STYLES:
        return
    wallpaper_style, tile = _WALLPAPER_STYLES[style]
    if sys.platform != "win32":
        return
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop",
                             0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ, wallpaper_style)
        winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ, tile)
        winreg.CloseKey(key)
    except Exception as e:
        print(f"[Desktop] ⚠️ Wallpaper style failed: {e}")


def set_wallpaper(image_path: str, style: str = "fill") -> str:
    """Set a local image as wallpaper, with conversion, history and style."""
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        return f"Image not found: {image_path}"
    if path.suffix.lower() not in _WALLPAPER_EXTS:
        return f"Unsupported format: {path.suffix}. Use jpg, png, webp, gif or bmp."

    try:
        _record_wallpaper(get_current_wallpaper())
        path = _normalise_wallpaper_image(path)
        _set_wallpaper_style(style)
        if sys.platform == "win32":
            ctypes.windll.user32.SystemParametersInfoW(20, 0, str(path), 3)
        elif sys.platform == "darwin":
            script = f'tell application "Finder" to set desktop picture to POSIX file "{path}"'
            subprocess.run(["osascript", "-e", script])
        else:
            subprocess.run(["gsettings", "set", "org.gnome.desktop.background",
                            "picture-uri", f"file://{path}"])
        return f"Wallpaper set: {path.name}"
    except Exception as e:
        return f"Could not set wallpaper: {e}"


def previous_wallpaper() -> str:
    """Report the most recent wallpaper recorded by :func:`set_wallpaper`."""
    path = _wallpaper_history_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        entries = data.get("entries", []) if isinstance(data, dict) else []
        if not entries:
            return "No previous wallpaper recorded."
        return f"Previous wallpaper: {entries[-1].get('path', '')}"
    except Exception as e:
        return f"Could not read wallpaper history: {e}"


def undo_wallpaper() -> str:
    """Restore the wallpaper that was active before the last change."""
    path = _wallpaper_history_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        entries = data.get("entries", []) if isinstance(data, dict) else []
        if not entries:
            return "No wallpaper change to undo."
        previous = entries[-1].get("path", "")
        if not previous or not Path(previous).exists():
            return f"Previous wallpaper is no longer available: {previous}"
        _atomic_write_json(path, {"entries": entries[:-1]})
        return set_wallpaper(previous)
    except Exception as e:
        return f"Could not undo wallpaper: {e}"


WALLPAPER_MAX_BYTES = 25 * 1024 * 1024


def _prune_wallpapers(keep: int = 8) -> None:
    """Keep only the newest downloaded wallpapers on disk."""
    try:
        directory = _data_dir("wallpapers")
        files = sorted((p for p in directory.iterdir() if p.is_file()),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass


def set_wallpaper_from_web(url: str) -> str:
    """Download a verified public image (≤25 MB) and set it as wallpaper."""
    try:
        import requests
        from urllib.parse import urlparse
        from actions.document_qa import _is_private_target

        url = str(url or "").strip()
        scheme = (urlparse(url).scheme or "").lower()
        if scheme not in ("http", "https") or _is_private_target(url):
            return "Wallpaper URL is not allowed (must be a public http/https URL)."

        response = requests.get(
            url, stream=True, timeout=(5, 15), allow_redirects=True,
            headers={"User-Agent": "Friday/1.0 (wallpaper)"},
        )
        final_url = str(getattr(response, "url", url) or url)
        if _is_private_target(final_url):
            return "Wallpaper URL is not allowed (must be a public http/https URL)."
        declared = response.headers.get("Content-Length") if getattr(response, "headers", None) else None
        try:
            if declared and int(declared) > WALLPAPER_MAX_BYTES:
                return "Wallpaper image is too large (25 MB limit)."
        except (TypeError, ValueError):
            pass

        chunks, total = [], 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > WALLPAPER_MAX_BYTES:
                return "Wallpaper image is too large (25 MB limit)."
            chunks.append(chunk)
        if not chunks:
            return "Could not download wallpaper: empty response."

        data = b"".join(chunks)
        try:
            from PIL import Image
            import io
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except Exception:
            return "Could not download wallpaper: invalid image data."

        suffix = Path(urlparse(final_url).path).suffix.lower()
        if suffix not in _WALLPAPER_EXTS:
            suffix = ".jpg"
        target = _data_dir("wallpapers") / f"wallpaper_{datetime.now():%Y%m%d_%H%M%S}{suffix}"
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        _prune_wallpapers()
        return set_wallpaper(str(target))
    except Exception as e:
        return f"Could not download wallpaper: {e}"


def get_current_wallpaper() -> str:
    """Returns the current wallpaper path."""
    try:
        if sys.platform == "win32":
            import winreg
            key  = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                  r"Control Panel\Desktop")
            val, _ = winreg.QueryValueEx(key, "Wallpaper")
            return f"Current wallpaper: {val}"
        else:
            try:
                out = subprocess.run(
                    ["gsettings", "get", "org.gnome.desktop.background", "picture-uri"],
                    capture_output=True, text=True, timeout=5)
                value = out.stdout.strip().strip("'")
                if value.startswith("file://"):
                    value = value[7:]
                return f"Current wallpaper: {value}" if value else "No wallpaper set."
            except Exception:
                return "Wallpaper path retrieval not supported on this OS."
    except Exception as e:
        return f"Could not get wallpaper: {e}"


FILE_TYPE_MAP = {
    "Images":    [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"],
    "Documents": [".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".odt"],
    "Videos":    [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"],
    "Music":     [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"],
    "Archives":  [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"],
    "Code":      [".py", ".js", ".html", ".css", ".json", ".xml", ".ts", ".cpp", ".java", ".cs", ".php"],
    "Executables": [".exe", ".msi", ".bat", ".cmd", ".sh"],
}


def _undo_dir() -> Path:
    return _data_dir("desktop_undo")


def _save_undo_manifest(kind: str, moves: list[tuple[Path, Path]]) -> str:
    """Persist a reversible move list atomically and return its id."""
    manifest_id = f"{kind}_{datetime.now():%Y%m%d_%H%M%S_%f}"
    path = _undo_dir() / f"{manifest_id}.json"
    payload = {
        "kind": kind,
        "created": datetime.now().isoformat(timespec="seconds"),
        "moves": [{"from": str(src), "to": str(dst)} for src, dst in moves],
    }
    return manifest_id if _atomic_write_json(path, payload) else ""


def undo_desktop_action(manifest_id: str = "") -> str:
    """Roll back the most recent (or a named) organize/clean move batch."""
    directory = _undo_dir()
    try:
        if manifest_id:
            path = directory / f"{manifest_id}.json"
        else:
            files = sorted(directory.glob("*.json"), reverse=True)
            path = files[0] if files else None
        if path is None or not path.exists():
            return "No desktop action to undo."
        data = json.loads(path.read_text(encoding="utf-8"))
        restored = 0
        for move in reversed(data.get("moves", [])):
            src, dst = Path(move["from"]), Path(move["to"])
            if dst.exists() and not src.exists():
                shutil.move(str(dst), str(src))
                restored += 1
        path.unlink(missing_ok=True)
        return f"Undid {restored} file move(s)."
    except Exception as e:
        return f"Could not undo desktop action: {e}"


def _organize_plan(desktop: Path, mode: str) -> tuple[list[tuple[Path, Path]], list[str]]:
    moves: list[tuple[Path, Path]] = []
    skipped: list[str] = []
    for item in sorted(desktop.iterdir()):
        if item.is_dir() or item.name.startswith(".") or item.suffix.lower() == ".lnk":
            continue
        if mode == "by_date":
            folder_name = datetime.fromtimestamp(item.stat().st_mtime).strftime("%Y-%m")
        else:
            folder_name = "Others"
            for folder, exts in FILE_TYPE_MAP.items():
                if item.suffix.lower() in exts:
                    folder_name = folder
                    break
        target_dir = desktop / folder_name
        new_path = target_dir / item.name
        if new_path.exists():
            skipped.append(item.name)
            continue
        moves.append((item, new_path))
    return moves, skipped


def organize_desktop(mode: str = "by_type", dry_run: bool = False) -> str:
    """Group desktop files by type/date; ``dry_run`` only previews the moves."""
    desktop = _get_desktop()
    moves, skipped = _organize_plan(desktop, "by_date" if mode == "by_date" else "by_type")
    if dry_run:
        preview = ", ".join(src.name for src, _ in moves[:8])
        return f"Dry run: {len(moves)} file(s) would be organized ({preview})."
    applied: list[tuple[Path, Path]] = []
    for src, dst in moves:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            applied.append((src, dst))
        except Exception as e:
            print(f"[Desktop] ⚠️ Could not move {src.name}: {e}")
    manifest_id = _save_undo_manifest("organize", applied)
    result = f"Desktop organized ({mode}). {len(applied)} files moved."
    if applied:
        result += "\n" + "\n".join(f"{src.name} → {dst.parent.name}/" for src, dst in applied[:8])
        if len(applied) > 8:
            result += f"\n... and {len(applied)-8} more."
        if manifest_id:
            result += f"\nUndo with: desktop_control action=undo_organize id={manifest_id}"
    if skipped:
        result += f"\n{len(skipped)} files skipped (name conflict)."
    return result


def list_desktop(sort_by: str = "name", limit: int | None = None) -> str:
    """List desktop entries with optional sorting and a result limit."""
    desktop = _get_desktop()
    entries = [item for item in desktop.iterdir() if not item.name.startswith(".")]
    sort_by = str(sort_by or "name").lower()
    if sort_by == "size":
        entries.sort(key=lambda p: (p.stat().st_size if p.is_file() else 0), reverse=True)
    elif sort_by in ("date", "modified"):
        entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    elif sort_by == "type":
        entries.sort(key=lambda p: (p.suffix.lower(), p.name.lower()))
    else:
        entries.sort(key=lambda p: p.name.lower())

    try:
        cap = int(limit) if limit is not None else None
    except (TypeError, ValueError):
        cap = None
    if cap is not None and cap > 0:
        entries = entries[:cap]

    items = []
    for item in entries:
        if item.is_dir():
            try:
                count = len(list(item.iterdir()))
            except Exception:
                count = 0
            items.append(f"📁 {item.name}/ ({count} items)")
        else:
            size = item.stat().st_size
            size_str = (f"{size/1024:.1f} KB" if size < 1024 * 1024
                        else f"{size/1024/1024:.1f} MB")
            items.append(f"📄 {item.name} ({size_str})")

    if not items:
        return "Desktop is empty."
    return f"Desktop ({len(items)} items):\n" + "\n".join(items)


def clean_desktop(dry_run: bool = False) -> str:
    """Archive loose desktop files into a dated folder (nothing is deleted)."""
    desktop = _get_desktop()
    archive_dir = desktop / f"Desktop Archive {datetime.now():%Y-%m-%d}"
    candidates = [item for item in sorted(desktop.iterdir())
                  if item.is_file() and not item.name.startswith(".")
                  and item.suffix.lower() != ".lnk"
                  and not (archive_dir / item.name).exists()]
    if dry_run:
        return f"Dry run: {len(candidates)} file(s) would be moved to '{archive_dir.name}'."
    archive_dir.mkdir(exist_ok=True)
    applied: list[tuple[Path, Path]] = []
    for item in candidates:
        dst = archive_dir / item.name
        try:
            shutil.move(str(item), str(dst))
            applied.append((item, dst))
        except Exception as e:
            print(f"[Desktop] ⚠️ Could not archive {item.name}: {e}")
    manifest_id = _save_undo_manifest("clean", applied)
    result = f"Desktop cleaned. {len(applied)} files moved to '{archive_dir.name}'."
    if manifest_id:
        result += f" Undo with: desktop_control action=undo_clean id={manifest_id}"
    return result


def get_desktop_stats() -> str:
    """Return file/folder counts, size and a per-category breakdown."""
    desktop = _get_desktop()
    entries = list(desktop.iterdir())
    files = [item for item in entries if item.is_file()]
    folders = [item for item in entries if item.is_dir()]
    total_size = sum(item.stat().st_size for item in files)
    size_str = (f"{total_size/1024:.1f} KB" if total_size < 1024 * 1024
                else f"{total_size/1024/1024:.1f} MB")

    counts: dict[str, int] = {}
    for item in files:
        ext = item.suffix.lower()
        category = "Other"
        for name, exts in FILE_TYPE_MAP.items():
            if ext in exts:
                category = name
                break
        counts[category] = counts.get(category, 0) + 1
    breakdown = ", ".join(f"{name}: {count}" for name, count in sorted(counts.items()))

    return (
        f"Desktop stats:\n"
        f"  Files   : {len(files)}\n"
        f"  Folders : {len(folders)}\n"
        f"  Total size: {size_str}\n"
        f"  File types: {breakdown or 'none'}"
    )


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")
    return bool(value) if value is not None else default


def desktop_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None
) -> str:
    """
    Called from main.py.

    parameters:
        action      : wallpaper | wallpaper_url | current_wallpaper |
                      organize | clean | list | stats |
                      task (AI-powered — anything else)

        path        : image path for 'wallpaper'
        url         : image URL for 'wallpaper_url'
        mode        : 'by_type' or 'by_date' for 'organize'
        task        : Natural language description for AI-powered actions.
                      Example: "arrange icons by size"
                               "show me what's on my desktop"
                               "move all screenshots to a folder"
    """
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action", "") or "").lower().strip()
    task   = str(params.get("task", "") or "").strip()
    dry_run = _as_bool(params.get("dry_run"), False)

    result = "Unknown action."

    try:
        if action == "wallpaper":
            path = params.get("path", "")
            result = (set_wallpaper(path, style=params.get("style", "fill")) if path
                      else "No image path provided.")

        elif action == "wallpaper_url":
            url = params.get("url", "")
            result = set_wallpaper_from_web(url) if url else "No URL provided."

        elif action in ("current_wallpaper", "wallpaper_get"):
            result = get_current_wallpaper()

        elif action in ("undo_wallpaper", "wallpaper_undo"):
            result = undo_wallpaper()

        elif action in ("previous_wallpaper", "wallpaper_previous"):
            result = previous_wallpaper()

        elif action == "organize":
            result = organize_desktop(params.get("mode", "by_type"), dry_run=dry_run)

        elif action == "undo_organize":
            result = undo_desktop_action(str(params.get("id", "")).strip())

        elif action == "clean":
            result = clean_desktop(dry_run=dry_run)

        elif action == "undo_clean":
            result = undo_desktop_action(str(params.get("id", "")).strip())

        elif action == "list":
            result = list_desktop(params.get("sort_by", "name"), params.get("limit"))

        elif action == "stats":
            result = get_desktop_stats()

        elif action == "task" or task:
            actual_task = task or params.get("description", "")
            if not actual_task:
                return "Please describe what you want to do on the desktop, sir."
            result = _handle_desktop_task(actual_task)

        else:
            full_task = task or action
            if full_task:
                result = _handle_desktop_task(full_task)
            else:
                result = "No action or task specified."

    except Exception as e:
        result = f"Desktop control error: {e}"

    print(f"[Desktop] {result[:100]}")
    if player:
        player.write_log(f"[desktop] {result[:60]}")

    return result