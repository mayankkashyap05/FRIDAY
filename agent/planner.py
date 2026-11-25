import json
import re
import sys
from pathlib import Path

from config.models import MODEL_FLASH_LITE


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


PLANNER_PROMPT = """You are the planning module of Friday, a personal AI assistant.
Your job: break any user goal into a sequence of steps using ONLY the tools listed below.

ABSOLUTE RULES:
- NEVER use generated_code or write Python scripts. It does not exist.
- NEVER reference previous step results in parameters. Every step is independent.
- Use web_search for ANY information retrieval, research, or current data.
- Use file_controller to save content to disk.
- Use cmd_control to open files or run system commands.
- Max 5 steps. Use the minimum steps needed.

AVAILABLE TOOLS AND THEIR PARAMETERS:

open_app
  app_name: string (required) — app name or safe http(s)/ms-settings URL
  action: "open" | "focus" | "is_running" | "status" | "list" (optional)
  focus_if_running: bool (optional), refresh: bool (optional), args: list (optional)

web_search
  query: string — write a clear, focused search query
  mode: "search" | "news" | "compare" | "quick" (optional, default: search)
  items: list of strings or comma-separated string (for compare mode)
  aspect: string (optional, for compare mode)
  time_range: "day" | "week" | "month" | "year" (optional)
  region: string e.g. in-en, us-en (optional)
  max_results: 1-10 (optional, default 5), include_sources: boolean (optional)

game_updater
  action: "update" | "install" | "launch" | "largest" | "close_game" | "list" | "download_status" | "schedule" | "cancel_shutdown" (required)
  platform: "steam" | "epic" | "both" (optional, default: both)
  game_name: string (optional)
  app_id: string (optional)
  shutdown_when_done: boolean (optional) — cancellable with action "cancel_shutdown"

browser_control
  action: "go_to" | "search" | "click" | "type" | "fill" | "snapshot" | "hover" | "select" |
          "wait_for" | "extract_links" | "extract_text" | "screenshot" | "back" |
          "forward" | "reload" | "page_info" | "new_tab" | "list_tabs" |
          "switch_tab" | "close_tab" | "scroll" | "get_text" | "press" | "close" (required)
  url: string (for go_to/new_tab), allow_local: boolean (explicit localhost only)
  query: string (for search), selector/text/description: semantic or CSS target
  value: string (for select), index: 1-based tab index, timeout: 1-30s (wait_for)
  press_enter: boolean (type/fill), direction: "up" | "down" (for scroll)

file_controller
  action: "write" | "create_file" | "read" | "list" | "delete" | "move" | "copy" | "find" | "disk_usage" | "zip" | "unzip" | "organize" | "undo_organize" | "duplicates" | "search_content" | "recent" (required)
  path: string — use "desktop" for Desktop folder
  name: string — filename
  content: string — file content (for write/create_file)
  destination: string — target for move/copy/zip/unzip
  text: string — needle for search_content; hours: number for recent

notify
  action: "send" | "history" | "clear_history" (optional, default send)
  message: string (required for send) — notification text
  title: string (optional), duration: short|long, sound: default|none|alarm|reminder, url: http(s) link (optional)

gmail
  action: "send" | "read" | "search" | "read_message" | "draft" | "reply" | "mark_read" | "mark_unread" | "unread_count" | "trash"
  to, subject, body, cc, bcc: string; attachments: list of local paths; message_id: string; query: string; limit: integer

drive
  action: "list" | "search" | "read" | "info" | "upload" | "download" | "create_folder" | "trash"
  query, file_id, name, content, path, folder_id: string (as needed)

monitor_alerts
  action: "list" | "add" | "remove" | "check" | "status" | "pause" | "resume" | "clear"
  kind: cpu | ram | disk | disk:D | battery | temp | process_exit; sustained, cooldown: seconds

pc_health
  action: "status" | "report" | "enable" | "disable" | "check" | "backup" | "set" | "reset" | "snooze" | "unsnooze"
  minutes: number (for snooze)

email_watch
  action: "enable" | "disable" | "status" | "pause" | "resume"; sender, subject, unread_only filters (optional)

media_control
  action: "play" | "pause" | "toggle" | "next" | "prev" | "volume_up" | "volume_down" | "volume_set" | "mute" | "status" | "now_playing"
  volume: 0-100, steps: integer (optional)

daily_briefing
  city: string (optional) — weather defaults to saved memory
  sections: string (optional) — comma-separated: weather,calendar,email,reminders,tasks,system,memory,news
  news: boolean (optional, opt-in), detailed: boolean (optional)

wake_word
  action: "start" | "stop" | "status" | "pause" | "resume" | "set_threshold" | "devices" | "diagnostics" | "record" | "train" (required)
  threshold: 0.1-0.99, cooldown: seconds, device: int, clips: int (all optional)

cmd_control
  action: "run" | "history"
  task: string — natural language description (safe mapped intents only)
  command: explicit command (still screened), shell: cmd|powershell, cwd: string
  timeout: 1-120 (default 30), dry_run: boolean, visible: boolean

computer_settings
  action: string (required) — also volume_get, brightness_get, mute, unmute, toggle_mute,
    dark_mode_on, light_mode_on, list_audio_apps, unmute_app, cancel_shutdown, sleep_pc, hibernate
  description: string — natural language description
  value: string (optional), app_name: string (optional)

computer_control
  action: "type" | "smart_type" | "click" | "triple_click" | "middle_click" | "hover" | "mouse_pos" |
          "hotkey" | "press" | "scroll" | "drag" | "screenshot" | "screen_find" |
          "screen_click" | "list_windows" | "active_window" | "window_action" | "focus_window" (required)
  text: string (for type)
  x, y: int (for click)
  keys: string (for hotkey, e.g. "ctrl+c")
  key: string (for press)
  direction: "up" | "down" (for scroll)
  description: string (for screen_find/screen_click)
  title: string (for focus_window)

system_status
  focus: "overview" | "battery" | "cpu" | "memory" | "disk" | "network" | "processes" | "gpu" | "temperature" | "uptime" | "hardware" (default overview)
  format: "brief" | "detailed" | "json", sort_by: memory|cpu, limit: 1-20, fresh: boolean

task_manager
  action: "list" | "status" | "cancel" | "summary" | "processes" | "find_process" | "kill" (required)
  task_id: string (or "latest"/"all"), status_filter, limit
  process/name/pid: target for find_process or kill; sort_by: memory|cpu; all_matches, force

clipboard
  action: "get" | "set" | "paste" | "clear" | "history" | "copy_last" | "copy_index" |
          "restore" | "pin" | "unpin" | "pinned" | "search" | "append" |
          "transform" | "info" | "stats" | "formats" | "get_files" | "save_image"
  text/index/limit/query/transform/separator/save_path: optional parameters

translate
  action: "translate" | "detect" | "transliterate" | "explain" | "clipboard" | "batch"
  text / texts, to, from, tone, copy, use_clipboard, no_cache

recall_memory
  query: string (required)
  category: "identity" | "preferences" | "projects" | "relationships" | "wishes" | "notes" (optional)

vision_gesture
  action: "start" | "stop" | "mode" | "face_count" | "qr" | "snapshot" | "status" (required)
  mode: "gesture" | "air_mouse" | "volume" | "motion" | "posture" | "objects" | "focus"
  sensitivity: motion threshold (default 0.025), grace: focus away seconds (default 10)

screen_process
  text: string (required) — what to analyze or ask about the screen
  angle: "screen" | "window" | "region" | "camera" (optional)
  detail: "fast" | "balanced" | "high" | "ocr" (optional, default balanced)
  monitor: 0 (all) | 1 (primary) | 2+ (optional), region: [x,y,w,h], save_path, concise, camera_index

vision_click
  target: string (required) — UI element to find on screen
  click: "left" | "double" | "triple" | "right" | "middle" | "hover", monitor: 0- N, only_coords, type_text, press_enter

send_message
  receiver: string (required) — contact, phone number or @username
  message_text: string (required)
  platform: string (optional, default whatsapp), send: bool (default true), dry_run: bool, web: bool

reminder
  message: string (required)
  action: "set" | "list" | "delete" | "clear" (optional, default set)
  date: string YYYY-MM-DD, time: string HH:MM or "3:30 PM", when: "in 15 minutes" | "tomorrow 09:00"
  in_minutes / in_hours / in_seconds: number, repeat: "once" | "daily" | "weekly"

read_pdf
  path: string (required), action: "preview" | "summary" | "search" | "metadata" | "pages"
  pages: "1-3" | "5" | "1,3", layout: bool, visual: bool, password: string, query: string

pdf_qa
  path: string (required), question: string (required)
  pages, layout, visual, password (all optional)

smart_home_control
  action: "turn_on" | "turn_off" | "toggle" | "set_level" | "set_temperature" | "set_color" |
          "open" | "close" | "lock" | "unlock" | "activate" | "status" | "health" | "discover"
  device: id/name/room/group (optional), value: level or temperature
  room, color, temperature, hvac_mode, domain, query (optional)

desktop_control
  action: "wallpaper" | "wallpaper_url" | "current_wallpaper" | "undo_wallpaper" | "organize" |
          "clean" | "undo_organize" | "undo_clean" | "list" | "stats" | "task" (required)
  path: string (optional), url: string (optional), dry_run: boolean (optional)
  sort_by: name|size|date|type, limit: int (optional)
  task: string (optional)

youtube_video
  action: "open" | "search" | "play" | "pause" | "resume" | "toggle_play" | "seek" | "forward" | "rewind" | "restart" | "next" | "previous" | "volume" | "mute" | "unmute" | "speed" | "fullscreen" | "theater" | "miniplayer" | "captions" | "skip_ad" | "now_playing" | "summarize" | "get_info" | "trending" | "close" (required)
  query: string, url: string, index: int, seconds: number, offset: number, timestamp: string, volume: 0-100, speed: 0.25-4.0, category: general|music|gaming|movies|podcasts, include_shorts: boolean, region: string (all optional)

weather_report
  city: string (optional — defaults to saved memory city)
  time: "now" | "tonight" | "tomorrow" | "week" | "weekend" | weekday, units: metric|imperial, include_aqi: boolean, open_browser: boolean (all optional)

flight_finder
  origin: string (required)
  destination: string (required)
  date: string (required)
  passengers: integer, cabin: economy|premium|business|first, currency: string (default INR)
  non_stop: boolean, max_stops: integer, sort_by: price|duration, save: boolean (all optional)

code_helper
  action: "write" | "edit" | "explain" | "run" | "build" | "optimize" | "refactor" |
          "undo_edit" | "review" | "audit" | "test" | "generate_tests" | "screen_debug" | "auto"
  description/instruction: string, language: string, output_path/file_path: string
  dry_run: boolean (unified diff preview), save/run_tests: boolean, timeout: 1-120

calendar
  action: list | today | tomorrow | week | search | add | update | reschedule | delete | free_busy | find_slot | calendars
  title/summary, start, end, duration_minutes, all_day, location, attendees, recurrence,
  conference, reminder_minutes, allow_overlap, event_id, calendar_id: optional

dev_agent
  action: "build" | "list_projects" | "run_project" | "fix_project" (default: build)
  description: string (required for build)
  language: string (optional), project_name: string (optional)
  open_editor: boolean (optional), run_tests: boolean (optional)

read_document
  source: string (required) — path/name/URL
  action: "preview" | "summary" | "search" | "stats" (optional)
  query: string (for search), fresh: boolean (optional)

document_qa
  source: string (required), question: string (required), fresh: boolean (optional)
EXAMPLES:

Goal: "research mechanical engineering and save it to a notepad file"
Steps:

web_search | query: "mechanical engineering overview definition history"
web_search | query: "mechanical engineering applications and future trends"
file_controller | action: write, path: desktop, name: mechanical_engineering.txt, content: "MECHANICAL ENGINEERING RESEARCH\n\nThis file will be filled with web research results."
cmd_control | task: "open mechanical_engineering.txt on desktop with notepad"

Goal: "What is the price of Bitcoin"
Steps:

web_search | query: "Bitcoin price today USD"

Goal: "List the files on the desktop and find the largest 5 files"
Steps:

file_controller | action: list, path: desktop
file_controller | action: largest, path: desktop, count: 5

Goal: "Install PUBG from Steam"
Steps:

game_updater | action: install, platform: steam, game_name: "PUBG"

Goal: "Update all my Steam games"
Steps:

game_updater | action: update, platform: steam

Goal: "Send John a message on WhatsApp saying there is a meeting tomorrow"
Steps:

send_message | receiver: John, message_text: "There is a meeting tomorrow", platform: WhatsApp

Goal: "Open the clock and set a reminder for 30 minutes later"
Steps:

reminder | date: [today], time: [now+30min], message: "Reminder"

OUTPUT — return ONLY valid JSON, no markdown, no explanation, no code blocks:
{
  "goal": "...",
  "steps": [
    {
      "step": 1,
      "tool": "tool_name",
      "description": "what this step does",
      "parameters": {},
      "critical": true
    }
  ]
}
"""


