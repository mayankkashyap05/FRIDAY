from __future__ import annotations

import asyncio
import threading
import json
import sys
import re
import time
import traceback
from pathlib import Path

from logger import setup_logger, log, log_tool
from config.audio import (AUDIO_END_GRACE_S, SPEAKING_WATCHDOG_S,
                          SPEAKING_WATCHDOG_TICK_S)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


try:
    import sounddevice as sd
except (ImportError, OSError):
    # sounddevice may be installed while its native PortAudio library is absent.
    sd = None

try:
    import numpy as np
except ModuleNotFoundError:
    np = None

try:
    from google import genai
    from google.genai import types
except ModuleNotFoundError:
    genai = None
    types = None

from ui import FridayUI, make_ui
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    should_extract_memory, extract_memory
)
from agent.resilience import ToolResult, run_sync_tool
from agent.loop_guard import LoopGuard

# Heavy action modules are imported lazily on-demand in _build_tool_handlers()
# to keep startup instantaneous (<0.5s) and prevent unused heavy ML modules from hogging memory.


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-3.1-flash-live-preview"
THINKING_LEVEL      = "low"  # Gemini 3.1 thinking depth: minimal | low | medium | high
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

PHASE_IDLE, PHASE_LISTENING, PHASE_THINKING, PHASE_SPEAKING = (
    "idle", "listening", "thinking", "speaking"
)
MAX_ROLLING_CHARS = 16000


def _ensure_core_deps():
    missing = []
    if sd is None:
        missing.append("sounddevice or its PortAudio library")
    if genai is None or types is None:
        missing.append("google-genai")

    if not missing:
        return

    missing_str = ", ".join(missing)
    msg = (
        f"\nMissing dependencies: {missing_str}\n\n"
        "Install dependencies for this project:\n"
        f"  {sys.executable} -m pip install -r requirements.txt\n\n"
        "Tip: On Windows, make sure you install into the SAME Python you run.\n"
    )
    raise SystemExit(msg)


def _normalize_mode(text) -> str:
    """Map user wording to a canonical mode id, or '' if unknown."""
    m = (str(text or "").strip().lower()
         .replace("-", " ").replace("_", " "))
    aliases = {
        "normal": "normal", "default": "normal", "standard": "normal",
        "regular": "normal", "profonal": "normal", "professional": "normal",
        "romantic": "girlfriend", "romantic girlfriend": "girlfriend",
        "girlfriend": "girlfriend", "girl": "girlfriend",
        "crazy friend": "crazy_friend", "crazy_friend": "crazy_friend",
        "crazy": "crazy_friend", "funny friend": "crazy_friend",
        "friend": "friend", "friendly": "friend",
        "butler": "butler", "batman's butler": "butler", "butler mode": "butler",
        "alfred": "butler", "servant": "butler",
        "casual": "casual", "chill": "casual",
    }
    m = aliases.get(m, m)
    valid = {"normal", "girlfriend", "crazy_friend", "butler", "friend", "casual"}
    return m if m in valid else ""


VOICES = {
    "Achernar":      "mayin (Soft)",
    "Achird":        "do'stona (Friendly)",
    "Algenib":       "bo'g'iq (Gravelly)",
    "Algieba":       "silliq (Smooth)",
    "Alnilam":       "qat'iy (Firm)",
    "Aoede":         "engil ayol (Breezy)",
    "Autonoe":       "yorqin (Bright)",
    "Callirrhoe":    "erkin (Easy-going)",
    "Charon":        "chuqur erkak (Informative)",
    "Despina":       "silliq (Smooth)",
    "Enceladus":     "nafasli (Breathy)",
    "Erinome":       "aniq (Clear)",
    "Fenrir":        "qattiq erkak (Excitable)",
    "Gacrux":        "etuk (Mature)",
    "Iapetus":       "aniq (Clear)",
    "Kore":          "jonli ayol (Firm)",
    "Laomedeia":     "quvnoq (Upbeat)",
    "Leda":          "yoshlik (Youthful)",
    "Orus":          "qat'iy erkak (Firm)",
    "Puck":          "quvnoq erkak (Upbeat)",
    "Pulcherrima":   "dadil (Forward)",
    "Rasalgethi":    "ma'lumotli (Informative)",
    "Sadachbia":     "jonli (Lively)",
    "Sadaltager":    "bilimdon (Knowledgeable)",
    "Schedar":       "tinch (Even)",
    "Sulafat":       "iliq (Warm)",
    "Umbriel":       "erkin (Easy-going)",
    "Vindemiatrix":  "muloyim (Gentle)",
    "Zephyr":        "yorqin ayol (Bright)",
    "Zubenelgenubi": "oddiy (Casual)",
}
VOICE_NAMES = list(VOICES.keys())


def _normalize_voice(text) -> str:
    """Map user wording to a canonical Gemini voice name, or '' if unknown."""
    v = (str(text or "").strip().lower()
         .replace("-", " ").replace("_", " "))
    aliases = {
        "default": "Aoede", "auto": "Aoede", "standart": "Aoede",
        "standard": "Aoede", "normal": "Aoede", "classic": "Aoede",
        "aoede": "Aoede", "charon": "Charon", "fenrir": "Fenrir",
        "kore": "Kore", "puck": "Puck", "zephyr": "Zephyr",
        "ayol": "Aoede", "erkak": "Charon",
    }
    v = aliases.get(v, v.title())
    return v if v in VOICE_NAMES else ""


def _get_style_from_memory(memory: dict | None) -> tuple[str, str]:
    """
    Returns (mode, mood).
    Stored under preferences.assistant_mode / preferences.assistant_mood.
    """
    if not memory:
        return ("normal", "calm")

    prefs = memory.get("preferences", {}) if isinstance(memory, dict) else {}

    mode_entry = prefs.get("assistant_mode")
    mood_entry = prefs.get("assistant_mood")

    mode = mode_entry.get("value") if isinstance(mode_entry, dict) else mode_entry
    mood = mood_entry.get("value") if isinstance(mood_entry, dict) else mood_entry

    mode = _normalize_mode(mode) or "normal"
    mood = (str(mood or "").strip().lower() or "calm")

    valid_moods = {"calm", "playful", "romantic", "strict"}

    if mood not in valid_moods:
        mood = "calm"

    return (mode, mood)


def _get_voice_from_memory(memory: dict | None) -> str:
    """Returns the saved Gemini voice name (defaults to Aoede)."""
    prefs = memory.get("preferences", {}) if isinstance(memory, dict) else {}
    entry = prefs.get("assistant_voice")
    voice = entry.get("value") if isinstance(entry, dict) else entry
    voice = _normalize_voice(voice)
    return voice or "Aoede"


def _speech_should_release(silent_for: float, queue_empty: bool) -> bool:
    """True when a still-raised "speaking" flag should be dropped.

    Shared by the player loop (which releases as soon as a finished turn's
    audio has drained) and the watchdog (which releases a flag nothing else
    cleared). Keeping it in one place is what stops the two from disagreeing
    about when speech ends -- the previous watchdog used a fixed 25s ceiling
    while the player used a single ``queue.empty()`` sample.
    """
    return bool(queue_empty) and silent_for > AUDIO_END_GRACE_S


def _get_api_key() -> str:
    from api_keys import next_key
    return next_key()


def _looks_like_quota(err: str) -> bool:
    e = (err or "").lower()
    return any(tok in e for tok in ("resource_exhausted", "429", "quota", "rate limit"))


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are Friday, a sharp and efficient AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )


# ── Hafıza ────────────────────────────────────────────────────────────────────
_last_memory_input = ""
_memory_lock       = threading.Lock()
_memory_running    = False


def _update_memory_async(user_text: str, assistant_text: str) -> None:
    global _last_memory_input, _memory_running

    user_text   = (user_text   or "").strip()
    assistant_text = (assistant_text or "").strip()

    if len(user_text) < 5 or user_text == _last_memory_input:
        return

    with _memory_lock:
        if _memory_running:
            print("[Memory] ⏳ Already extracting — skipping this turn.")
            return
        _memory_running = True

    _last_memory_input = user_text

    try:
        api_key = _get_api_key()
        if not should_extract_memory(user_text, assistant_text, api_key):
            return
        data = extract_memory(user_text, assistant_text, api_key)
        if data:
            update_memory(data)
            print(f"[Memory] ✅ {list(data.keys())}")
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ {e}")
    finally:
        with _memory_lock:
            _memory_running = False


