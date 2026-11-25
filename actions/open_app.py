# actions/open_app.py
# Friday — fast (<20ms), dynamic cross-platform app launcher.
#
# Windows launches use a persistent, stale-while-revalidate multi-source index
# (Start Menu, App Paths, Uninstall keys, and UWP Get-StartApps) with
# cross-source deduplication, smart multi-app disambiguation, and UAC-safe
# AUMID launching.

from __future__ import annotations

import concurrent.futures
import difflib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
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


BASE_DIR = Path(__file__).resolve().parent.parent
APP_INDEX_CACHE_PATH = BASE_DIR / "data" / "app_index_cache.json"
INDEX_TTL_SECONDS = 900.0
_INDEX_TTL_S = INDEX_TTL_SECONDS
_CACHE_SCHEMA_VERSION = 2

# Shell metacharacters that must never reach a shell or an app name.
_UNSAFE_RE = re.compile(r"[&|<>^()%;!\r\n]")
_AUMID_RE = re.compile(r"^[A-Za-z0-9._-]+![A-Za-z0-9._-]+$")
_ICON_INDEX_RE = re.compile(r",\s*-?\d+\s*$")
_DEDUP_NOISE_RE = re.compile(
    r"\s*(?:\((?:64-bit|32-bit|x64|x86|user|system)\)|\[(?:64-bit|x64)\]|v?\d+\.\d+(?:\.\d+)*)\s*",
    re.IGNORECASE,
)

_JUNK_NAME_RE = re.compile(
    r"\b(?:uninstall(?:er)?|uninst|readme|license|release\s+notes|documentation|manual|changelog|on\s+the\s+web|website|what\s+is\s+new)\b"
    r"|^remove\s+|^help$",
    re.IGNORECASE,
)
_JUNK_TARGET_RE = re.compile(
    r"(?:unins\w*\.exe|uninstall\w*\.exe|msiexec\.exe|chromedriver)"
    r"|\.(?:ico|pdf|chm|txt|rtf|ini|html?|url|msi)$"
    r"|^(?:https?://|file://)",
    re.IGNORECASE,
)
_OPT_IN_ADMIN_TOOLS = frozenset({"administrative tools", "windows tools"})

_INDEX_LOCK = threading.RLock()
_APP_INDEX_CACHE: tuple[float, list[dict]] | None = None
_REFRESH_LOCK = threading.Lock()
_REFRESH_THREAD: threading.Thread | None = None
_UWP_FUTURE_LOCK = threading.Lock()
_UWP_FUTURE: concurrent.futures.Future | None = None
_UWP_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="FridayUWP")

_PENDING_LOCK = threading.RLock()
_PENDING_CHOICES: dict | None = None

_ORDINAL_MAP = {
    "1": 1, "first": 1, "first one": 1, "one": 1, "1st": 1,
    "2": 2, "second": 2, "second one": 2, "two": 2, "2nd": 2,
    "3": 3, "third": 3, "third one": 3, "three": 3, "3rd": 3,
    "4": 4, "fourth": 4, "fourth one": 4, "four": 4, "4th": 4,
    "5": 5, "fifth": 5, "fifth one": 5, "five": 5, "5th": 5,
}

_URI_SCHEMES = {
    "whatsapp": "whatsapp",
    "spotify": "spotify",
    "telegram": "tg",
    "discord": "discord",
    "settings": "ms-settings",
    "calculator": "calculator",
    "store": "ms-windows-store",
    "clock": "ms-clock",
    "photos": "ms-photos",
}