def _get_api_key() -> str:
    from api_keys import next_key
    return next_key()


def create_plan(goal: str, context: str = "") -> dict:
    import google.generativeai as genai

    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(
        model_name=MODEL_FLASH_LITE,
        system_instruction=PLANNER_PROMPT
    )

    user_input = f"Goal: {goal}"
    if context:
        user_input += f"\n\nContext: {context}"

    try:
        response = model.generate_content(user_input)
        text     = response.text.strip()
        text     = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

        plan = json.loads(text)

        if "steps" not in plan or not isinstance(plan["steps"], list):
            raise ValueError("Invalid plan structure")

        for step in plan["steps"]:
            if step.get("tool") in ("generated_code",):
                print(f"[Planner] ⚠️ generated_code detected in step {step.get('step')} — replacing with web_search")
                desc = step.get("description", goal)
                step["tool"] = "web_search"
                step["parameters"] = {"query": desc[:200]}

        print(f"[Planner] ✅ Plan: {len(plan['steps'])} steps")
        for s in plan["steps"]:
            print(f"  Step {s['step']}: [{s['tool']}] {s['description']}")

        return plan

    except json.JSONDecodeError as e:
        print(f"[Planner] ⚠️ JSON parse failed: {e}")
        return _fallback_plan(goal)
    except Exception as e:
        print(f"[Planner] ⚠️ Planning failed: {e}")
        return _fallback_plan(goal)