# ── Tool declarations ─────────────────────────────────────────────────────────
TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the Windows computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Application name or a safe http(s)/ms-settings URL"
                },
                "action": {"type": "STRING", "description": "open | focus | is_running | status | list (default: open)"},
                "focus_if_running": {"type": "BOOLEAN", "description": "Focus an existing window instead of launching a duplicate"},
                "url": {"type": "STRING", "description": "Optional http(s) or ms-settings URL to open"},
                "args": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Safe arguments for an explicit executable"},
                "refresh": {"type": "BOOLEAN", "description": "Rebuild the cached Windows app index"}
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web with grounded sources: concise answers, news, "
            "quick voice-ready answers, and structured comparisons."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":   {"type": "STRING", "description": "Search query"},
                "mode":    {"type": "STRING", "description": "search | news | compare | quick | answer"},
                "items":   {"type": "ARRAY", "items": {"type": "STRING"},
                            "description": "Items to compare (a comma-separated string also works)"},
                "aspect":  {"type": "STRING", "description": "price | specs | reviews | general"},
                "time_range": {"type": "STRING", "description": "day | week | month | year"},
                "region":  {"type": "STRING", "description": "Search region e.g. in-en, us-en, wt-wt"},
                "max_results": {"type": "INTEGER", "description": "1-10 fallback results (default 5)"},
                "include_sources": {"type": "BOOLEAN", "description": "Append up to 5 citations (default true)"},
                "fresh":   {"type": "BOOLEAN", "description": "Bypass the 5-minute search cache"},
            },
            "required": []
        }
    },
    {
        "name": "weather_report",
        "description": "Gets live Open-Meteo weather, forecasts and optional air quality.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name; omit for saved city"},
                "time": {"type": "STRING", "description": "now | today | tonight | morning | tomorrow | week | weekend | weekday"},
                "units": {"type": "STRING", "description": "metric (default) or imperial"},
                "include_aqi": {"type": "BOOLEAN", "description": "Include US AQI and PM2.5"},
                "open_browser": {"type": "BOOLEAN", "description": "Also open Google Weather (default false)"},
            },
            "required": []
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Contact name, phone number, or @username"},
                "message_text": {"type": "STRING", "description": "The message to send (Unicode is safe)"},
                "platform":     {"type": "STRING", "description": "whatsapp | instagram | telegram | other app (default: whatsapp)"},
                "send":         {"type": "BOOLEAN", "description": "Press Enter to send (default true)"},
                "dry_run":      {"type": "BOOLEAN", "description": "Draft only; never press Enter"},
                "web":          {"type": "BOOLEAN", "description": "Use wa.me web deep link instead of the desktop app"}
            },
            "required": ["receiver", "message_text"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Windows Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":     {"type": "STRING", "description": "set | list | delete | clear (default: set)"},
                "date":       {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":       {"type": "STRING", "description": "Time in HH:MM or 3:30 PM"},
                "when":       {"type": "STRING", "description": "'in 15 minutes', 'tomorrow 09:00', ISO-8601"},
                "in_minutes": {"type": "NUMBER", "description": "Minutes from now"},
                "in_hours":   {"type": "NUMBER", "description": "Hours from now"},
                "in_seconds": {"type": "NUMBER", "description": "Seconds from now"},
                "repeat":     {"type": "STRING", "description": "once | daily | weekly (default: once)"},
                "message":    {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube deterministically. Use for: opening YouTube, searching and "
            "playing videos (by query, URL or search-result index), pause/resume/toggle, "
            "seeking, next/previous, volume/mute, playback speed, fullscreen/theater/"
            "miniplayer/captions, ad skipping, now-playing status, summaries, video info "
            "and category charts/trending. 'play' with no arguments resumes the loaded video."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "open | search | play | pause | resume | toggle_play | seek | "
                        "forward | rewind | restart | next | previous | volume | mute | "
                        "unmute | speed | fullscreen | theater | miniplayer | captions | "
                        "skip_ad | now_playing | summarize | get_info | trending | close "
                        "(default: play; aliases such as play_pause, skip, cc are accepted)"
                    ),
                },
                "query":   {"type": "STRING", "description": "Search query for search/play/summarize/get_info"},
                "url":     {"type": "STRING", "description": "YouTube video URL"},
                "index":   {"type": "INTEGER", "description": "1-based result number from the last search/charts list"},
                "seconds": {"type": "NUMBER", "description": "Absolute seek position in seconds, or seconds to play from"},
                "offset":  {"type": "NUMBER", "description": "Relative seek offset in seconds (e.g. 15 or -10)"},
                "timestamp": {"type": "STRING", "description": "Seek position as 90, 1:30, 01:02:05 or 2m30s"},
                "volume":  {"type": "NUMBER", "description": "Volume 0-100 (0 also mutes)"},
                "speed":   {"type": "NUMBER", "description": "Playback rate 0.25-4.0 (1.0 is normal)"},
                "category": {"type": "STRING", "description": "Charts category: general | music | gaming | movies | podcasts"},
                "include_shorts": {"type": "BOOLEAN", "description": "Include Shorts in search results (default: false)"},
                "region":  {"type": "STRING", "description": "Country code for charts e.g. IN, US"},
                "open":    {"type": "BOOLEAN", "description": "Also open the YouTube results page for search"},
                "enabled": {"type": "BOOLEAN", "description": "Explicit on/off for theater, captions, miniplayer, fullscreen"},
                "save":    {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image and RETURNS the analysis "
            "as text. MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. You have NO visual ability without "
            "this tool. After it returns, speak the result naturally. "
            "No other vision channel exists — this is the only way to see."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "screen | window | region | camera (default: screen)"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"},
                "detail": {"type": "STRING", "description": "fast | balanced | high | ocr (default: balanced)"},
                "monitor": {"type": "INTEGER", "description": "0 = all monitors, 1 = primary (default), 2+ = secondary"},
                "region": {"type": "ARRAY", "items": {"type": "INTEGER"}, "description": "[x, y, width, height] for angle=region"},
                "save_path": {"type": "STRING", "description": "Optional path to save the captured JPEG"},
                "concise": {"type": "BOOLEAN", "description": "2-3 sentence voice answer (default true)"},
                "camera_index": {"type": "INTEGER", "description": "Optional webcam index"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page, "
            "media playback (play/pause/next/previous), and per-app volume "
            "(e.g. 'mute Discord', 'lower Spotify only'); exact volume_get / "
            "brightness_get queries, explicit dark_mode_on / light_mode_on, "
            "toggle_mute / unmute_app / list_audio_apps, cancel_shutdown, "
            "sleep_pc and hibernate. "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."},
                "app_name":    {"type": "STRING", "description": "App name for per-app volume (e.g. 'Spotify' for app_volume/mute_app)"}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls the web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, any web-based task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | fill | snapshot | hover | select | wait_for | extract_links | extract_text | screenshot | back | forward | reload | page_info | new_tab | list_tabs | switch_tab | close_tab | scroll | fill_form | smart_click | smart_type | get_text | press | close"},
                "url":         {"type": "STRING", "description": "URL for go_to action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "selector":    {"type": "STRING", "description": "CSS selector or semantic target for click/type"},
                "value":       {"type": "STRING", "description": "Value for select action"},
                "index":       {"type": "INTEGER", "description": "1-based tab index"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up or down for scroll"},
                "key":         {"type": "STRING", "description": "Key name for press action"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "allow_local": {"type": "BOOLEAN", "description": "Permit explicit localhost/private URLs"},
                "press_enter": {"type": "BOOLEAN", "description": "Press Enter after type/fill"},
                "timeout":     {"type": "INTEGER", "description": "wait_for timeout in seconds (1-30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": (
            "Manages files and folders: list, create, delete, move, copy, rename, read, write, "
            "find, disk usage, zip/unzip, and full auto-organization. The 'organize' action "
            "groups loose files into category subfolders (Images, Documents, Spreadsheets, "
            "Code, ...) in mode by_type, by month in by_date, or by AI in mode ai. Use "
            "dry_run=true to preview first. 'undo_organize' restores the last run."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | organize | undo_organize | info | zip | unzip | duplicates | search_content | recent"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "scope":       {"type": "STRING", "description": "For organize: desktop | downloads | documents | pictures | music | videos | home | <custom path>"},
                "mode":        {"type": "STRING", "description": "For organize: by_type (default) | by_date | ai"},
                "dry_run":     {"type": "BOOLEAN", "description": "For organize: preview only, move nothing"},
                "include_subfolders": {"type": "BOOLEAN", "description": "For organize: also process files in subfolders"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy/zip/unzip"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest/recent"},
                "text":        {"type": "STRING", "description": "Text to find with search_content"},
                "hours":       {"type": "NUMBER", "description": "Look-back window for recent files"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "cmd_control",
        "description": (
            "Runs CMD/terminal commands via natural language: disk space, processes, "
            "system info, network, find files, or anything in the command line."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "run | history"},
                "task":    {"type": "STRING", "description": "Natural language description of what to do"},
                "visible": {"type": "BOOLEAN", "description": "Open visible CMD window. Default: true"},
                "command": {"type": "STRING", "description": "Optional: exact command if already known"},
                "shell":   {"type": "STRING", "description": "cmd | powershell"},
                "cwd":     {"type": "STRING", "description": "Validated working directory"},
                "timeout": {"type": "INTEGER", "description": "1-120 seconds (default 30)"},
                "dry_run": {"type": "BOOLEAN", "description": "Validate and show resolved command without executing"},
            },
            "required": ["task"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | current_wallpaper | undo_wallpaper | previous_wallpaper | organize | clean | undo_organize | undo_clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "sort_by": {"type": "STRING", "description": "name | size | date | type for list"},
                "limit":   {"type": "INTEGER", "description": "Max entries for list"},
                "dry_run": {"type": "BOOLEAN", "description": "Preview organize/clean without moving files"},
                "id":      {"type": "STRING", "description": "Undo manifest id"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | optimize | refactor | undo_edit | review | test | generate_tests | screen_debug | auto"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
                "dry_run":     {"type": "BOOLEAN", "description": "Preview edits as a unified diff without writing"},
                "instruction": {"type": "STRING", "description": "Instruction for refactor/edit"},
                "save":        {"type": "BOOLEAN", "description": "Save generated tests"},
                "run_tests":   {"type": "BOOLEAN", "description": "Run generated tests with pytest -q"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":       {"type": "STRING", "description": "build | list_projects | run_project | fix_project"},
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
                "open_editor":  {"type": "BOOLEAN", "description": "Open the project in VS Code (default true)"},
                "run_tests":    {"type": "BOOLEAN", "description": "Allow python -m pytest/unittest run commands"},
            },
            "required": []
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | triple_click | middle_click | right_click | hotkey | press | scroll | move | hover | mouse_pos | drag | copy | paste | screenshot | wait | clear_field | focus_window | active_window | window_action | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "duration":    {"type": "NUMBER",  "description": "Move/drag duration in seconds (0-5)"},
                "locale":      {"type": "STRING",  "description": "Optional locale for random_data, e.g. IN"},
                "mode":        {"type": "STRING",  "description": "window_action: minimize|maximize|restore|close|focus"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | launch | largest | close_game | list | download_status | schedule | cancel_schedule | schedule_status | cancel_shutdown (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "currency":    {"type": "STRING",  "description": "Currency code (default INR)"},
                "non_stop":    {"type": "BOOLEAN", "description": "Only non-stop flights"},
                "max_stops":   {"type": "INTEGER", "description": "Maximum number of stops"},
                "sort_by":     {"type": "STRING",  "description": "price | duration"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "system_status",
        "description": (
            "Reports real-time system telemetry: battery, CPU, RAM/swap, disk, network, "
            "top processes, GPU, temperature, uptime and hardware specs. "
            "Use for questions about system health, performance, battery, Wi-Fi, storage, "
            "graphics, thermals, or what is running on the computer."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "focus": {
                    "type": "STRING",
                    "description": (
                        "overview | battery | cpu | memory | disk | network | "
                        "processes | gpu | temperature | uptime | hardware (default overview)"
                    ),
                },
                "format": {
                    "type": "STRING",
                    "description": "brief (voice-ready) | detailed | json (default brief)",
                },
                "sort_by":     {"type": "STRING", "description": "memory (default) or cpu for top processes"},
                "limit":       {"type": "INTEGER", "description": "Top-process count 1-20 (default 5)"},
                "fresh":       {"type": "BOOLEAN", "description": "Bypass the 3-second snapshot cache"}
            },
            "required": []
        }
    },
    {
        "name": "set_mode",
        "description": (
            "Sets Friday's conversation mode/persona. "
            "Use when the user says things like: "
            "'girlfriend mode', 'normal mode', 'crazy friend', 'butler mode', "
            "'engaging mode', 'switch your mode', 'o'zgartir'. "
            "This persists across sessions. Modes: "
            "normal | girlfriend | crazy_friend | butler | friend | casual."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mode": {
                    "type": "STRING",
                    "description": "One of: normal | girlfriend | crazy_friend | butler | friend | casual"
                }
            },
            "required": ["mode"]
        }
    },
    {
        "name": "set_voice",
        "description": (
            "Changes Friday's speaking voice (Gemini prebuilt voices). "
            "Use when the user asks to change/switch the voice, or says "
            "'ovozni o'zgartir', 'boshqa ovoz', 'speak with a male voice', "
            "'sounds too robotic'. This persists across sessions and takes "
            "effect shortly. Available voices: "
            + ", ".join(VOICE_NAMES) + "."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "voice": {
                    "type": "STRING",
                    "description": "One of: " + " | ".join(VOICE_NAMES)
                }
            },
            "required": ["voice"]
        }
    },
    {
        "name": "set_mood",
        "description": (
            "Sets Friday's mood/energy within the current mode. "
            "Use when the user asks for calmer, more playful, more romantic, or stricter tone. "
            "This persists across sessions."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mood": {
                    "type": "STRING",
                    "description": "One of: calm | playful | romantic | strict"
                }
            },
            "required": ["mood"]
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Alex, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "recall_memory",
        "description": (
            "Search long-term memory for a stored fact about the user. "
            "Use when the user asks 'do you remember...', 'what do you know about...', "
            "'what's my favorite...', or when you need a saved detail mid-conversation "
            "(name, preferences, projects, people). Returns what you know."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":    {"type": "STRING", "description": "What to look for (e.g. 'favorite food', 'sister', 'project')"},
                "category": {"type": "STRING", "description": "Optional: identity | preferences | projects | relationships | wishes | notes"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "forget_memory",
        "description": (
            "Delete a specific fact from long-term memory. "
            "Use when the user asks to forget/remove something they told you, "
            "e.g. 'forget that I like X', 'remove my name from memory'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "key":      {"type": "STRING", "description": "The key to remove (e.g. 'name', 'favorite_food', 'sister_name')"},
                "category": {"type": "STRING", "description": "Category containing the key: identity | preferences | projects | relationships | wishes | notes (default notes)"}
            },
            "required": ["key"]
        }
    },
    {
        "name": "clear_memory",
        "description": (
            "Clear all long-term memory, or just one category. "
            "Use when the user asks to clear/reset memory, e.g. 'forget everything'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {"type": "STRING", "description": "Optional: only clear this category (identity | preferences | projects | relationships | wishes | notes). Empty = clear everything."}
            },
            "required": []
        }
    },
    {
        "name": "task_manager",
        "description": (
            "Manage FRIDAY background agent tasks AND OS processes. Check "
            "'how is my task going?', cancel tasks, summarize activity, list "
            "top CPU/RAM processes, find a process, or end a process by name/PID. "
            "Process killing requires user confirmation."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "list | status | cancel | summary | processes | "
                        "find_process | kill"
                    ),
                },
                "task_id":        {"type": "STRING", "description": "Task ID, or 'latest'/'all'"},
                "status_filter":  {"type": "STRING", "description": "pending | running | completed | failed | cancelled"},
                "limit":          {"type": "INTEGER", "description": "Result count (tasks 1-50, processes 1-30)"},
                "process":        {"type": "STRING", "description": "Process name for find_process/kill"},
                "name":           {"type": "STRING", "description": "Alias for process"},
                "pid":            {"type": "INTEGER", "description": "Exact process PID"},
                "sort_by":        {"type": "STRING", "description": "memory (default) or cpu"},
                "all_matches":    {"type": "BOOLEAN", "description": "Allow killing all ambiguous name matches"},
                "force":          {"type": "BOOLEAN", "description": "Escalate to kill after graceful terminate (default true)"}
            },
            "required": []
        }
    },
    {
        "name": "clipboard",
        "description": (
            "Manage the system clipboard: read what's copied, copy text, paste it, "
            "clear it, or recall recent copied items. "
            "Use for 'copy that to clipboard', 'what's on my clipboard?', 'paste it'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "get | set | paste | clear | history | copy_last | copy_index | restore | pin | unpin | pinned | search | append | transform | info | stats | formats | get_files | save_image"},
                "text":   {"type": "STRING", "description": "Text for set/copy/pin/search/append/transform"},
                "index":  {"type": "INTEGER", "description": "1-based history index"},
                "limit":  {"type": "INTEGER", "description": "History limit 1-50"},
                "query":  {"type": "STRING", "description": "Search term"},
                "transform": {"type": "STRING", "description": "upper | lower | title | strip | trim_lines | clean_whitespace | dedupe_lines | sort_lines | json_pretty | json_minify | url_encode | url_decode | word_count"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "vision_gesture",
        "description": (
            "Full-body computer vision using the camera. Free, local, no AI API. "
            "Modes: gesture (debounced hand gestures), air_mouse (1€-filtered cursor, "
            "pinch click/drag, middle-pinch right click), volume (depth-normalized), "
            "motion (adjustable sensitivity), posture, objects, focus (away alerts). "
            "One-shots: face_count, qr, ocr, background_remove, describe, vqa, "
            "register_face, identify_face, multimodal, snapshot."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "start | stop | mode | record_start | record_stop | face_count | "
                        "qr | ocr | background_remove | describe | vqa | register_face | "
                        "identify_face | multimodal | snapshot | status"
                    ),
                },
                "mode": {"type": "STRING", "description": "gesture | air_mouse | volume | motion | posture | objects | focus"},
                "text": {"type": "STRING", "description": "Optional question or profile name"},
                "name": {"type": "STRING", "description": "Face profile name"},
                "sensitivity": {"type": "NUMBER", "description": "Motion threshold 0.001-1.0 (default 0.025)"},
                "grace": {"type": "NUMBER", "description": "Focus away grace window in seconds (default 10)"}
            },
            "required": []
        }
    },
    {
        "name": "notify",
        "description": (
            "Sends a Windows toast notification popup (title + message). "
            "Use when the user asks to notify/alert me at a moment, pop a notification, "
            "or remind me with a popup, e.g. 'notify me when the download finishes'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "send | history | clear_history (default: send)"},
                "message": {"type": "STRING", "description": "The notification message text"},
                "title":   {"type": "STRING", "description": "Optional title (default: Friday)"},
                "duration":{"type": "STRING", "description": "short | long"},
                "sound":   {"type": "STRING", "description": "default | none | alarm | reminder"},
                "url":     {"type": "STRING", "description": "Optional http(s) link for the toast"},
                "force":   {"type": "BOOLEAN", "description": "Bypass 30-second duplicate suppression"}
            },
            "required": []
        }
    },
    {
        "name": "daily_briefing",
        "description": (
            "Morning briefing: today's date/time, weather for the user's city, "
            "key facts remembered about them, and system status. "
            "Use when the user says 'good morning', 'daily briefing', 'brief me', "
            "or asks what's going on today."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "Optional city for weather (defaults to saved memory)"},
                "sections": {"type": "STRING", "description": "Optional comma-separated filter: weather,calendar,email,reminders,tasks,system,memory,news"},
                "news": {"type": "BOOLEAN", "description": "Opt-in news headlines section"},
                "detailed": {"type": "BOOLEAN", "description": "Return the detailed digest instead of short spoken text"}
            },
            "required": []
        }
    },
    {
        "name": "schedule",
        "description": (
            "Sets a timed in-app action that fires after a number of seconds. "
            "When it fires, Friday speaks the message and displays a notification. "
            "Also lists and cancels pending schedules. "
            "Use for countdowns, 'remind me in X minutes', timers, or delayed tasks."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "set | list | cancel (default: list)"},
                "seconds": {"type": "INTEGER", "description": "Delay in seconds (set only, min 10)"},
                "message": {"type": "STRING", "description": "What to say/notify when it fires"},
                "id":      {"type": "INTEGER", "description": "Schedule id to cancel"}
            },
            "required": []
        }
    },
    {
        "name": "wake_word",
        "description": (
            "Controls the hands-free wake word listener ('Hey Friday'). "
            "When active, Friday sleeps until the wake word is said, then listens. "
            "'record' samples your voice and builds a 'Hey Friday' reference. "
            "Use when the user says 'activate wake word', 'record the wake word', "
            "'listen for hey friday', or any wake-word command."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "start | stop | status | pause | resume | set_threshold | "
                        "devices | diagnostics | record | train (default: status)"
                    ),
                },
                "clips":     {"type": "INTEGER", "description": "Number of clips to record for 'record' (default: 5)"},
                "threshold": {"type": "NUMBER", "description": "Detection threshold 0.1-0.99 for set_threshold"},
                "cooldown":  {"type": "NUMBER", "description": "Post-trigger cooldown in seconds"},
                "device":    {"type": "INTEGER", "description": "Optional microphone device index"},
            },
            "required": []
        }
    },
    {
        "name": "create_presentation",
        "description": (
            "Generates a PowerPoint (.pptx) file from a simple request and saves it "
            "to Documents/Friday. Use for 'make me a presentation about X', "
            "'create slides about Y'. Pass the topic as title and the slides with "
            "their bullet points. After creating, tell the user where it was saved."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {
                    "type": "STRING",
                    "description": "Presentation title / topic"
                },
                "filename": {
                    "type": "STRING",
                    "description": "Optional file name without extension (defaults to title)"
                },
                "subtitle": {
                    "type": "STRING",
                    "description": "Optional subtitle for the title slide"
                },
                "slides": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "title":   {"type": "STRING", "description": "Slide heading"},
                            "bullets": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Bullet points for the slide"}
                        },
                        "required": ["title"]
                    },
                    "description": "List of slides, each with a title and bullet points"
                }
            },
            "required": ["title"]
        }
    },
    {
        "name": "create_spreadsheet",
        "description": (
            "Creates an Excel (.xlsx) spreadsheet with headers and data rows and saves it "
            "to Documents/Friday. Use for 'make a spreadsheet of ...', "
            "'create a table with columns ... and these values'. "
            "After creating, tell the user where it was saved."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "filename":  {"type": "STRING", "description": "File name without extension"},
                "sheet_name": {"type": "STRING", "description": "Optional sheet name (default: Sheet1)"},
                "sheets": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "name": {"type": "STRING"},
                            "headers": {"type": "ARRAY", "items": {"type": "STRING"}},
                            "rows": {"type": "ARRAY", "items": {"type": "ARRAY", "items": {"type": "STRING"}}},
                            "summary": {"type": "ARRAY", "items": {"type": "STRING"}},
                        },
                    },
                    "description": "Optional multiple sheets: [{name, headers, rows}]",
                },
                "summary":   {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Optional bold summary row (formulas supported)"},
                "headers":   {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Column headers"},
                "rows":      {"type": "ARRAY", "items": {"type": "ARRAY", "items": {"type": "STRING"}}, "description": "Data rows; each row is an array of cell values"}
            },
            "required": ["filename"]
        }
    },
    {
        "name": "smart_home_control",
        "description": (
            "Controls smart home devices registered in config/smart_home.json (Home Assistant, "
            "webhook or simulated). Actions: turn_on, turn_off, toggle, set_level, "
            "set_temperature, set_color, open, close, lock, unlock, activate, status, "
            "health, discover. Use for 'turn on the bedroom lights', 'set brightness to 50', "
            "'set the AC to 24', 'what devices are available'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "turn_on | turn_off | toggle | set_level | set_temperature | set_color | open | close | lock | unlock | activate | status | health | discover"},
                "device": {"type": "STRING", "description": "Device name/id, room, or group such as 'bedroom lights'"},
                "value":  {"type": "NUMBER", "description": "Level, percent, or temperature"},
                "room": {"type": "STRING", "description": "Restrict control to a room/area"},
                "color": {"type": "STRING", "description": "Named colour or #RRGGBB"},
                "temperature": {"type": "NUMBER", "description": "Target temperature"},
                "hvac_mode": {"type": "STRING", "description": "heat | cool | auto | off"},
                "domain": {"type": "STRING", "description": "Filter discovery/control by HA domain"},
                "query": {"type": "STRING", "description": "Filter discovery results"}
            },
            "required": []
        }
    },
    {
        "name": "gmail",
        "description": (
            "Full Gmail automation: send, read, search, read_message, draft, "
            "threaded reply, mark read/unread, unread_count and trash. Uses "
            "Google OAuth when authorized, otherwise a Gmail app password "
            "(gmail_user + gmail_app_password in config/api_keys.json)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":     {"type": "STRING", "description": "send | read | search | read_message | draft | reply | mark_read | mark_unread | unread_count | trash (default: send)"},
                "to":         {"type": "STRING", "description": "Recipient email address"},
                "subject":    {"type": "STRING", "description": "Email subject"},
                "body":       {"type": "STRING", "description": "Email body text"},
                "cc":         {"type": "STRING", "description": "CC addresses (comma separated)"},
                "bcc":        {"type": "STRING", "description": "BCC addresses (comma separated)"},
                "attachments":{"type": "ARRAY", "items": {"type": "STRING"}, "description": "Local file paths to attach"},
                "html":       {"type": "STRING", "description": "Optional HTML body"},
                "message_id": {"type": "STRING", "description": "Message id for read_message/reply/mark/trash"},
                "query":      {"type": "STRING", "description": "Gmail search query"},
                "limit":      {"type": "INTEGER", "description": "Recent email count (default 5)"}
            },
            "required": []
        }
    },
    {
        "name": "read_pdf",
        "description": (
            "Reads a PDF file and shows a preview of its content (page count and "
            "opening text). Give the file name or path, e.g. 'manual.pdf' or "
            "'C:\\Users\\you\\Documents\\manual.pdf'. Use for 'open/read the PDF', "
            "'what is in this document'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "PDF file name or full path"},
                "action": {"type": "STRING", "description": "preview | summary | search | metadata | pages (default: preview)"},
                "pages": {"type": "STRING", "description": "e.g. '1-3', '5', '1, 3-4'"},
                "layout": {"type": "BOOLEAN", "description": "Use layout-preserving text extraction"},
                "visual": {"type": "BOOLEAN", "description": "Send the PDF to Gemini for visual reading"},
                "password": {"type": "STRING", "description": "Password for encrypted PDFs"},
                "query": {"type": "STRING", "description": "Search term for action=search"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "pdf_qa",
        "description": (
            "Answers a question about the contents of a PDF file. Finds the "
            "relevant parts of the document and answers from them. Use for "
            "'what does the PDF say about X', 'summarize this document', "
            "'find Y in the manual'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path":     {"type": "STRING", "description": "PDF file name or full path"},
                "question": {"type": "STRING", "description": "Question about the document's content"},
                "pages":    {"type": "STRING", "description": "Only these pages, e.g. '2-5'"},
                "layout":   {"type": "BOOLEAN", "description": "Use layout-preserving extraction"},
                "visual":   {"type": "BOOLEAN", "description": "Read scanned pages with Gemini vision"},
                "password": {"type": "STRING", "description": "Password for encrypted PDFs"}
            },
            "required": ["path", "question"]
        }
    },
    {
        "name": "autostart",
        "description": (
            "Controls whether Friday starts automatically at Windows login. "
            "Actions: status | enable | disable. 'enable' registers Friday in "
            "the startup list. Use for 'start at login', 'run on startup', 'open automatically'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "status | enable | disable | repair | update (default: status)"},
                "delay_seconds": {"type": "INTEGER", "description": "Optional login delay, 0-300 seconds; positive values use Task Scheduler on Windows"},
                "method": {"type": "STRING", "description": "auto | registry | task_scheduler (Windows)"},
                "minimized": {"type": "BOOLEAN", "description": "Start FRIDAY minimized/hidden where supported"}
            },
            "required": []
        }
    },
    {
        "name": "vision_click",
        "description": (
            "Click anywhere on screen by describing it, using Gemini spatial "
            "grounding. Supports left/double/triple/right/middle clicks, hover, "
            "multi-monitor and DPI-aware coordinates, typing after a click, and "
            "coordinate-only reporting."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target":      {"type": "STRING", "description": "What to click, in plain words"},
                "click":       {"type": "STRING", "description": "left | double | triple | right | middle | hover"},
                "button":      {"type": "STRING", "description": "Alias for click"},
                "only_coords": {"type": "BOOLEAN", "description": "Return coordinates without clicking"},
                "monitor":     {"type": "INTEGER", "description": "0=virtual desktop, 1=primary (default), 2+=secondary"},
                "type_text":   {"type": "STRING", "description": "Text to type after clicking"},
                "press_enter": {"type": "BOOLEAN", "description": "Press Enter after typing"}
            },
            "required": ["target"]
        }
    },
    {
        "name": "monitor_alerts",
        "description": (
            "Sets PC monitoring alerts: warn when CPU, RAM, a disk (disk or "
            "disk:D), battery, temperature or a process exit crosses a "
            "threshold. Actions: list | add | remove | check | status | pause | "
            "resume | clear. Rules support sustained seconds and custom cooldown."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING", "description": "list | add | remove | check | status | pause | resume | clear (default: list)"},
                "kind":      {"type": "STRING", "description": "cpu | ram | disk | disk:D | battery | temp | process_exit"},
                "threshold": {"type": "NUMBER", "description": "Percent threshold to trigger"},
                "above":     {"type": "BOOLEAN", "description": "True=alert above threshold, false=below. Default true."},
                "sustained": {"type": "NUMBER", "description": "Seconds the threshold must hold before alerting"},
                "cooldown":  {"type": "NUMBER", "description": "Seconds before this rule may fire again"},
                "message":   {"type": "STRING", "description": "Optional custom alert message"},
                "id":        {"type": "INTEGER", "description": "Alert id to remove"}
            },
            "required": []
        }
    },
    {
        "name": "pc_health",
        "description": (
            "PC Health Guardian: automatic proactive alerts for battery, disk "
            "space, RAM, CPU, temperature and backup reminders — works out of "
            "the box with smart defaults, no setup needed. "
            "Actions: status | report | enable | disable | check | backup | "
            "set | reset | snooze | unsnooze. Probes CPU/RAM/temperature with "
            "backoff, checks all fixed drives, and reports battery health and "
            "cycle count. Use for 'pc health report', 'battery health', "
            "'snooze alerts for 30 minutes', 'warn me about low disk'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "status | report | enable | disable | check | backup | set | reset | snooze | unsnooze (default: status)"},
                "key":     {"type": "STRING", "description": "Setting for 'set': battery_low | battery_full_plugged | disk_min_free_gb | ram_high | cpu_high | temp_high | backup_days | cooldown | sustained_seconds"},
                "value":   {"type": "NUMBER", "description": "New numeric value for 'set'"},
                "minutes": {"type": "NUMBER", "description": "Snooze duration in minutes"}
            },
            "required": []
        }
    },
    {
        "name": "read_document",
        "description": (
            "Reads any local document or web page and shows a preview: PDF, TXT, "
            "Markdown, HTML, Word (.docx), CSV, Excel, PowerPoint, or a website "
            "URL. Use for 'read this document', 'what is in the file', "
            "'show me that page'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "source": {"type": "STRING", "description": "File name, full path, or URL"},
                "action": {"type": "STRING", "description": "preview | summary | search | stats"},
                "query":  {"type": "STRING", "description": "Search term for action=search"},
                "fresh":  {"type": "BOOLEAN", "description": "Bypass the extraction cache"}
            },
            "required": ["source"]
        }
    },
    {
        "name": "document_qa",
        "description": (
            "Answers questions about the content of ANY document or web page: "
            "PDF, Word, TXT, Markdown, HTML, Excel, PowerPoint or a website URL. "
            "Finds the relevant parts and answers from them. Use for "
            "'summarize this document', 'what does the file say about X', "
            "'what is on this website about Y'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "source":   {"type": "STRING", "description": "File name, full path, or URL"},
                "question": {"type": "STRING", "description": "Question about the content"}
            },
            "required": ["source", "question"]
        }
    },
    {
        "name": "email_watch",
        "description": (
            "Turns on or off an automatic watcher that alerts the user as soon "
            "as new mail arrives in Gmail (speaks + desktop notification). "
            "Works with Google OAuth or IMAP, supports sender/subject/unread "
            "filters and seeds a watermark on first enable. "
            "Actions: enable | disable | status | pause | resume."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "enable | disable | status | pause | resume (default: status)"},
                "sender": {"type": "STRING", "description": "Only alert for mail from this address"},
                "subject":{"type": "STRING", "description": "Only alert for subjects containing this text"},
                "unread_only": {"type": "BOOLEAN", "description": "Only alert for unread mail"}
            },
            "required": []
        }
    },
    {
        "name": "translate",
        "description": (
            "Translate, detect languages, transliterate, explain grammar, translate "
            "clipboard content, or batch-translate a list of texts. Use for "
            "'translate this into Russian', 'detect the language', 'romanize this', "
            "'translate what's on my clipboard'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "translate | detect | transliterate | explain | clipboard | batch"},
                "text":   {"type": "STRING", "description": "Text to translate (or use_clipboard=true)"},
                "texts":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Texts for batch mode"},
                "to":     {"type": "STRING", "description": "Target language or code (default English)"},
                "from":   {"type": "STRING", "description": "Optional source language or code"},
                "tone":   {"type": "STRING", "description": "natural | formal | casual | literal | business"},
                "copy":   {"type": "BOOLEAN", "description": "Copy clipboard translation result back"},
                "use_clipboard": {"type": "BOOLEAN", "description": "Read source text from clipboard"},
                "no_cache": {"type": "BOOLEAN", "description": "Bypass the 10-minute translation cache"}
            },
            "required": []
        }
    },
    {
        "name": "media_control",
        "description": (
            "Controls whatever music or video is playing (Spotify, YouTube, "
            "VLC, media players): play/pause, next, previous, volume up/down, "
            "mute. Uses native SendInput media keys on Windows (no hook) with "
            "keyboard/OS fallbacks. Actions: play, pause, toggle, next, prev, "
            "volume_up/down, volume_set, mute, status, now_playing."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | pause | toggle | next | prev | volume_up | volume_down | volume_set | mute | status | now_playing (default: toggle)"},
                "volume": {"type": "NUMBER", "description": "Target volume 0-100 for volume_set"},
                "steps":  {"type": "INTEGER", "description": "How many times to press the key"}
            },
            "required": []
        }
    },
    {
        "name": "api_add_key",
        "description": (
            "Adds a NEW Gemini API key to the rotation pool. Use when the "
            "current key(s) hit their daily quota (the AI says the limit is "
            "exhausted). The user pastes a new key starting with 'AIza...'. "
            "Old keys are kept and reused automatically after their quota "
            "resets. Use for 'yangi api key qoshish', 'add api key AIza...', "
            "'key qo'sh'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "key": {"type": "STRING", "description": "The new Gemini API key, starts with AIza"}
            },
            "required": ["key"]
        }
    },
    {
        "name": "google_auth",
        "description": (
            "Starts Google Desktop OAuth in the user's browser using a local "
            "loopback callback and PKCE S256. Complete consent in the opened "
            "browser tab; FRIDAY saves the Workspace token in the background. "
            "Use when Google Calendar/Drive/Gmail need connecting."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "google_auth_status",
        "description": (
            "Reports whether Google is authorized (and which Gmail account), "
            "or whether the local browser sign-in callback is still pending."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "calendar",
        "description": (
            "Google Calendar operations: list upcoming events, add a new "
            "event, delete an event. Use for 'what's on my calendar', 'add an "
            "event tomorrow at 3pm', 'dushanba 10da uchrashuv qo'y', 'delete "
            "the dentist appointment'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | today | tomorrow | week | search | add | update | reschedule | delete | free_busy | find_slot | calendars"},
                "summary":     {"type": "STRING", "description": "Event title (for add/delete)"},
                "title":       {"type": "STRING", "description": "Alias for summary"},
                "query":       {"type": "STRING", "description": "Search term or free/busy range query"},
                "start":       {"type": "STRING", "description": "Start 'YYYY-MM-DD HH:MM' or 'HH:MM' (for add)"},
                "end":         {"type": "STRING", "description": "End time, same format (for add)"},
                "description": {"type": "STRING", "description": "Optional details (for add)"},
                "event_id":    {"type": "STRING", "description": "Event id (for delete)"},
                "max_results": {"type": "INTEGER", "description": "How many events to list (1-50, default 10)"},
                "duration_minutes": {"type": "INTEGER", "description": "Event/slot duration (5-1440)"},
                "all_day":     {"type": "BOOLEAN", "description": "Create an all-day event"},
                "attendees":   {"type": "STRING", "description": "Comma-separated email addresses"},
                "recurrence":  {"type": "STRING", "description": "daily | weekly | monthly | weekdays"},
                "conference":  {"type": "BOOLEAN", "description": "Create a Google Meet link"},
                "reminder_minutes": {"type": "INTEGER", "description": "Popup reminder override"},
                "allow_overlap": {"type": "BOOLEAN", "description": "Create despite detected conflicts"},
                "force":       {"type": "BOOLEAN", "description": "Force update/delete/create despite conflicts"},
                "calendar_id": {"type": "STRING", "description": "Calendar id (default primary)"}
            },
            "required": []
        }
    },
    {
        "name": "drive",
        "description": (
            "Google Drive operations: list, search, read (Docs/Sheets/Slides/"
            "PDF/text), info, upload inline or local, download into "
            "Downloads/Friday, create_folder, trash/delete. Trashed files are "
            "always excluded from listings."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":     {"type": "STRING", "description": "list | search | read | info | upload | download | create_folder | trash (default: list)"},
                "query":      {"type": "STRING", "description": "Search query (for search)"},
                "file_id":    {"type": "STRING", "description": "File id (for read/info/download/trash)"},
                "name":       {"type": "STRING", "description": "File or folder name"},
                "content":    {"type": "STRING", "description": "Text content (for inline upload)"},
                "path":       {"type": "STRING", "description": "Local file path to upload"},
                "folder_id":  {"type": "STRING", "description": "Destination Drive folder id"},
                "max_results":{"type": "INTEGER", "description": "How many files to list (default 10)"}
            },
            "required": []
        }
    },
]