_APP_ALIASES = {
    "whatsapp":           {"Windows": "WhatsApp",               "Darwin": "WhatsApp",            "Linux": "whatsapp"},
    "wa":                 {"Windows": "WhatsApp",               "Darwin": "WhatsApp",            "Linux": "whatsapp"},
    "chrome":             {"Windows": "Google Chrome",          "Darwin": "Google Chrome",       "Linux": "google-chrome"},
    "google chrome":      {"Windows": "Google Chrome",          "Darwin": "Google Chrome",       "Linux": "google-chrome"},
    "firefox":            {"Windows": "Firefox",                "Darwin": "Firefox",             "Linux": "firefox"},
    "edge":               {"Windows": "Microsoft Edge",         "Darwin": "Microsoft Edge",      "Linux": "microsoft-edge"},
    "brave":              {"Windows": "Brave",                  "Darwin": "Brave Browser",       "Linux": "brave-browser"},
    "spotify":            {"Windows": "Spotify",                "Darwin": "Spotify",             "Linux": "spotify"},
    "vscode":             {"Windows": "Visual Studio Code",     "Darwin": "Visual Studio Code",  "Linux": "code"},
    "vs code":            {"Windows": "Visual Studio Code",     "Darwin": "Visual Studio Code",  "Linux": "code"},
    "visual studio code": {"Windows": "Visual Studio Code",     "Darwin": "Visual Studio Code",  "Linux": "code"},
    "discord":            {"Windows": "Discord",                "Darwin": "Discord",             "Linux": "discord"},
    "telegram":           {"Windows": "Telegram",               "Darwin": "Telegram",            "Linux": "telegram"},
    "instagram":          {"Windows": "Instagram",              "Darwin": "Instagram",           "Linux": "instagram"},
    "tiktok":             {"Windows": "TikTok",                 "Darwin": "TikTok",              "Linux": "tiktok"},
    "notepad":            {"Windows": "notepad.exe",            "Darwin": "TextEdit",            "Linux": "gedit"},
    "calculator":         {"Windows": "Calculator",             "Darwin": "Calculator",          "Linux": "gnome-calculator"},
    "calc":               {"Windows": "Calculator",             "Darwin": "Calculator",          "Linux": "gnome-calculator"},
    "terminal":           {"Windows": "Terminal",               "Darwin": "Terminal",            "Linux": "gnome-terminal"},
    "cmd":                {"Windows": "Command Prompt",         "Darwin": "Terminal",            "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",         "Darwin": "Terminal",            "Linux": "bash"},
    "explorer":           {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "paint":              {"Windows": "mspaint.exe",            "Darwin": "Preview",             "Linux": "gimp"},
    "word":               {"Windows": "winword",                "Darwin": "Microsoft Word",      "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                  "Darwin": "Microsoft Excel",     "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",               "Darwin": "Microsoft PowerPoint","Linux": "libreoffice --impress"},
    "outlook":            {"Windows": "outlook",                "Darwin": "Microsoft Outlook",   "Linux": "thunderbird"},
    "teams":              {"Windows": "ms-teams",               "Darwin": "Microsoft Teams",     "Linux": "teams"},
    "vlc":                {"Windows": "VLC media player",       "Darwin": "VLC",                 "Linux": "vlc"},
    "zoom":               {"Windows": "Zoom",                   "Darwin": "zoom.us",             "Linux": "zoom"},
    "slack":              {"Windows": "Slack",                  "Darwin": "Slack",               "Linux": "slack"},
    "steam":              {"Windows": "Steam",                  "Darwin": "Steam",               "Linux": "steam"},
    "epic":               {"Windows": "Epic Games Launcher",    "Darwin": "Epic Games Launcher", "Linux": "epic"},
    "obs":                {"Windows": "OBS Studio",             "Darwin": "OBS",                 "Linux": "obs"},
    "task manager":       {"Windows": "taskmgr.exe",            "Darwin": "Activity Monitor",    "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",           "Darwin": "System Settings",     "Linux": "gnome-control-center"},
    "store":              {"Windows": "ms-windows-store:",      "Darwin": "App Store",           "Linux": "gnome-software"},
    "photos":             {"Windows": "ms-photos:",             "Darwin": "Photos",              "Linux": "eog"},
    "clock":              {"Windows": "ms-clock:",              "Darwin": "Clock",               "Linux": "gnome-clocks"},
    "camera":             {"Windows": "ms-camera:",             "Darwin": "Photo Booth",         "Linux": "cheese"},
    "snipping tool":      {"Windows": "Snipping Tool",          "Darwin": "Screenshot",          "Linux": "gnome-screenshot"},
    "obsidian":           {"Windows": "Obsidian",               "Darwin": "Obsidian",            "Linux": "obsidian"},
    "notion":             {"Windows": "Notion",                 "Darwin": "Notion",              "Linux": "notion"},
    "blender":            {"Windows": "Blender",                "Darwin": "Blender",             "Linux": "blender"},
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
# Search field pre-computation & hygiene
# ---------------------------------------------------------------------------

def _norm_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _prepare_entry(entry: dict) -> dict:
    name = str(entry.get("name") or "").strip()
    norm = _norm_text(name)
    tokens = tuple(norm.split()) if norm else ()
    acronym = "".join(t[0] for t in tokens if t) if len(tokens) >= 2 else ""
    out = dict(entry)
    out["_norm"] = norm
    out["_tokens"] = tokens
    out["_acronym"] = acronym
    return out


def _is_junk_entry(name: str, target: str) -> bool:
    n = str(name or "").strip()
    t = str(target or "").strip()
    if len(n) <= 1:
        return True
    if _JUNK_NAME_RE.search(n):
        return True
    if t and _JUNK_TARGET_RE.search(t):
        return True
    return False


def _clean_display_icon(raw_icon: str) -> str:
    text = str(raw_icon or "").strip()
    if not text:
        return ""
    text = _ICON_INDEX_RE.sub("", text).strip().strip('"').strip("'")
    return text


def _dedup_key(name: str) -> str:
    cleaned = _DEDUP_NOISE_RE.sub(" ", str(name or ""))
    return _norm_text(cleaned)


def _source_priority(entry: dict) -> int:
    src = entry.get("source", "")
    aumid = str(entry.get("aumid") or "")
    path = str(entry.get("path") or "").lower()
    if src == "uwp" and _AUMID_RE.match(aumid):
        return 0
    if src == "app_paths" and path.endswith(".exe"):
        return 1
    if src == "start_menu" and path.endswith(".lnk"):
        return 2
    if src == "uwp":
        return 3
    if src == "uninstall" and path.endswith(".exe"):
        return 4
    return 5


# ---------------------------------------------------------------------------
# Windows app index & disk cache
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
    """(DisplayName, validated_exe) from Uninstall keys."""
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
            icon = _clean_display_icon(info.get("DisplayIcon") or "")
            if name and icon.lower().endswith(".exe"):
                rows.append((name, icon))
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


def _merge_and_deduplicate(raw_entries: list[dict]) -> list[dict]:
    best_by_key: dict[str, dict] = {}
    for entry in raw_entries:
        name = str(entry.get("name") or "").strip()
        target = str(entry.get("aumid") or entry.get("path") or "").strip()
        if _is_junk_entry(name, target):
            continue
        key = _dedup_key(name)
        if not key:
            continue
        prepared = _prepare_entry(entry)
        existing = best_by_key.get(key)
        if existing is None or _source_priority(prepared) < _source_priority(existing):
            best_by_key[key] = prepared
    return list(best_by_key.values())


def _load_disk_cache() -> tuple[float, list[dict]] | None:
    try:
        if not APP_INDEX_CACHE_PATH.is_file():
            return None
        payload = json.loads(APP_INDEX_CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != _CACHE_SCHEMA_VERSION:
            return None
        updated_at = float(payload.get("updated_at") or 0.0)
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            return None
        entries = [_prepare_entry(e) for e in raw_entries if isinstance(e, dict) and e.get("name")]
        return (updated_at, entries)
    except Exception:
        return None


def _save_disk_cache(entries: list[dict], updated_at: float | None = None) -> None:
    ts = float(updated_at if updated_at is not None else time.time())
    serializable = [
        {k: v for k, v in e.items() if not k.startswith("_")}
        for e in entries
    ]
    payload = {
        "version": _CACHE_SCHEMA_VERSION,
        "updated_at": ts,
        "entries": serializable,
    }
    try:
        APP_INDEX_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(APP_INDEX_CACHE_PATH.parent),
            prefix=".app_index_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp_name, APP_INDEX_CACHE_PATH)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    except Exception:
        pass


def _build_fast_native_entries() -> list[dict]:
    entries: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        f_start = pool.submit(_start_menu_shortcuts)
        f_app = pool.submit(_app_paths_registry)
        f_un = pool.submit(_registry_app_paths)
        for link in f_start.result():
            entries.append({"name": link.stem, "path": str(link), "source": "start_menu"})
        for name, exe in f_app.result():
            entries.append({"name": name, "path": exe, "source": "app_paths"})
        for name, exe in f_un.result():
            entries.append({"name": name, "path": exe, "source": "uninstall"})
    return entries


def _start_uwp_future() -> concurrent.futures.Future:
    global _UWP_FUTURE
    with _UWP_FUTURE_LOCK:
        if _UWP_FUTURE is None or _UWP_FUTURE.done():
            _UWP_FUTURE = _UWP_EXECUTOR.submit(_uwp_apps)
        return _UWP_FUTURE


def _schedule_background_refresh() -> None:
    global _REFRESH_THREAD
    with _REFRESH_LOCK:
        if _REFRESH_THREAD is not None and _REFRESH_THREAD.is_alive():
            return
        t = threading.Thread(
            target=lambda: _build_windows_app_index(refresh=True),
            daemon=True,
            name="FridayAppIndexRefresh",
        )
        _REFRESH_THREAD = t
        t.start()


def warm_app_index() -> None:
    """Non-blocking startup pre-warm: load disk cache into RAM and refresh if stale."""
    if platform.system() != "Windows":
        return
    with _INDEX_LOCK:
        if _APP_INDEX_CACHE is None:
            disk = _load_disk_cache()
            if disk is not None:
                updated_at, entries = disk
                _APP_INDEX_CACHE = (time.monotonic(), entries)
                if time.time() - updated_at > _INDEX_TTL_S:
                    _schedule_background_refresh()
                return
    _schedule_background_refresh()


def _build_windows_app_index(refresh: bool = False, *, wait_uwp: bool = True) -> list[dict]:
    """Cached, multi-source Windows app index with stale-while-revalidate."""
    global _APP_INDEX_CACHE
    with _INDEX_LOCK:
        if (not refresh and _APP_INDEX_CACHE is not None and
                time.monotonic() - _APP_INDEX_CACHE[0] <= _INDEX_TTL_S):
            return list(_APP_INDEX_CACHE[1])

        if platform.system() != "Windows":
            _APP_INDEX_CACHE = (time.monotonic(), [])
            return []

        if not refresh and _APP_INDEX_CACHE is None:
            disk = _load_disk_cache()
            if disk is not None:
                updated_at, entries = disk
                _APP_INDEX_CACHE = (time.monotonic(), entries)
                if time.time() - updated_at > _INDEX_TTL_S:
                    _schedule_background_refresh()
                return list(entries)

    # Cold start or explicit refresh
    uwp_future = _start_uwp_future()
    entries = _build_fast_native_entries()

    if refresh or wait_uwp:
        try:
            for name, aumid in uwp_future.result(timeout=4.0):
                entries.append({"name": name, "aumid": aumid, "source": "uwp"})
        except Exception:
            pass

    unique = _merge_and_deduplicate(entries)
    now_mono = time.monotonic()
    with _INDEX_LOCK:
        _APP_INDEX_CACHE = (now_mono, unique)
    if refresh or wait_uwp:
        _save_disk_cache(unique)
    else:
        _schedule_background_refresh()
    return list(unique)


def _rank_candidates_with_tiers(query: str, entries: list[dict]) -> list[tuple[int, dict]]:
    """Return [(tier, entry)] sorted best-first, filtering out non-matches."""
    q_raw = str(query or "").strip().lower()
    if not q_raw:
        return []
    q_norm = _norm_text(q_raw)
    q_tokens = tuple(q_norm.split())
    alias_norm = _norm_text(_normalize(q_raw))

    allow_admin_tools = any(term in q_norm for term in _OPT_IN_ADMIN_TOOLS)
    tiers: tuple[list[dict], ...] = tuple([] for _ in range(7))

    for entry in entries:
        prep = entry if "_norm" in entry else _prepare_entry(entry)
        cand_norm = prep["_norm"]
        if not cand_norm:
            continue
        if not allow_admin_tools and cand_norm in _OPT_IN_ADMIN_TOOLS:
            continue

        cand_tokens = prep["_tokens"]
        cand_acronym = prep["_acronym"]
        aumid_prefix = _norm_text(str(prep.get("aumid") or "").split("!")[0])

        # Tier 1: Exact normalized match on literal query
        if cand_norm == q_norm:
            tiers[0].append(prep)
        # Tier 2: Prefix whole-word match (e.g. "Chrome Canary" for "chrome")
        elif cand_norm.startswith(q_norm + " "):
            tiers[1].append(prep)
        # Tier 3: Curated alias exact match
        elif alias_norm and cand_norm == alias_norm:
            tiers[2].append(prep)
        # Tier 4: Non-prefix whole-word / token exact or acronym match
        elif (q_tokens and all(t in cand_tokens for t in q_tokens)) or (
            cand_acronym and q_norm == cand_acronym
        ):
            tiers[3].append(prep)
        # Tier 5: Prefix substring
        elif len(q_norm) >= 3 and (cand_norm.startswith(q_norm) or (aumid_prefix and aumid_prefix.startswith(q_norm))):
            tiers[4].append(prep)
        # Tier 6: Interior substring
        elif len(q_norm) >= 3 and (q_norm in cand_norm or (aumid_prefix and q_norm in aumid_prefix)):
            tiers[5].append(prep)
        # Tier 7: Conservative fuzzy
        elif len(q_norm) >= 3 and difflib.SequenceMatcher(None, q_norm, cand_norm).ratio() >= 0.80:
            tiers[6].append(prep)

    ranked: list[tuple[int, dict]] = []
    for tier_idx, bucket in enumerate(tiers, start=1):
        for item in bucket:
            ranked.append((tier_idx, item))
    return ranked


def _rank_candidates(query: str, entries: list[dict]) -> list[dict]:
    """exact/alias > whole-word/acronym > prefix > substring > fuzzy (no 0-score junk)."""
    return [entry for _, entry in _rank_candidates_with_tiers(query, entries)]


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


def _uri_protocol_exists(scheme: str) -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import winreg
    except ImportError:
        return False
    clean = str(scheme or "").strip().rstrip(":")
    if not clean:
        return False
    for hive, subkey in (
        (winreg.HKEY_CLASSES_ROOT, clean),
        (winreg.HKEY_CURRENT_USER, rf"Software\Classes\{clean}"),
    ):
        try:
            with winreg.OpenKey(hive, subkey):
                return True
        except OSError:
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


def _launch_aumid(aumid: str) -> bool:
    clean = str(aumid or "").strip()
    if not clean or _UNSAFE_RE.search(clean):
        return False
    target = f"shell:AppsFolder\\{clean}"
    try:
        subprocess.Popen(
            ["explorer.exe", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        return True
    except Exception:
        pass
    try:
        os.startfile(target)                            # type: ignore[attr-defined]
        return True
    except Exception:
        return False


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

    if aumid:
        if _launch_aumid(aumid):
            return True

    if name.startswith("shell:AppsFolder\\"):
        return _launch_aumid(name[len("shell:AppsFolder\\"):])

    if name.lower().startswith(("ms-settings:", "ms-windows-store:", "ms-photos:", "ms-clock:", "ms-camera:")):
        try:
            os.startfile(name)                          # type: ignore[attr-defined]
            return True
        except Exception:
            pass

    if safe_args:
        exe = shutil.which(name) or (name if Path(name).suffix.lower() == ".exe" else None)
        if exe:
            try:
                subprocess.Popen([exe, *safe_args], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, shell=False)
                return True
            except Exception:
                pass
        return False

    if name.lower().endswith(".lnk"):
        try:
            os.startfile(name)                          # type: ignore[attr-defined]
            return True
        except Exception:
            pass

    if name.lower().endswith(".exe") and Path(name).exists():
        try:
            subprocess.Popen([name], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, shell=False)
            return True
        except Exception:
            pass

    # Fallback for direct Start Menu / registry / PATH calls
    if platform.system() == "Windows":
        if _launch_start_menu(name):
            return True
        exe = shutil.which(name)
        if exe:
            try:
                subprocess.Popen([exe], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, shell=False)
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


def _resolve_pending_choice(params: dict, app_name: str) -> dict | None:
    """Resolve a pending disambiguation choice if active and matching."""
    global _PENDING_CHOICES
    with _PENDING_LOCK:
        pending = _PENDING_CHOICES
        if not pending or time.monotonic() > pending.get("expires_at", 0.0):
            _PENDING_CHOICES = None
            return None
        choices = pending.get("choices") or []
        if not choices:
            _PENDING_CHOICES = None
            return None

        raw_idx = params.get("choice") if params.get("choice") is not None else params.get("index")
        if raw_idx is not None:
            try:
                idx = int(raw_idx)
                if 1 <= idx <= len(choices):
                    _PENDING_CHOICES = None
                    return choices[idx - 1]
            except (TypeError, ValueError):
                pass

        key = str(app_name or "").strip().lower()
        if key in _ORDINAL_MAP:
            idx = _ORDINAL_MAP[key]
            if 1 <= idx <= len(choices):
                _PENDING_CHOICES = None
                return choices[idx - 1]

        q_norm = _norm_text(key)
        if q_norm:
            for item in choices:
                if _norm_text(item.get("name", "")) == q_norm:
                    _PENDING_CHOICES = None
                    return item

        # Unrelated new app request -> clear stale pending choices
        _PENDING_CHOICES = None
        return None


def _launch_entry(entry: dict, launcher, args) -> bool:
    aumid = entry.get("aumid")
    target = str(aumid or entry.get("path") or entry.get("name") or "")
    return launcher(target, args, aumid)


def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Open, focus, inspect or list apps. Never types into a global search bar."""
    global _PENDING_CHOICES
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "open").strip().lower()
    app_name = str(params.get("app_name") or params.get("app") or "").strip()
    url = str(params.get("url") or "").strip()
    if not app_name and url:
        app_name = url
    focus_if_running = params.get("focus_if_running", False)
    if isinstance(focus_if_running, str):
        focus_if_running = focus_if_running.strip().lower() in ("true", "1", "yes", "on")
    auto_select = bool(params.get("auto_select") or params.get("pick_first"))

    if action == "list":
        entries = _build_windows_app_index(refresh=bool(params.get("refresh")))
        query = str(params.get("query") or app_name).strip().lower()
        matches = _rank_candidates(query, entries) if query else entries[:40]
        if query:
            matches = [e for e in matches if query in str(e.get("name") or "").lower()]
        names = [str(e.get("name")) for e in matches[:40] if e.get("name")]
        return ("Indexed apps: " + ", ".join(names)) if names else "No apps are indexed on this system."

    system = platform.system()
    launcher = _OS_LAUNCHERS.get(system)

    # Handle follow-up disambiguation choice first (< 5ms)
    chosen_pending = _resolve_pending_choice(params, app_name)
    if chosen_pending is not None and launcher is not None:
        chosen_name = str(chosen_pending.get("name") or app_name)
        if _launch_entry(chosen_pending, launcher, params.get("args")):
            return f"Opened {chosen_name} successfully, sir."
        return f"I tried to open {chosen_name}, sir, but couldn't confirm it launched."

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

    if launcher is None:
        return f"Unsupported OS: {system}"

    if _UNSAFE_RE.search(app_name):
        print(f"[open_app] Refused unsafe launch name: {app_name!r}")
        return f"I can't open {app_name}, sir — that name isn't safe."

    normalized = _normalize(app_name)
    if action not in ("is_running", "status"):
        print(f"[open_app] Launching: {app_name} -> {normalized} ({system})")
    if player and action not in ("is_running", "status"):
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

    if system == "Windows":
        refresh = bool(params.get("refresh"))
        entries = _build_windows_app_index(refresh=refresh, wait_uwp=False)
        ranked = _rank_candidates_with_tiers(app_name, entries)
        if not ranked and not refresh:
            entries = _build_windows_app_index(refresh=False, wait_uwp=True)
            ranked = _rank_candidates_with_tiers(app_name, entries)

        if not ranked:
            scheme = _URI_SCHEMES.get(app_name.lower().strip())
            if scheme and _uri_protocol_exists(scheme):
                try:
                    os.startfile(f"{scheme}:")          # type: ignore[attr-defined]
                    return f"Opened {app_name} successfully, sir."
                except Exception:
                    pass
            if shutil.which(app_name) or shutil.which(normalized):
                if launcher(app_name, params.get("args"), None):
                    return f"Opened {app_name} successfully, sir."
            return f"I couldn't find '{app_name}' installed on this PC, sir."

        top_tier = ranked[0][0]
        top_tier_entries = [e for t, e in ranked if t == top_tier]

        if len(top_tier_entries) > 1 and not auto_select and top_tier != 1:
            choices = top_tier_entries[:5]
            with _PENDING_LOCK:
                _PENDING_CHOICES = {
                    "query": app_name,
                    "choices": choices,
                    "expires_at": time.monotonic() + 120.0,
                }
            numbered = ", ".join(f"{i}. {c['name']}" for i, c in enumerate(choices, 1))
            return f"I found multiple apps matching '{app_name}': {numbered}. Which one should I open, sir?"

        if len(top_tier_entries) > 1 and top_tier == 1 and not auto_select:
            # Multiple exact matches only if genuinely distinct; otherwise pick first
            pass

        chosen = ranked[0][1]
        if _launch_entry(chosen, launcher, params.get("args")):
            return f"Opened {chosen.get('name') or app_name} successfully, sir."
        return (f"I tried to open {app_name}, sir, but couldn't confirm it launched. "
                "It may still be loading or might not be installed.")

    try:
        success = launcher(normalized, params.get("args"), None)
        if not success and normalized != app_name:
            success = launcher(app_name, params.get("args"), None)
        if success:
            return f"Opened {app_name} successfully, sir."
        return (f"I tried to open {app_name}, sir, but couldn't confirm it launched. "
                "It may still be loading or might not be installed.")
    except Exception as e:
        print(f"[open_app] Failed: {e}")
        return f"Failed to open {app_name}, sir: {e}"
