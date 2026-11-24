# actions/open_app.py
# Friday — fast, deterministic cross-platform app launcher.
#
# Windows launches use a cached multi-source index (Start Menu, App Paths,
# Uninstall keys and UWP Get-StartApps). Nothing is ever typed into the
# Windows Start menu: an unknown app fails honestly instead of opening a
# Bing search by accident.

from __future__ import annotations

import difflib
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

try:
    import psutil
    _PSUTIL = True
except ImportError:                                    # pragma: no cover - environment
    psutil = None
    _PSUTIL = False


# Shell metacharacters that must never reach a shell or an app name.
_UNSAFE_RE = re.compile(r"[&|<>^()%;!\r\n]")
_INDEX_TTL_S = 300.0
_INDEX_LOCK = threading.RLock()
_APP_INDEX_CACHE: tuple[float, list[dict]] | None = None

_APP_ALIASES = {
    "whatsapp":           {"Windows": "WhatsApp",               "Darwin": "WhatsApp",            "Linux": "whatsapp"},
    "chrome":             {"Windows": "chrome",                 "Darwin": "Google Chrome",       "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                 "Darwin": "Google Chrome",       "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                "Darwin": "Firefox",             "Linux": "firefox"},
    "edge":               {"Windows": "msedge",                 "Darwin": "Microsoft Edge",      "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                  "Darwin": "Brave Browser",       "Linux": "brave-browser"},
    "spotify":            {"Windows": "Spotify",                "Darwin": "Spotify",             "Linux": "spotify"},
    "vscode":             {"Windows": "code",                   "Darwin": "Visual Studio Code",  "Linux": "code"},
    "vs code":            {"Windows": "code",                   "Darwin": "Visual Studio Code",  "Linux": "code"},
    "visual studio code": {"Windows": "code",                   "Darwin": "Visual Studio Code",  "Linux": "code"},
    "discord":            {"Windows": "Discord",                "Darwin": "Discord",             "Linux": "discord"},
    "telegram":           {"Windows": "Telegram",               "Darwin": "Telegram",            "Linux": "telegram"},
    "instagram":          {"Windows": "Instagram",              "Darwin": "Instagram",           "Linux": "instagram"},
    "tiktok":             {"Windows": "TikTok",                 "Darwin": "TikTok",              "Linux": "tiktok"},
    "notepad":            {"Windows": "notepad.exe",            "Darwin": "TextEdit",            "Linux": "gedit"},
    "calculator":         {"Windows": "calc.exe",               "Darwin": "Calculator",          "Linux": "gnome-calculator"},
    "terminal":           {"Windows": "cmd.exe",                "Darwin": "Terminal",            "Linux": "gnome-terminal"},
    "cmd":                {"Windows": "cmd.exe",                "Darwin": "Terminal",            "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",         "Darwin": "Terminal",            "Linux": "bash"},
    "explorer":           {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "paint":              {"Windows": "mspaint.exe",            "Darwin": "Preview",             "Linux": "gimp"},
    "word":               {"Windows": "winword",                "Darwin": "Microsoft Word",      "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                  "Darwin": "Microsoft Excel",     "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",               "Darwin": "Microsoft PowerPoint","Linux": "libreoffice --impress"},
    "outlook":            {"Windows": "outlook",                "Darwin": "Microsoft Outlook",   "Linux": "thunderbird"},
    "teams":              {"Windows": "ms-teams",               "Darwin": "Microsoft Teams",     "Linux": "teams"},
    "vlc":                {"Windows": "vlc",                    "Darwin": "VLC",                 "Linux": "vlc"},
    "zoom":               {"Windows": "Zoom",                   "Darwin": "zoom.us",             "Linux": "zoom"},
    "slack":              {"Windows": "Slack",                  "Darwin": "Slack",               "Linux": "slack"},
    "steam":              {"Windows": "steam",                  "Darwin": "Steam",               "Linux": "steam"},
    "epic":               {"Windows": "Epic Games Launcher",   "Darwin": "Epic Games Launcher", "Linux": "epic"},
    "obs":                {"Windows": "obs64",                  "Darwin": "OBS",                 "Linux": "obs"},
    "task manager":       {"Windows": "taskmgr.exe",            "Darwin": "Activity Monitor",    "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",           "Darwin": "System Settings",     "Linux": "gnome-control-center"},
    "store":              {"Windows": "ms-windows-store:",      "Darwin": "App Store",           "Linux": "gnome-software"},
    "photos":             {"Windows": "ms-photos:",             "Darwin": "Photos",              "Linux": "eog"},
    "clock":              {"Windows": "ms-clock:",              "Darwin": "Clock",               "Linux": "gnome-clocks"},
    "camera":             {"Windows": "ms-camera:",             "Darwin": "Photo Booth",         "Linux": "cheese"},
    "snipping tool":      {"Windows": "SnippingTool",           "Darwin": "Screenshot",          "Linux": "gnome-screenshot"},
    "obsidian":           {"Windows": "Obsidian",               "Darwin": "Obsidian",            "Linux": "obsidian"},
    "notion":             {"Windows": "Notion",                 "Darwin": "Notion",              "Linux": "notion"},
    "blender":            {"Windows": "blender",                "Darwin": "Blender",             "Linux": "blender"},
    "capcut":             {"Windows": "CapCut",                 "Darwin": "CapCut",              "Linux": "capcut"},
    "postman":            {"Windows": "Postman",                "Darwin": "Postman",             "Linux": "postman"},
    "figma":              {"Windows": "Figma",                  "Darwin": "Figma",               "Linux": "figma"},
}


def _normalize(raw: str) -> str:
    """Map a friendly app name to the canonical name for this OS."""
    system = platform.system()
    key = str(raw or "").lower().strip()
    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(system, str(raw))
    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(system, str(raw))
    return str(raw or "").strip()


def _is_running(app_name: str) -> bool:
    if not _PSUTIL:
        return True
    app_lower = app_name.lower().replace(" ", "").replace(".exe", "")
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                proc_name = proc.info["name"].lower().replace(" ", "").replace(".exe", "")
                if app_lower in proc_name or proc_name in app_lower:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Windows app index
# ---------------------------------------------------------------------------

def _start_menu_shortcuts() -> list[Path]:
    """All .lnk files from the user + system Start Menu programs folders."""
    dirs = []
    for env in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    links: list[Path] = []
    for d in dirs:
        if d.exists():
            try:
                links.extend(p for p in d.rglob("*.lnk") if p.is_file())
            except Exception:
                pass
    return links


def _registry_key_values(hive, path: str) -> list[dict]:
    try:
        import winreg
    except ImportError:
        return []
    rows = []
    try:
        with winreg.OpenKey(hive, path) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    sub = winreg.EnumKey(root, i)
                    with winreg.OpenKey(root, sub) as key:
                        info = {}
                        for name in ("DisplayName", "InstallLocation", "DisplayIcon", "(Default)"):
                            try:
                                info[name] = winreg.QueryValueEx(key, name)[0]
                            except OSError:
                                pass
                        rows.append(info)
                except OSError:
                    continue
    except Exception:
        pass
    return rows


def _app_paths_registry() -> list[tuple[str, str]]:
    """(AppName, executable) from HKLM/HKCU App Paths — no PATH traps."""
    try:
        import winreg
    except ImportError:
        return []
    results = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        rows = _registry_key_values(
            hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths")
        for row in rows:
            executable = str(row.get("(Default)") or "").strip().strip('"')
            name = Path(executable.replace("\\", "/")).stem if executable else ""
            if name:
                results.append((name, executable))
    return results


def _registry_app_paths() -> list[tuple[str, str]]:
    """(DisplayName, InstallLocation/DisplayIcon) from Uninstall keys."""
    try:
        import winreg
    except ImportError:
        return []
    rows = []
    hives = [
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in hives:
        for info in _registry_key_values(hive, path):
            name = str(info.get("DisplayName") or "").strip()
            location = str(info.get("InstallLocation") or "").strip()
            icon = str(info.get("DisplayIcon") or "").strip().strip('"')
            if name:
                rows.append((name, location or icon))
    return rows


def _uwp_apps() -> list[tuple[str, str]]:
    """(Name, AppID) pairs from `Get-StartApps`; timeout-bounded and guarded."""
    if platform.system() != "Windows" or not shutil.which("powershell"):
        return []
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode not in (0, None):
            return []
        import json
        data = json.loads(result.stdout or "[]")
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    return [
        (str(item.get("Name") or "").strip(), str(item.get("AppID") or "").strip())
        for item in data if isinstance(item, dict)
        and item.get("Name") and item.get("AppID")
    ]


def _build_windows_app_index(refresh: bool = False) -> list[dict]:
    """Cached, multi-source Windows app index (TTL 5 minutes)."""
    global _APP_INDEX_CACHE
    with _INDEX_LOCK:
        if (not refresh and _APP_INDEX_CACHE is not None and
                time.monotonic() - _APP_INDEX_CACHE[0] <= _INDEX_TTL_S):
            return list(_APP_INDEX_CACHE[1])
        if platform.system() != "Windows":
            _APP_INDEX_CACHE = (time.monotonic(), [])
            return []

        entries: list[dict] = []
        for link in _start_menu_shortcuts():
            entries.append({"name": link.stem, "path": str(link), "source": "start_menu"})
        for name, exe in _app_paths_registry():
            entries.append({"name": name, "path": exe, "source": "app_paths"})
        for name, location in _registry_app_paths():
            entries.append({"name": name, "path": location, "source": "uninstall"})
        for name, aumid in _uwp_apps():
            entries.append({"name": name, "aumid": aumid, "source": "uwp"})
        # Deterministic, de-duplicated by (name, launch target).
        unique, seen = [], set()
        for entry in entries:
            key = (entry.get("name", "").lower(),
                   str(entry.get("aumid") or entry.get("path") or "").lower())
            if key not in seen:
                seen.add(key)
                unique.append(entry)
        _APP_INDEX_CACHE = (time.monotonic(), unique)
        return list(unique)


def _rank_candidates(query: str, entries: list[dict]) -> list[dict]:
    """exact alias/name > prefix > substring > fuzzy close matches."""
    q = str(query or "").strip().lower()
    alias = _normalize(query).lower()
    tiers: tuple[list[dict], list[dict], list[dict], list[dict]] = ([], [], [], [])
    for entry in entries:
        names = [str(entry.get("name") or "")]
        if entry.get("aumid"):
            names.append(str(entry["aumid"]).split("!")[0])
        haystacks = [n.lower() for n in names if n]
        if not haystacks:
            continue
        if q in (alias, *(h.lower() for h in haystacks)):
            tiers[0].append(entry)
        elif any(h.startswith(q) for h in haystacks):
            tiers[1].append(entry)
        elif any(q in h or h in q for h in haystacks if len(q) > 2):
            tiers[2].append(entry)
        elif difflib.get_close_matches(q, haystacks, n=1, cutoff=0.72):
            tiers[3].append(entry)
    return [entry for tier in tiers for entry in tier]


def _find_registry_exe(app_name: str) -> str | None:
    """Kept for compatibility: best canonical executable for an app name."""
    for entry in _rank_candidates(app_name, _build_windows_app_index()):
        path = str(entry.get("path") or "")
        if path.lower().endswith(".exe") and Path(path).name:
            return path
    return None


def _launch_start_menu(app_name: str) -> bool:
    """Kept for compatibility: launch the first matching Start Menu link."""
    for link in _start_menu_shortcuts():
        stem = link.stem.lower()
        if app_name.lower() == stem or (app_name.lower() in stem and len(app_name) > 2):
            try:
                os.startfile(str(link))                 # type: ignore[attr-defined]
                return True
            except Exception:
                continue
    return False


def _safe_args(args) -> list[str] | None:
    if args is None:
        return []
    if isinstance(args, str):
        args = [args]
    if not isinstance(args, (list, tuple)):
        return None
    result = []
    for item in args:
        text = str(item)
        if _UNSAFE_RE.search(text) or "\x00" in text:
            return None
        result.append(text)
    return result


def _launch_windows(app_name: str, args=None, aumid: str | None = None) -> bool:
    """Launch/focus a Windows app through deterministic, shell-free paths."""
    name = str(app_name or "").strip()
    if not name or _UNSAFE_RE.search(name):
        print(f"[open_app] Refused unsafe launch name: {name!r}")
        return False
    safe_args = _safe_args(args)
    if safe_args is None:
        print(f"[open_app] Refused unsafe launch arguments: {args!r}")
        return False

    # UWP AUMIDs and protocol shortcuts launch through the AppsFolder.
    target = aumid or name
    if aumid and not target.startswith("shell:AppsFolder\\"):
        target = "shell:AppsFolder\\" + aumid.lstrip("\\")
    if target.startswith("shell:AppsFolder\\") or target.lower().startswith(("ms-settings:", "ms-windows-store:")):
        try:
            os.startfile(target)                       # type: ignore[attr-defined]
            return True
        except Exception:
            pass

    if safe_args is None:
        return False

    # Deterministic indexed launch: Start Menu shortcut / canonical executable.
    if not safe_args and platform.system() == "Windows":
        for entry in _rank_candidates(name, _build_windows_app_index()):
            target = str(entry.get("path") or "")
            entry_aumid = entry.get("aumid")
            if entry_aumid:
                try:
                    os.startfile("shell:AppsFolder\\" + str(entry_aumid))  # type: ignore[attr-defined]
                    return True
                except Exception:
                    continue
            if target.lower().endswith(".lnk"):
                try:
                    os.startfile(target)                   # type: ignore[attr-defined]
                    return True
                except Exception:
                    continue
            if target.lower().endswith(".exe") and Path(target).exists():
                try:
                    subprocess.Popen([target], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    return True
                except Exception:
                    continue

    if safe_args:
        # Explicit executable + validated argv: no shell, no typing.
        exe = shutil.which(name) or (name if Path(name).suffix.lower() == ".exe" else None)
        if exe:
            try:
                subprocess.Popen([exe, *safe_args], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return True
            except Exception:
                pass

    try:
        subprocess.Popen([name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        pass

    try:
        subprocess.Popen(["cmd", "/c", "start", "", name],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        pass
    return False


def _launch_macos(app_name: str, args=None, aumid: str | None = None) -> bool:
    name = str(app_name or "").strip()
    if not name or _UNSAFE_RE.search(name):
        return False
    for candidate in (name, f"{name}.app"):
        try:
            result = subprocess.run(["open", "-a", candidate], capture_output=True, timeout=8)
            if result.returncode == 0:
                return True
        except Exception:
            pass
    binary = shutil.which(name)
    if binary:
        try:
            subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            pass
    return False


def _launch_linux(app_name: str, args=None, aumid: str | None = None) -> bool:
    name = str(app_name or "").strip()
    if not name or _UNSAFE_RE.search(name):
        return False
    binary = (shutil.which(name) or shutil.which(name.lower()) or
              shutil.which(name.lower().replace(" ", "-")))
    if binary:
        try:
            subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            pass
    for command in (["xdg-open", name], ["gtk-launch", name.lower().replace(" ", "-")]):
        try:
            result = subprocess.run(command, capture_output=True, timeout=5)
            if result.returncode == 0:
                return True
        except Exception:
            pass
    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin": _launch_macos,
    "Linux": _launch_linux,
}


def _focus_window(app_name: str) -> bool:
    """Restore/activate an existing visible window, if pygetwindow supports it."""
    try:
        import pygetwindow as gw
    except Exception:
        return False
    q = str(app_name or "").lower()
    try:
        windows = [w for w in gw.getAllWindows() if getattr(w, "title", "")]
    except Exception:
        return False
    for window in windows:
        title = str(window.title).lower()
        if q in title or title in q:
            try:
                if getattr(window, "isMinimized", False):
                    window.restore()
                window.activate()
                return True
            except Exception:
                continue
    return False


_SAFE_URL_RE = re.compile(r"^(https?://|ms-settings:)", re.IGNORECASE)


def _open_url(url: str) -> bool:
    """Open only an http(s) or ms-settings URL — never file:/javascript:."""
    text = str(url or "").strip()
    if not _SAFE_URL_RE.match(text) or _UNSAFE_RE.search(text):
        print(f"[open_app] Refused unsafe URL: {text!r}")
        return False
    try:
        webbrowser.open(text)
        return True
    except Exception as e:
        print(f"[open_app] URL launch failed: {e}")
        return False


def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Open, focus, inspect or list apps. Never types into a global search bar."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "open").strip().lower()
    app_name = str(params.get("app_name") or params.get("app") or "").strip()
    url = str(params.get("url") or "").strip()
    if not app_name and url:
        app_name = url
    focus_if_running = params.get("focus_if_running", False)
    if isinstance(focus_if_running, str):
        focus_if_running = focus_if_running.strip().lower() in ("true", "1", "yes", "on")

    if action == "list":
        entries = _build_windows_app_index(refresh=bool(params.get("refresh")))
        query = app_name.lower()
        matches = _rank_candidates(app_name, entries) if query else entries[:40]
        names = [str(e.get("name")) for e in matches[:40] if e.get("name")]
        return ("Indexed apps: " + ", ".join(names)) if names else "No apps are indexed on this system."

    if not app_name:
        return "Please specify which application to open, sir."

    if _SAFE_URL_RE.match(app_name):
        if not _open_url(app_name):
            return "That URL isn't safe to open, sir."
        return f"Opened {app_name} successfully, sir."
    lowered = app_name.lower()
    if (lowered.startswith(("javascript:", "file:", "data:", "ftp:")) or
            re.match(r"^[a-z][a-z0-9+.-]*://", lowered)):
        return "That URL scheme isn't safe to open, sir."

    system = platform.system()
    launcher = _OS_LAUNCHERS.get(system)
    if launcher is None:
        return f"Unsupported OS: {system}"

    if _UNSAFE_RE.search(app_name):
        print(f"[open_app] Refused unsafe launch name: {app_name!r}")
        return f"I can't open {app_name}, sir — that name isn't safe."

    normalized = _normalize(app_name)
    print(f"[open_app] Launching: {app_name} -> {normalized} ({system})")
    if player:
        try:
            player.write_log(f"[open_app] {app_name}")
        except Exception:
            pass

    if action in ("is_running", "status"):
        running = _is_running(normalized) or _is_running(app_name)
        return f"{app_name} is {'running' if running else 'not running'}, sir."

    if action == "focus" or focus_if_running:
        if _focus_window(normalized) or _focus_window(app_name):
            return f"Brought {app_name} to the front, sir."

    aumid = None
    if system == "Windows":
        entries = _build_windows_app_index(refresh=bool(params.get("refresh")))
        candidates = _rank_candidates(app_name, entries)
        if candidates:
            chosen = candidates[0]
            aumid = chosen.get("aumid")
            target = aumid or str(chosen.get("path") or "")
            if target:
                normalized = target

    try:
        success = launcher(normalized, params.get("args"), aumid)
        if not success and normalized != app_name:
            success = launcher(app_name, params.get("args"), None)
        if success:
            return f"Opened {app_name} successfully, sir."
        return (f"I tried to open {app_name}, sir, but couldn't confirm it launched. "
                "It may still be loading or might not be installed.")
    except Exception as e:
        print(f"[open_app] Failed: {e}")
        return f"Failed to open {app_name}, sir: {e}"