# ── Safety: inject a `confirm` flag into every tool that may be HIGH risk ────
# The model can pass confirm=true only after the user explicitly approves. The
# gate in _execute_tool/_dispatch_tool enforces this centrally (see safety.py).
for _decl in TOOL_DECLARATIONS:
    _name = _decl.get("name", "")
    if _name in {
        "cmd_control", "open_app", "desktop_control", "file_controller", "gmail",
        "task_manager", "code_helper", "game_updater",
        "computer_settings", "computer_control", "calendar", "drive",
        "smart_home_control", "browser_control", "send_message", "autostart",
    }:
        _decl.setdefault("parameters", {}).setdefault("properties", {})[
            "confirm"
        ] = {"type": "BOOLEAN",
             "description": ("Must be TRUE only after the user explicitly "
                             "approves this action.")}
        _decl.setdefault("parameters", {}).setdefault("properties", {})[
            "confirm_token"
        ] = {"type": "STRING",
             "description": ("One-time token from a blocked response "
                             "([token: ...]). Copy it exactly when re-invoking "
                             "after user approval.")}
        _decl.setdefault("parameters", {}).setdefault("properties", {})[
            "face_verify"
        ] = {"type": "BOOLEAN",
             "description": ("Optional: require local face identity verification "
                             "before executing this confirmed action.")}