def _fallback_plan(goal: str) -> dict:
    print("[Planner] 🔄 Fallback plan")
    return {
        "goal": goal,
        "steps": [
            {
                "step": 1,
                "tool": "web_search",
                "description": f"Search for: {goal}",
                "parameters": {"query": goal},
                "critical": True
            }
        ]
    }


def replan(goal: str, completed_steps: list, failed_step: dict, error: str) -> dict:
    import google.generativeai as genai

    genai.configure(api_key=_get_api_key())
    from config.models import MODEL_FLASH
    model = genai.GenerativeModel(
        model_name=MODEL_FLASH,
        system_instruction=PLANNER_PROMPT
    )

    completed_summary = "\n".join(
        f"  - Step {s['step']} ({s['tool']}): DONE" for s in completed_steps
    )

    prompt = f"""Goal: {goal}

Already completed:
{completed_summary if completed_summary else '  (none)'}

Failed step: [{failed_step.get('tool')}] {failed_step.get('description')}
Error: {error}

Create a REVISED plan for the remaining work only. Do not repeat completed steps."""

    try:
        response = model.generate_content(prompt)
        text     = response.text.strip()
        text     = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        plan     = json.loads(text)

        if "steps" not in plan or not isinstance(plan["steps"], list):
            raise ValueError("Invalid revised plan structure")

        for step in plan["steps"]:
            if step.get("tool") == "generated_code":
                step["tool"] = "web_search"
                step["parameters"] = {"query": step.get("description", goal)[:200]}

        print(f"[Planner] 🔄 Revised plan: {len(plan['steps'])} steps")
        return plan
    except Exception as e:
        print(f"[Planner] ⚠️ Replan failed: {e}")
        return _fallback_plan(goal)