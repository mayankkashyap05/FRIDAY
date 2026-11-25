# actions/daily_briefing.py
# Friday — time-aware executive briefing.
#
# Sections are gathered concurrently with a hard 6-second budget each, so a
# slow weather/OAuth call can never stall the whole briefing.  Optional
# sections (calendar, email, reminders, news) are gated before they run.

from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from xml.etree import ElementTree

SECTION_TIMEOUT_S = 6.0
MAX_SPOKEN_CHARS = 1600

# Stable display order — also the canonical list accepted by ``sections``.
SECTION_ORDER = (
    "weather", "calendar", "email", "reminders", "tasks",
    "system", "memory", "news",
)
DEFAULT_SECTIONS = (
    "weather", "calendar", "email", "reminders", "tasks", "system", "memory",
)
SECTION_LABELS = {
    "weather": "Weather",
    "calendar": "Calendar",
    "email": "Email",
    "reminders": "Reminders",
    "tasks": "Tasks",
    "system": "System",
    "memory": "What I remember",
    "news": "News",
}


def _get_time() -> str:
    now = datetime.now()
    return now.strftime("%A, %B %d %Y — %I:%M %p")


def _greeting(now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")
    return bool(value) if value is not None else default


def _memory_identity() -> dict:
    """Only identity fields: never raw conversation notes."""
    try:
        from memory.memory_manager import load_memory
        mem = load_memory() or {}
        identity = mem.get("identity", {}) if isinstance(mem, dict) else {}
        out = {}
        for key in ("name", "city", "location"):
            entry = identity.get(key, {})
            value = entry.get("value") if isinstance(entry, dict) else entry
            if value:
                out[key] = str(value).strip()
        return out
    except Exception:
        return {}


def _get_weather(city: str) -> str:
    if not city:
        return ""
    try:
        from actions.weather_report import weather_action
        text = weather_action(parameters={"city": city}, player=None)
        return str(text or "").strip()
    except Exception as e:
        return f"Weather unavailable: {e}"


def _get_calendar() -> str:
    try:
        import google_oauth
        if not google_oauth.has_token():
            return ""
        from actions.calendar import calendar_action
        text = calendar_action({"action": "list", "max_results": 3}, player=None)
        return str(text or "").strip()
    except Exception:
        return ""


def _get_email() -> str:
    try:
        import google_oauth
        if not google_oauth.has_token():
            return ""
        from actions.gmail import gmail_action
        text = gmail_action({"action": "unread_count"}, player=None)
        return str(text or "").strip()
    except Exception:
        return ""


def _get_reminders() -> str:
    import platform
    if platform.system() != "Windows":
        # Windows Task Scheduler reminders do not exist elsewhere.
        return ""
    try:
        from actions.reminder import reminder
        text = reminder({"action": "list"}, player=None)
        return str(text or "").strip()
    except Exception:
        return ""


def _get_tasks() -> str:
    try:
        from actions.task_manager import task_manager
        text = task_manager({"action": "summary"}, player=None)
        return str(text or "").strip()
    except Exception:
        return ""


def _get_system() -> str:
    try:
        from actions.system_status import system_status
        text = system_status(
            parameters={"focus": "overview", "format": "brief"}, player=None)
        return str(text or "").strip()
    except Exception:
        return ""


def _get_memory() -> str:
    """Curated identity/preference facts only — no raw notes or transcripts."""
    try:
        from memory.memory_manager import load_memory
        mem = load_memory() or {}
        if not isinstance(mem, dict):
            return ""
        lines = []
        identity = mem.get("identity", {}) or {}
        for key in ("name", "city", "job", "birthday", "language"):
            entry = identity.get(key, {})
            value = entry.get("value") if isinstance(entry, dict) else entry
            if value:
                lines.append(f"{key}: {value}")
        prefs = mem.get("preferences", {}) or {}
        for key, entry in list(prefs.items())[:4]:
            value = entry.get("value") if isinstance(entry, dict) else entry
            if key.lower().startswith(("note", "conversation")):
                continue
            if value:
                lines.append(f"{key.replace('_', ' ')}: {value}")
        facts = [line for line in lines
                 if "conversation_" not in line.lower()
                 and not line.lower().startswith("notes")]
        if not facts:
            # Honour format_memory_for_prompt overrides while still filtering
            # raw conversation transcripts.
            try:
                from memory.memory_manager import format_memory_for_prompt
                formatted = format_memory_for_prompt(mem) or ""
                for line in formatted.splitlines():
                    stripped = line.strip().lstrip("- ").strip()
                    lower = stripped.lower()
                    if (not stripped or len(stripped) > 120
                            or lower.startswith("notes")
                            or "conversation_" in lower
                            or lower.startswith("[")):
                        continue
                    facts.append(stripped)
            except Exception:
                pass
        return "; ".join(facts[:6])[:600]
    except Exception:
        return ""


def _get_news() -> str:
    """Opt-in headline section; silently skipped when offline."""
    try:
        import requests
        response = requests.get(
            "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-IN&gl=IN&ceid=IN:en",
            timeout=5,
        )
        root = ElementTree.fromstring(response.content)
        headlines = []
        for item in root.iter("item"):
            title = item.findtext("title")
            if title:
                headlines.append(title.strip())
            if len(headlines) >= 3:
                break
        return "; ".join(headlines)
    except Exception:
        return ""


_SECTION_FUNCS = {
    "weather": lambda ctx: _get_weather(ctx.get("city", "")),
    "calendar": lambda ctx: _get_calendar(),
    "email": lambda ctx: _get_email(),
    "reminders": lambda ctx: _get_reminders(),
    "tasks": lambda ctx: _get_tasks(),
    "system": lambda ctx: _get_system(),
    "memory": lambda ctx: _get_memory(),
    "news": lambda ctx: _get_news(),
}


def _parse_sections(value) -> list[str]:
    """Accept a list, a comma/space separated string, ``all`` or ``none``."""
    if value is None:
        return list(DEFAULT_SECTIONS)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("", "default", "full"):
            return list(DEFAULT_SECTIONS)
        if text == "all":
            return list(SECTION_ORDER)
        if text in ("none", "off"):
            return []
        tokens = [token.strip() for token in re.split(r"[,;|\s]+", text) if token.strip()]
        if "all" in tokens:
            return list(SECTION_ORDER)
        return [token for token in tokens if token in SECTION_ORDER]
    if isinstance(value, (list, tuple, set)):
        if not value:
            return list(DEFAULT_SECTIONS)
        return [str(item).strip().lower() for item in value
                if str(item).strip().lower() in SECTION_ORDER]
    return list(DEFAULT_SECTIONS)


def _collect_sections(requested: list[str], city: str, timeout: float = SECTION_TIMEOUT_S) -> dict:
    """Gather each section on a daemon thread with a hard per-section budget."""
    results: dict[str, str] = {}
    threads: list[threading.Thread] = []

    def work(name: str):
        try:
            text = _SECTION_FUNCS[name]({"city": city})
            results[name] = str(text or "").strip()
        except Exception as exc:
            print(f"[Briefing] {name} section failed: {exc}")
            results[name] = ""

    for name in requested:
        thread = threading.Thread(target=work, args=(name,), daemon=True,
                                  name=f"briefing-{name}")
        threads.append(thread)
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + min(max(0.0, float(timeout)), SECTION_TIMEOUT_S)
    for thread in threads:
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(timeout=remaining)
    return {name: results.get(name, "") for name in requested}


def _trim_spoken(text: str, limit: int = MAX_SPOKEN_CHARS) -> str:
    if len(text) <= limit:
        return text
    window = text[:limit]
    # Prefer the last sentence end, then the last word boundary.
    for terminator in (". ", "! ", "? "):
        idx = window.rfind(terminator)
        if idx > limit // 2:
            return window[:idx + 1].rstrip()
    return window.rsplit(" ", 1)[0].rstrip() + "…"


def daily_briefing(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Time-aware briefing: date, weather, calendar, mail, tasks and status."""
    params = parameters if isinstance(parameters, dict) else {}
    now = datetime.now()
    identity = _memory_identity()
    city = str(params.get("city") or identity.get("city") or identity.get("location") or "").strip()
    name = str(params.get("name") or identity.get("name") or "").strip()

    sections = _parse_sections(params.get("sections", params.get("focus")))
    if _as_bool(params.get("news"), False) and "news" not in sections:
        sections.append("news")

    gathered = _collect_sections(sections, city)

    lines = [f"{_greeting(now)}, {name or 'sir'}. Today is {_get_time()}."]
    for section in SECTION_ORDER:
        text = gathered.get(section, "")
        if not text:
            continue
        if section == "weather" and "unavailable" in text.lower():
            continue
        if section == "reminders" and re.search(r"\bno pending reminders\b", text, re.I):
            continue
        if section == "tasks" and re.search(r"\btasks:\s*0\s+total\b", text, re.I):
            continue
        lines.append(f"{SECTION_LABELS[section]}: {text}")

    spoken = " ".join(lines)
    spoken = re.sub(r"\s+", " ", spoken).strip()

    detailed = (_as_bool(params.get("detailed"), False)
                or str(params.get("format", "")).lower() in ("detailed", "digest", "full"))
    if detailed:
        return "\n\n".join(lines)

    if player:
        try:
            player.write_log(f"[Briefing] {len(lines)} section(s): {spoken[:120]}")
        except Exception:
            pass
    return _trim_spoken(spoken)