class FridayLive:

    def __init__(self, ui: FridayUI, enable_bluetooth: bool = False):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._send_lock     = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self._last_play_ts = 0.0
        self._turn_complete = False
        # Monotonic timestamp of the last chunk handed to the speaker. The
        # watchdog measures silence from *this*, not from the last chunk
        # that happened to arrive, so a stall inside a reply is detected.
        self._last_audio_write_ts = 0.0
        # Throttle for live mic-amplitude updates pushed to the Siri orb.
        self._last_mic_amp_ts = 0.0
        self._last_speaker_amp_ts = 0.0
        self._ready         = False
        self._phase         = PHASE_IDLE
        self._phase_lock    = threading.Lock()
        self._rolling_parts = []
        self._rolling_len   = 0
        self._roll_turns    = 0
        self._human_confirmed = False
        self._confirmation_lock = threading.Lock()
        self._pending_card_token = None
        self._approved_card_token = None
        self.loop_guard     = LoopGuard()
        self._tool_handlers = self._build_tool_handlers()
        self.ui.on_text_command = self._on_text_command
        self.ui.on_silent_change = self._on_silent_change
        self.enable_bluetooth = False
        self._schedules     = []
        self._sched_lock    = threading.Lock()

        try:
            from actions.wake_word import _service as wake_service
            wake_service.configure(on_detect=self._on_wake_detect)
        except Exception:
            pass

    def _on_wake_detect(self):
        """Excited when the wake word is heard — un-mute, invoke Siri, listen."""
        try:
            if not self._ready or not self.session:
                print("[WakeWord] ⚠️ Ignored — session not ready.")
                return
            # Un-mute on the GUI thread so the orb's muted colour follows.
            self.ui._dispatch(self.ui.state_machine.set_muted, False, "wake-word")
            self.ui.muted = False
            self.set_speaking(False)
            # Invoke Siri: the orb springs into view (dormant → listening).
            self.ui.invoke()
            self.ui.write_log("SYS: Wake word heard — listening.")
        except Exception as e:
            print(f"[WakeWord] ⚠️ {e}")

    def attach_tray(self):
        """Register optional global shortcuts; the Qt UI owns the tray icon."""
        try:
            from actions import system_tray
            def toggle_wake():
                try:
                    from actions.wake_word import _pick_engine
                    _, engine = _pick_engine()
                    if not engine.available:
                        self.ui.write_log('SYS: Wake word engine unavailable.')
                        return
                    if engine._running:
                        result = engine.stop()
                    else:
                        engine.configure(on_detect=self._on_wake_detect)
                        result = engine.start()
                    self.ui.write_log(f'SYS: {result}')
                except Exception as exc:
                    self.ui.write_log(f'ERR: Wake word: {exc}')
            callbacks = {
                'toggle_mute': self.ui._toggle_mute,
                'toggle_wake': toggle_wake,
                'hide_show': lambda: self.ui.hide() if self.ui.visible else self.ui.show(),
                'briefing': lambda: self.ui.submit_text("Give me today's daily briefing."),
                'quit': self.ui.quit,
            }
            system_tray.register_hotkeys(callbacks)
        except Exception as exc:
            self.ui.write_log(f'SYS: Global shortcuts unavailable: {exc}')

    def _mark_human_confirmed(self):
        with self._confirmation_lock:
            if not self._pending_card_token:
                return
            self._human_confirmed = True
            self._approved_card_token = self._pending_card_token
        print("[FRIDAY] 🛡️ Human confirmation verified out-of-band.")

    def _request_card_confirmation(self, description, token):
        with self._confirmation_lock:
            self._pending_card_token = token
            self._approved_card_token = None
            self._human_confirmed = False
        request = getattr(self.ui, 'request_confirmation', None)
        if callable(request):
            try:
                request(description, lambda accepted: self._answer_card_confirmation(token, accepted))
            except Exception as exc:
                self.ui.write_log(f'ERR: Could not show confirmation: {exc}')

    def _answer_card_confirmation(self, token, accepted):
        with self._confirmation_lock:
            if token != self._pending_card_token:
                return 'This confirmation is no longer current.'
            self._pending_card_token = None
            if accepted and (not self._loop or not self.session or not self._send_lock):
                self._human_confirmed = False
                self._approved_card_token = None
                return 'Cannot approve while the assistant is disconnected.'
            self._human_confirmed = bool(accepted)
            self._approved_card_token = token if accepted else None
        self._send_text('Yes, I approve the requested action.' if accepted else 'No, I decline the requested action.')
        return 'Approval recorded.' if accepted else 'Request denied.'

    def _has_human_confirmation(self, token):
        with self._confirmation_lock:
            return bool(self._human_confirmed and token and token == self._approved_card_token)

    def _consume_human_confirmation(self, confirmed, token):
        with self._confirmation_lock:
            allowed = not confirmed or (self._human_confirmed and
                token == self._approved_card_token and bool(token))
            if allowed:
                self._human_confirmed = False
                self._pending_card_token = None
                self._approved_card_token = None
            return allowed

    def flush_audio(self):
        """Drop queued TTS when the user switches to text-only mode."""
        queue_ref = self.audio_in_queue
        if queue_ref is None:
            return
        dropped = 0
        try:
            while True:
                queue_ref.get_nowait()
                dropped += 1
        except asyncio.QueueEmpty:
            pass
        except Exception:
            pass
        if dropped:
            print(f"[FRIDAY] 🔇 Text-only mode: dropped {dropped} queued audio chunk(s)")

    def _on_silent_change(self, silent: bool):
        if silent:
            self.flush_audio()

    @staticmethod
    def _is_explicit_approval(text):
        # Never interpret a sentence containing an incidental affirmative as consent.
        normalized = " ".join(re.findall(r"\w+", (text or "").lower()))
        return normalized in {"yes", "yeah", "yep", "sure", "ok", "okay", "proceed",
                              "allow", "approve", "i approve", "yes i approve",
                              "ha", "albatta", "evet", "olur"}

    def _on_text_command(self, text: str):
        if not self._loop or not self.session or not self._send_lock:
            return
        if self._is_explicit_approval(text):
            self._mark_human_confirmed()
        self._send_text(text)

    def _send_text(self, text):
        if not self._loop or not self.session or not self._send_lock:
            return
        async def _send():
            async with self._send_lock:
                await self.session.send_realtime_input(text=text)
        asyncio.run_coroutine_threadsafe(_send(), self._loop)

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            was = self._is_speaking
            self._is_speaking = value
        if value:
            self._set_phase(PHASE_SPEAKING)
        elif was:
            self.ui._dispatch(self.ui.bridge.setAmplitude, 0.0)
            # Releasing must be unconditional. `_set_phase(PHASE_LISTENING)`
            # deliberately does nothing when muted, which left the machine (and
            # therefore audio ducking) stuck on SPEAKING for the rest of the
            # session. release_speech() always publishes the transition.
            self._set_phase(PHASE_LISTENING)
            try:
                self.ui.stop_speaking()
            except Exception:
                self._set_phase(PHASE_LISTENING)

    def _persona_info(self) -> tuple[str, str, str]:
        """Return (mode, mood, voice) for the UI header badges."""
        try:
            memory       = load_memory()
            mode, mood   = _get_style_from_memory(memory)
            voice        = _get_voice_from_memory(memory)
            return (mode, mood, voice)
        except Exception:
            return ("normal", "calm", "Aoede")

    def _set_phase(self, phase: str):
        with self._phase_lock:
            self._phase = phase
        if phase == PHASE_THINKING:
            self.ui.set_state("THINKING")
        elif phase == PHASE_SPEAKING:
            self.ui.set_state("SPEAKING")
        elif phase == PHASE_LISTENING:
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
        else:
            self.ui.set_state("ONLINE")
    def broadcast_remote(self, payload: dict):
        """Legacy remote event broadcaster (no-op)."""
        pass

    def _safely_send(self, client, payload: dict):
        """Legacy single-client sender (no-op)."""
        pass

    def _current_phase(self) -> str:
        with self._phase_lock:
            return self._phase

    def _schedule_session_reload(self):
        """Mode/voice changed — close the live session shortly so run() reconnects
        and re-applies the system prompt / speech config from memory."""
        if not self._loop or not self.session:
            return

        async def _reload_soon():
            await asyncio.sleep(2.5)
            try:
                await self.session.close()
            except Exception:
                pass

        try:
            asyncio.run_coroutine_threadsafe(_reload_soon(), self._loop)
        except Exception:
            pass

    def _update_rolling(self, user_text: str, friday_text: str):
        if not user_text and not friday_text:
            return
        part = f"User: {user_text}\nFriday: {friday_text}\n"
        self._rolling_parts.append(part)
        self._rolling_len += len(part)
        with self._phase_lock:
            self._roll_turns += 1
        while self._rolling_len > MAX_ROLLING_CHARS and self._rolling_parts:
            old = self._rolling_parts.pop(0)
            self._rolling_len -= len(old)

    def _rolling_digest(self, limit: int = 1400) -> str:
        """Flatten the recent-context buffer into the prompt-able string."""
        joined = "".join(self._rolling_parts)[-MAX_ROLLING_CHARS:]
        if len(joined) > limit:
            joined = joined[-limit:]
            cut = joined.find("\n")
            if cut > 0:
                joined = joined[cut:]
        return joined.strip()

    def _persist_rolling_summary(self):
        """Save a compact summary of recent turns into long-term memory so a
        restart doesn't wipe the conversation context entirely."""
        try:
            with self._phase_lock:
                self._roll_turns = 0
            recent = self._rolling_digest(limit=2000)
            if not recent:
                return
            from datetime import datetime
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            update_memory({
                "notes": {
                    f"conversation_{stamp.replace(' ', '_').replace(':', '-')}": {
                        "value": recent[:400]
                    }
                }
            })
            print("[Memory] 🧠 Rolling summary persisted")
        except Exception as e:
            print(f"[Memory] ⚠️ Could not persist summary: {e}")

    def speak(self, text: str):
        if not self._loop or not self.session or not self._send_lock:
            return
        async def _send():
            async with self._send_lock:
                await self.session.send_realtime_input(text=text)
        asyncio.run_coroutine_threadsafe(_send(), self._loop)

    def _log_tool_error(self, tool_name: str, tr: ToolResult):
        short = str(tr.content)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        if tr.error_kind == "fatal":
            self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_tool_handlers(self):
        """name -> (handler(args)->str, timeout_seconds)."""
        ui    = self.ui
        speak = self.speak

        def _agent_task(args):
            from agent.task_queue import get_queue, TaskPriority
            priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
            priority = priority_map.get(str(args.get("priority", "normal")).lower(), TaskPriority.NORMAL)
            task_id  = get_queue().submit(goal=args.get("goal", ""), priority=priority, speak=speak)
            return f"Task started (ID: {task_id})."

        def _recall(args):
            from memory.memory_manager import search_memory
            return search_memory(args.get("query", ""), args.get("category", ""))

        def _forget(args):
            from memory.memory_manager import forget_memory
            return forget_memory(args.get("key", ""), args.get("category", "notes"))

        def _clear_mem(args):
            from memory.memory_manager import clear_memory
            return clear_memory(args.get("category", ""))

        def _wake(args):
            from actions.wake_word import wake_word as wake_word_action
            return wake_word_action(parameters=args, player=ui)

        def _save_memory(args):
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            return "ok"

        def _set_mode(args):
            mode = _normalize_mode(args.get("mode", ""))
            if not mode:
                return ("Invalid mode. Use: normal, girlfriend, crazy_friend, "
                        "butler, friend, casual.")
            update_memory({"preferences": {"assistant_mode": {"value": mode}}})
            self._schedule_session_reload()
            self.ui.set_persona(*self._persona_info())
            return f"Mode set to: {mode}."

        def _set_voice(args):
            voice = _normalize_voice(args.get("voice", ""))
            if not voice:
                return ("Invalid voice. Use: " + ", ".join(VOICE_NAMES) + ".")
            update_memory({"preferences": {"assistant_voice": {"value": voice}}})
            self._schedule_session_reload()
            self.ui.set_persona(*self._persona_info())
            return f"Voice set to: {voice}."

        def _set_mood(args):
            mood = str(args.get("mood", "")).strip().lower()
            valid = {"calm", "playful", "romantic", "strict"}
            if mood not in valid:
                return f"Invalid mood '{mood}'. Use: calm, playful, romantic, strict."
            update_memory({"preferences": {"assistant_mood": {"value": mood}}})
            self.ui.set_persona(*self._persona_info())
            return f"Mood set to: {mood}."

        def _api_add_key(args):
            import api_keys
            key = str(args.get("key", "")).strip()
            try:
                n = api_keys.add_key(key)
            except ValueError as e:
                return f"❌ {e}"
            return (f"✅ Yangi API kalit qo'shildi (jami: {n}). "
                    "Limit tugagan kalitlar avtomatik aylanadi.")

        def _google_auth(args):
            import google_oauth
            if not google_oauth.is_configured():
                return ("Google OAuth not configured. Add the Desktop OAuth client JSON "
                        "to config/google_oauth.json, or set google_client_id in config/api_keys.json.")
            if google_oauth.has_token():
                return ("Google allaqachon ulangan"
                        + (f" ({google_oauth.email_address()})" if google_oauth.email_address() else "")
                        + ". /google_auth_status bilan ko'ring.")
            info = google_oauth.auth_pkce_start(open_browser=True)
            if not info.get("ok"):
                return f"❌ {info.get('error')}"
            return ("A Google sign-in page has opened in your browser. "
                    "Complete consent there; FRIDAY will finish connecting automatically.\n"
                    f"Fallback link: {info.get('auth_url', '')}")

        def _google_auth_status(args):
            import google_oauth
            return google_oauth.status()

        def _office_pptx(args):
            from actions.office_builder import create_presentation as _make_pptx
            return _make_pptx(
                title=args.get("title", ""),
                slides=args.get("slides") or [],
                filename=args.get("filename"),
                subtitle=args.get("subtitle", ""),
            )

        def _office_xlsx(args):
            from actions.office_builder import create_spreadsheet as _make_xlsx
            return _make_xlsx(
                filename=args.get("filename", "spreadsheet"),
                headers=args.get("headers") or [],
                rows=args.get("rows") or [],
                sheet_name=args.get("sheet_name", "Sheet1"),
                sheets=args.get("sheets"),
                summary=args.get("summary"),
                summary_row=args.get("summary_row"),
            )

        def _smart_home(args):
            from actions.smart_home import smart_home_control as _sh_control
            return _sh_control(
                action=args.get("action", "status"),
                device=args.get("device"),
                value=args.get("value"),
                **{k: args[k] for k in ("room", "color", "temperature", "hvac_mode",
                                        "domain", "query") if k in args},
            )

        def _lazy(mod, fn, *extra_args, **extra_kwargs):
            def invoke(args):
                import importlib
                f = getattr(importlib.import_module(mod), fn)
                return f(parameters=args, *extra_args, **extra_kwargs)
            return invoke

        return {
            "open_app":          (_lazy("actions.open_app", "open_app", response=None, player=ui), 60),
            "weather_report":    (_lazy("actions.weather_report", "weather_action", player=ui), 60),
            "browser_control":   (_lazy("actions.browser_control", "browser_control", player=ui), 120),
            "file_controller":   (_lazy("actions.file_controller", "file_controller", player=ui), 60),
            "send_message":      (_lazy("actions.send_message", "send_message", response=None, player=ui, session_memory=None), 60),
            "reminder":          (_lazy("actions.reminder", "reminder", response=None, player=ui), 30),
            "youtube_video":     (_lazy("actions.youtube_video", "youtube_video", response=None, player=ui), 90),
            "screen_process":    (_lazy("actions.screen_processor", "screen_process", player=ui), 120),
            "computer_settings": (_lazy("actions.computer_settings", "computer_settings", response=None, player=ui), 120),
            "cmd_control":       (_lazy("actions.cmd_control", "cmd_control", player=ui), 120),
            "desktop_control":   (_lazy("actions.desktop", "desktop_control", player=ui), 60),
            "code_helper":       (_lazy("actions.code_helper", "code_helper", player=ui, speak=speak), 180),
            "dev_agent":         (_lazy("actions.dev_agent", "dev_agent", player=ui, speak=speak), 180),
            "agent_task":        (_agent_task, 15),
            "web_search":        (_lazy("actions.web_search", "web_search", player=ui), 90),
            "computer_control":  (_lazy("actions.computer_control", "computer_control", player=ui), 120),
            "system_status":     (_lazy("actions.system_status", "system_status", player=ui), 45),
            "task_manager":      (_lazy("actions.task_manager", "task_manager", player=ui), 60),
            "clipboard":         (_lazy("actions.clipboard", "clipboard_action", player=ui), 45),
            "vision_gesture":    (_lazy("actions.vision_gesture", "vision_gesture", player=ui, speak=speak), 120),
            "recall_memory":     (_recall, 20),
            "forget_memory":     (_forget, 10),
            "clear_memory":      (_clear_mem, 10),
            "game_updater":      (_lazy("actions.game_updater", "game_updater", player=ui, speak=speak), 120),
            "flight_finder":     (_lazy("actions.flight_finder", "flight_finder", player=ui), 120),
            "notify":            (_lazy("actions.notifications", "notify", player=ui), 30),
            "daily_briefing":    (_lazy("actions.daily_briefing", "daily_briefing", player=ui), 120),
            "wake_word":         (_wake, 45),
            "save_memory":       (_save_memory, 10),
            "set_mode":          (_set_mode, 10),
            "set_voice":         (_set_voice, 10),
            "set_mood":          (_set_mood, 10),
            "gmail":             (_lazy("actions.gmail", "gmail_action", response=None, player=ui), 60),
            "read_pdf":          (_lazy("actions.pdf_reader", "read_pdf", response=None, player=ui), 60),
            "pdf_qa":            (_lazy("actions.pdf_reader", "pdf_qa", response=None, player=ui), 90),
            "autostart":         (_lazy("actions.autostart", "autostart_action", response=None, player=ui), 20),
            "vision_click":      (_lazy("actions.vision_click", "vision_click_action", response=None, player=ui), 90),
            "monitor_alerts":    (_lazy("actions.monitor", "monitor_action", response=None, player=ui), 15),
            "pc_health":         (_lazy("actions.guardian", "guardian_action", response=None, player=ui), 15),
            "email_watch":       (_lazy("actions.monitor", "email_watch_action", response=None, player=ui), 15),
            "translate":         (_lazy("actions.translate", "translate_action", response=None, player=ui), 45),
            "media_control":     (_lazy("actions.media_control", "media_control_action", response=None, player=ui), 20),
            "read_document":     (_lazy("actions.document_qa", "read_document", response=None, player=ui), 45),
            "document_qa":       (_lazy("actions.document_qa", "document_qa", response=None, player=ui), 90),
            "api_add_key":       (_api_add_key, 15),
            "google_auth":       (_google_auth, 30),
            "google_auth_status": (_google_auth_status, 15),
            "calendar":          (_lazy("actions.calendar", "calendar_action", response=None, player=ui), 60),
            "drive":             (_lazy("actions.drive", "drive_action", response=None, player=ui), 60),
            "schedule":          (lambda a: self._schedule(a), 30),
            "create_presentation": (_office_pptx, 60),
            "create_spreadsheet":  (_office_xlsx, 60),
            "smart_home_control":  (_smart_home, 30),
        }

    async def _run_safety_gate(self, name: str, args: dict) -> ToolResult | None:
        """Shared loop-guard, confirmation-token, human-consent, and face-verify gate.
        Returns a ToolResult if the call is blocked, or None if it may proceed."""
        from safety import (
            needs_confirmation, describe, CONFIRM_MESSAGE, classify, Risk,
            CONFIRM_PARAM, CONFIRM_TOKEN_PARAM, issue_confirmation_token, sanitize,
        )
        confirm = bool(args.pop(CONFIRM_PARAM, False))
        confirm_token = str(args.pop(CONFIRM_TOKEN_PARAM, ""))
        face_verify = bool(args.pop("face_verify", False))

        high_risk = classify(name, args) == Risk.HIGH
        if confirm and high_risk and not self._has_human_confirmation(confirm_token):
            reason = "no matching human approval (or missing confirmation token)"
            print(f"[FRIDAY] ⛔ {name} confirmation rejected: {reason}")
            self.ui.write_log(f"SECURITY: {name} confirmation rejected: {reason}")
            log_tool(name, sanitize(name, args), error=reason)
            return ToolResult(
                ok=True,
                content="Action blocked: human user has not verbally approved this yet.",
                error_kind="confirmation",
            )
        if needs_confirmation(name, args, confirm=confirm, confirm_token=confirm_token):
            token = issue_confirmation_token(name, args) if not confirm else ""
            if confirm:
                reason = "token invalid, expired, or arguments differ"
                print(f"[FRIDAY] ⛔ {name} confirmation rejected: {reason}")
                self.ui.write_log(f"SECURITY: {name} confirmation rejected: {reason}")
            else:
                self.ui.write_log(f"SECURITY: '{describe(name, args)}' requires your approval.")
                print(f"[FRIDAY] ⛔ {name} blocked — confirmation required")
            if token:
                self._request_card_confirmation(
                    f"{describe(name, args)}\n\n{json.dumps(sanitize(name, args), ensure_ascii=False, indent=2, default=str)}",
                    token,
                )
            log_tool(name, sanitize(name, args), error="confirmation required")
            return ToolResult(
                ok=True,
                content=("Confirmation rejected: token invalid, expired, or arguments differ. "
                         "Request a new confirmation for the exact action." if confirm else
                         CONFIRM_MESSAGE.format(desc=describe(name, args), param=CONFIRM_PARAM)
                         + f"\n[token: {token}]"),
                error_kind="confirmation",
            )
        if high_risk and confirm and not self._consume_human_confirmation(confirm, confirm_token):
            print(f"[FRIDAY] ⛔ {name} confirmation rejected: human approval changed before redemption")
            self.ui.write_log(f"SECURITY: '{name}' blocked — no verbal consent.")
            return ToolResult(
                ok=True,
                content="Action blocked: human user has not verbally approved this yet. Ask the user for permission first.",
                error_kind="confirmation",
            )
        if high_risk and confirm:
            self.ui.write_log(f"SECURITY: '{name}' approved by user.")
        if face_verify:
            from actions.vision_gesture import vision_gesture

            identity = await run_sync_tool(
                lambda: vision_gesture({"action": "verify_identity"}, player=self.ui),
                "face_identity_verification",
                timeout=30,
            )
            if not identity.ok or not str(identity.content).lower().startswith("identity verified:"):
                return ToolResult(
                    ok=False,
                    content="Face identity verification failed. The requested action was not executed.",
                    error_kind="identity",
                )
        # Only calls that have passed the gate count as executions. A freshly
        # approved action starts a new sequence even after earlier executions.
        if high_risk and confirm:
            self.loop_guard.reset()
        blocked = self.loop_guard.check(name, args)
        if blocked:
            print(f"[FRIDAY] 🛑 {name} blocked by loop guard")
            return ToolResult(ok=False, content=blocked, error_kind="loop")
        return None

    async def _dispatch_tool(self, name: str, args: dict) -> ToolResult:
        from safety import sanitize as _sanitize
        gate_result = await self._run_safety_gate(name, args)
        if gate_result is not None:
            return gate_result

        entry = self._tool_handlers.get(name)
        if entry is None:
            return ToolResult(ok=False, content=f"Unknown tool: {name}", error_kind="fatal")

        print(f"[FRIDAY] 🔧 {name} {_sanitize(name, args)}")
        fn, timeout = entry
        return await run_sync_tool(lambda: fn(args), name, timeout=timeout)

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        mode, mood = _get_style_from_memory(memory)
        voice      = _get_voice_from_memory(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        style_ctx = (
            "[ASSISTANT MODE & MOOD]\n"
            f"Mode: {mode}\n"
            f"Mood: {mood}\n"
            f"Voice: {voice}\n"
            "Follow the mode/mood rules from the system prompt.\n\n"
        )

        parts = [time_ctx]
        recent_ctx = self._rolling_digest(limit=1400)
        if recent_ctx:
            parts.append(
                "[RECENT CONVERSATION — use it to stay consistent, but focus on what the user just said]\n"
                + recent_ctx + "\n"
            )
        if mem_str:
            parts.append(mem_str)
        parts.append(style_ctx)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow()
            ),
            thinking_config=types.ThinkingConfig(
                thinking_level=THINKING_LEVEL
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        from safety import sanitize as _sanitize
        log_tool(name, _sanitize(name, args))
        print(f"[FRIDAY] 🔧 {name}  {_sanitize(name, args)}")
        self.ui.set_state("THINKING")

        # ── save_memory: sessiz, hızlı, Gemini'ye bildirim yok ───────────────
        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        # ── set_mode / set_mood / set_voice: persist style across sessions ──
        if name == "set_mode":
            mode = _normalize_mode(args.get("mode", ""))
            if not mode:
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": ("Invalid mode. Use: normal, girlfriend, "
                                         "crazy_friend, butler, friend, casual.")}
                )
            update_memory({"preferences": {"assistant_mode": {"value": mode}}})
            self._schedule_session_reload()
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": f"Mode set to: {mode}."}
            )

        if name == "set_voice":
            voice = _normalize_voice(args.get("voice", ""))
            if not voice:
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": ("Invalid voice. Use: "
                                          + ", ".join(VOICE_NAMES) + ".")}
                )
            update_memory({"preferences": {"assistant_voice": {"value": voice}}})
            self._schedule_session_reload()
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": f"Voice set to: {voice}."}
            )

        if name == "set_mood":
            mood = str(args.get("mood", "")).strip().lower()
            valid = {"calm", "playful", "romantic", "strict"}
            if mood not in valid:
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": f"Invalid mood '{mood}'. Use: calm, playful, romantic, strict."}
                )
            update_memory({"preferences": {"assistant_mood": {"value": mood}}})
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": f"Mood set to: {mood}."}
            )

        result = "Done."

        # ── Loop guard: stop identical-arg / ping-pong tool loops in live mode ──
        gate_result = await self._run_safety_gate(name, args)
        if gate_result is not None:
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": gate_result.content}
            )
        try:
            # Uniform dispatch through the shared handler table so every
            # declared tool gets a timeout.
            entry = self._tool_handlers.get(name)
            if entry is not None:
                handler, timeout = entry
                from agent.resilience import run_sync_tool
                r = await run_sync_tool(lambda: handler(args), name, timeout=timeout)
                result = r.content or f"{name} done."
            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            log_tool(name, args, error=e)
            self.ui.write_log(f"ERR: {name} — {str(e)[:120]}")
            try:
                self.speak(f"Sir, {name} hit an error. {str(e)[:80]}")
            except Exception:
                pass


        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        log_tool(name, args, result=result)
        print(f"[FRIDAY] 📤 {name} → {str(result)[:80]}")

        # ── Result: tek cümle söyle, dur ──────────────────────────────────────
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    def _schedule(self, args: dict) -> str:
        """In-app timed actions: when they fire, Friday speaks reminders
        instead of relying on Windows Task Scheduler."""
        action = str(args.get("action", "list")).lower().strip()
        now = time.time()

        with self._sched_lock:
            if action in ("set", "add", "create"):
                try:
                    seconds = max(10, int(args.get("seconds", 60)))
                except (TypeError, ValueError):
                    return "I need `seconds` as a number."
                message = str(args.get("message", "Reminder")).strip() or "Reminder"
                sid = max((s.get("id", 0) for s in self._schedules), default=0) + 1
                self._schedules.append({
                    "id": sid, "fire_at": now + seconds,
                    "message": message, "fired": False,
                })
                when = self._fmt_time(now + seconds)
                return (f"Scheduled: '{message}' in {seconds}s (ID {sid}, fires at {when}). "
                        "I will speak up and display a notification.")

            if action in ("cancel", "delete", "remove"):
                try:
                    sid = int(args.get("id", 0))
                except (TypeError, ValueError):
                    return "I need `id` as a number."
                for s in self._schedules:
                    if s.get("id") == sid:
                        self._schedules.remove(s)
                        return f"Cancelled schedule ID {sid}."
                return f"No scheduled action with ID {sid}, sir."

            # list / status
            pending = [s for s in self._schedules if not s.get("fired")]
            if not pending:
                return "No active scheduled actions, sir."
            lines = [f"ID {s['id']} → '{s['message']}' at {self._fmt_time(s['fire_at'])}"
                     for s in pending[:8]]
            return "Active schedules: " + "; ".join(lines)

    @staticmethod
    def _fmt_time(ts: float) -> str:
        try:
            return time.strftime("%I:%M %p", time.localtime(ts))
        except Exception:
            return "?"

    async def _scheduler_loop(self):
        """Fire due scheduled actions — speak + notify, once each."""
        try:
            from actions.notifications import notify
        except Exception:
            notify = None
        while True:
            try:
                due = []
                with self._sched_lock:
                    for s in self._schedules:
                        if not s.get("fired") and time.time() >= s.get("fire_at", 0):
                            due.append(s)
                            s["fired"] = True
                for s in due:
                    msg = f"⏰ Reminder: {s['message']}"
                    print(f"[Scheduler] 🔔 {msg}")
                    if self.session:
                        self.speak(msg)
                    self.ui.write_log(msg)
                    if notify:
                        try:
                            await asyncio.to_thread(
                                notify,
                                {"title": "Friday", "message": s["message"]},
                                self.ui)
                        except Exception:
                            pass
            except Exception as e:
                print(f"[Scheduler] ⚠️ {e}")
            await asyncio.sleep(5)

    async def _monitor_loop(self, interval: float = 30.0):
        """Background watchdog: speak + push any newly-triggered alert."""
        from actions.monitor import check_rules as check_monitor_rules, check_email_watch
        from actions.guardian import check_guardian
        from actions.notifications import notify as _notify_action
        while True:
            try:
                alerts = await asyncio.to_thread(check_monitor_rules)
                for text in alerts:
                    print(f"[Monitor] 🔔 {text}")
                    self.ui.write_log(text)
                    if self.session:
                        self.speak(text)
            except Exception as e:
                print(f"[Monitor] ⚠️ {e}")
            try:
                alerts = await asyncio.to_thread(check_guardian)
                for text in alerts:
                    print(f"[Guardian] 🔔 {text}")
                    self.ui.write_log(text)
                    await asyncio.to_thread(
                        _notify_action,
                        {"title": "PC Health", "message": text},
                        self.ui)
                    if self.session:
                        self.speak(text)
            except Exception as e:
                print(f"[Guardian] ⚠️ {e}")
            try:
                for text in await asyncio.to_thread(check_email_watch):
                    print(f"[Email] 🔔 {text}")
                    self.ui.write_log(text)
                    await asyncio.to_thread(
                        _notify_action,
                        {"title": "Friday", "message": text},
                        self.ui)
                    if self.session:
                        self.speak(text)
            except Exception as e:
                print(f"[Email] ⚠️ {e}")
            await asyncio.sleep(interval)

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            async with self._send_lock:
                await self.session.send_realtime_input(
                    audio=types.Blob(data=msg["data"], mime_type=msg["mime_type"])
                )

    async def _listen_audio(self):
        print("[FRIDAY] 🎤 Mic started")
        loop = asyncio.get_running_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                assistant_speaking = self._is_speaking
            if not assistant_speaking and not self.ui.muted:
                data = indata.tobytes()
                loop.call_soon_threadsafe(self._safe_put_mic, data)

            # Voice reactivity: feed live mic energy to the Siri orb so the
            # ribbons respond to the user's voice in real time. Kept cheap and
            # throttled (~30 Hz) so the audio callback never blocks and the
            # cross-thread dispatcher is never flooded.
            try:
                if (np is not None and not assistant_speaking
                        and not self.ui.muted):
                    rms = float(np.sqrt(np.mean(
                        np.square(indata.astype(np.float32))))) / 32768.0
                    amp = min(1.0, rms * 3.0)
                else:
                    amp = 0.0
            except Exception:
                amp = 0.0
            now = time.monotonic()
            if not assistant_speaking and now - self._last_mic_amp_ts > 0.03:
                self._last_mic_amp_ts = now
                self.ui._dispatch(self.ui.bridge.setAmplitude, amp)

        if sd is None:
            print("[FRIDAY] ⚠️ sounddevice not available — mic disabled")
            while True:
                await asyncio.sleep(3600)

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[FRIDAY] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[FRIDAY] ⚠️ Microphone unavailable ({e}) — continuing in text mode")
            self.ui.write_log(f"SYS: Mic unavailable ({str(e)[:60]}).")
            while True:
                await asyncio.sleep(3600)

    def _safe_put_mic(self, data):
        try:
            self.out_queue.put_nowait({"data": data, "mime_type": "audio/pcm"})
        except asyncio.QueueFull:
            pass

    async def _watch_speaking(self):
        """Backstop for a "speaking" flag that nothing else released.

        This used to fire on every normal reply: it compared against
        ``_last_play_ts`` (the last chunk that *arrived* from the network) with
        a 25s ceiling. Whenever the model went quiet to run a tool call, the
        flag stayed set -- the player never sees a ``turn_complete`` in that
        case -- and roughly one reply later the watchdog "force-cleared" a flag
        that had simply been forgotten. Meanwhile audio ducking stayed engaged.

        It now measures silence from the last chunk actually handed to the
        speaker, and only clears when the queue has been empty for the whole
        grace period, so it cannot cut a reply off mid-sentence.
        """
        while True:
            await asyncio.sleep(SPEAKING_WATCHDOG_TICK_S)
            with self._speaking_lock:
                speaking = self._is_speaking
                last     = self._last_audio_write_ts
            if not speaking or not last:
                continue
            silent_for = time.monotonic() - last
            if (silent_for > SPEAKING_WATCHDOG_S
                    and _speech_should_release(silent_for,
                                               self.audio_in_queue.empty())):
                print(f"[FRIDAY] ⏱ Speaking watchdog fired — "
                      f"{silent_for:.1f}s of silence, force-cleared")
                self.set_speaking(False)

    async def _receive_audio(self):
        print("[FRIDAY] 👂 Recv started")
        out_buf, in_buf = [], []
        input_approval_handled = False

        try:
            while True:
                async for response in self.session.receive():

                    if response.data and not getattr(self.ui, "silent_mode", False):
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            if not getattr(self.ui, "silent_mode", False):
                                self.set_speaking(True)
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                out_buf.append(txt)
                                self.ui.set_live_transcript(" ".join(out_buf))

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                in_buf.append(txt)
                                self.ui.set_live_transcript(" ".join(in_buf))

                        if sc.turn_complete:
                            self._turn_complete = True

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                if not input_approval_handled and self._is_explicit_approval(full_in):
                                    self._mark_human_confirmed()
                            in_buf = []
                            input_approval_handled = False

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Friday: {full_out}")
                            out_buf = []



                            self._update_rolling(full_in, full_out)

                            if full_in and len(full_in) > 5:
                                threading.Thread(
                                    target=_update_memory_async,
                                    args=(full_in, full_out),
                                    daemon=True
                                ).start()

                            # Persist a rolling summary every ~10 turns so a
                            # restart never wipes long-run context entirely.
                            with self._phase_lock:
                                n_turns = self._roll_turns
                            if n_turns >= 10:
                                self._persist_rolling_summary()

                    if response.tool_call:
                        # Live can emit a tool call before turn_complete. Arm an
                        # exact spoken affirmation before dispatch, not afterward.
                        if not input_approval_handled and self._is_explicit_approval(" ".join(in_buf)):
                            self._mark_human_confirmed()
                            input_approval_handled = True
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[FRIDAY] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
                        # ── Boş turn YOK — bu "Anladım." sorununu yaratıyordu ──

        except Exception as e:
            print(f"[FRIDAY] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[FRIDAY] 🔊 Play started")
        if sd is None:
            print("[FRIDAY] ⚠️ sounddevice not available — audio output disabled")
            while True:
                chunk = await self.audio_in_queue.get()
                self.set_speaking(False)

        stream = None
        try:
            stream = sd.RawOutputStream(
                samplerate=RECEIVE_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
            )
            stream.start()
            while True:
                # ── drain with a bounded wait ───────────────────────────────
                # The old loop blocked forever on the first `get()`, so once a
                # turn's audio had played out it could not notice that the
                # reply was over until the *next* turn produced a chunk: the
                # speaking flag and the audio ducking stayed engaged the whole
                # time in between. Waiting with a timeout lets the release run
                # between turns as well as within one.
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=SPEAKING_WATCHDOG_TICK_S,
                    )
                except asyncio.TimeoutError:
                    silent_for = (time.monotonic()
                                  - self._last_audio_write_ts)
                    if self._is_speaking and (
                            self._turn_complete
                            or _speech_should_release(
                                silent_for, self.audio_in_queue.empty())):
                        self._turn_complete = False
                        self.set_speaking(False)
                    continue

                if getattr(self.ui, "silent_mode", False):
                    self.set_speaking(False)
                    continue
                if not self._is_speaking:
                    self.set_speaking(True)
                self._last_play_ts = time.monotonic()
                if np is not None and self._last_play_ts - self._last_speaker_amp_ts >= 0.03:
                    try:
                        samples = np.frombuffer(chunk, dtype='<i2').astype(np.float32)
                        amp = min(1.0, float(np.sqrt(np.mean(samples * samples))) / 32768.0 * 3.0) if samples.size else 0.0
                    except (ValueError, TypeError):
                        amp = 0.0
                    self._last_speaker_amp_ts = self._last_play_ts
                    self.ui._dispatch(self.ui.bridge.setAmplitude, amp)
                await asyncio.to_thread(stream.write, chunk)
                self._last_audio_write_ts = time.monotonic()

                # Release only after the queue has been *continuously* empty
                # for the grace period. A single `.empty()` sample is true
                # between every chunk at the network's natural pacing, which
                # is what truncated replies mid-sentence.
                if self._turn_complete and self.audio_in_queue.empty():
                    deadline = time.monotonic() + AUDIO_END_GRACE_S
                    while (time.monotonic() < deadline
                           and self.audio_in_queue.empty()):
                        await asyncio.sleep(0.02)
                    if self.audio_in_queue.empty():
                        self._turn_complete = False
                        self.set_speaking(False)
        except Exception as e:
            print(f"[FRIDAY] ⚠️ Audio output failed ({e}) — running in silent mode")
            self.ui.write_log(f"SYS: Speaker unavailable ({str(e)[:60]}).")
            self.set_speaking(False)
            while True:
                chunk = await self.audio_in_queue.get()
                self.set_speaking(False)
        finally:
            self.set_speaking(False)
            if stream:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass

    async def run(self):
        self._loop = asyncio.get_running_loop()

        client = None
        backoff = 3
        while True:
            try:
                if client is None:
                    try:
                        client = genai.Client(
                            api_key=_get_api_key(),
                            http_options={"api_version": "v1beta"}
                        )
                    except KeyError:
                        msg = ("No Gemini API key configured. Open "
                               "config/api_keys.json and add gemini_api_key.")
                        log(msg, level="ERROR")
                        self.ui.write_log(f"SYS: {msg}")
                        self.ui.set_state("THINKING")
                        self.ui.set_connecting(False)
                        await asyncio.sleep(5)
                        continue
                    except Exception as ke:
                        log(f"API client error: {ke}", level="ERROR")
                        await asyncio.sleep(5)
                        continue
                print("[FRIDAY] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                self.ui.set_connecting(True)
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    backoff = 3  # success resets the backoff
                    self.session        = session
                    self._loop          = asyncio.get_running_loop()
                    self._send_lock     = asyncio.Lock()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=64)
                    self._ready         = True

                    log("Gemini session CONNECTED")
                    print("[FRIDAY] ✅ Connected.")
                    self.ui.set_connection(True)
                    self.ui.set_persona(*self._persona_info())
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: Friday online.")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._watch_speaking())
                    tg.create_task(self._scheduler_loop())
                    tg.create_task(self._monitor_loop())

            except Exception as e:
                error_text = str(e)
                error_code = getattr(e, "code", None) or getattr(e, "status", None)
                schema_failure = (
                    str(error_code) == "1007"
                    and any(marker in error_text.lower() for marker in
                            ("bidigeneratecontentrequest", "missing field", "invalid frame payload"))
                ) or (
                    "1007" in error_text
                    and any(marker in error_text.lower() for marker in
                            ("bidigeneratecontentrequest", "missing field", "invalid frame payload"))
                )
                if schema_failure:
                    message = ("Fatal Gemini Live setup schema error (1007). "
                               "FRIDAY will not reconnect-loop; validate TOOL_DECLARATIONS "
                               "against google-genai Schema (ARRAY fields need items).")
                    log(f"{message} Details: {error_text}", level="ERROR")
                    self.ui.write_log(f"SYS: {message}")
                    self._ready = False
                    self.session = None
                    self.ui.set_connection(False)
                    self.ui.set_connecting(False)
                    self.ui.set_state("THINKING")
                    return
                log(f"Session error: {e}", level="ERROR")
                print(f"[FRIDAY] ⚠️ {e}")
                traceback.print_exc()
                # Rotate the key on quota/exhaustion so a dead key can't
                # pin the reconnect loop forever.
                try:
                    from api_keys import mark_exhausted
                    if client is not None and _looks_like_quota(str(e)):
                        key = getattr(getattr(client, "_api_client", None),
                                      "api_key", "")
                        if key:
                            mark_exhausted(key)
                        client = None
                except Exception:
                    pass

            self._ready         = False
            self.session        = None
            self.out_queue      = None
            self.audio_in_queue = None
            self.set_speaking(False)
            self.ui.set_connection(False)
            self.ui.set_state("THINKING")
            log(f"Session lost — reconnecting in {backoff}s", level="WARN")
            print(f"[FRIDAY] 🔄 Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)



def _tk_exception_handler(exc_type, exc_value, tb):
    """Suppress the noisy 'Exception in callback' block when the user
    presses Ctrl+C mid-draw; real errors still print normally."""
    if isinstance(exc_value, KeyboardInterrupt):
        return
    traceback.print_exception(exc_type, exc_value, tb)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='FRIDAY voice assistant')
    parser.add_argument('--headless', action='store_true', help='Run without showing the native island or tray')
    parser.add_argument('--minimized', action='store_true', help='Start with the HUD hidden')
    args = parser.parse_args(argv)
    _ensure_core_deps()
    log_file = setup_logger(BASE_DIR)
    log(f"Base dir: {BASE_DIR}\nLog file: {log_file}")
    ui = make_ui('face.png', headless=args.headless)
    if args.minimized and not args.headless:
        ui.hud.hide()
    task_ref = {}
    task_lock = threading.Lock()

    def runner():
        ui.wait_for_api_key()
        if ui._closed.is_set(): return
        friday = FridayLive(ui)
        if not args.headless:
            ui.root.after(0, friday.attach_tray)
        async def run_voice():
            with task_lock:
                task_ref['loop'] = asyncio.get_running_loop()
                task_ref['task'] = asyncio.current_task()
            if ui._closed.is_set(): return
            await friday.run()
        try:
            asyncio.run(run_voice())
        except asyncio.CancelledError:
            pass
        except Exception:
            traceback.print_exc()
            ui.write_log('ERR: The assistant stopped unexpectedly.')

    worker = threading.Thread(target=runner, daemon=True, name='FridayVoice')
    worker.start()
    ui.root.report_callback_exception = _tk_exception_handler
    try:
        ui.root.mainloop()
    except KeyboardInterrupt:
        ui.quit()
    finally:
        ui._closed.set()
        with task_lock:
            loop, task = task_ref.get('loop'), task_ref.get('task')
            if loop and task and not loop.is_closed():
                loop.call_soon_threadsafe(task.cancel)
        worker.join(timeout=3)
        try:
            from actions.system_tray import stop as stop_tray
            stop_tray()
        except Exception:
            pass


def _graceful_shutdown(ui) -> None:
    """Request an orderly UI shutdown."""
    ui.quit()


if __name__ == "__main__":
    main()
