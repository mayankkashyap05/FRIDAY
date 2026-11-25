"""actions/calendar.py — complete Google Calendar v3 assistant."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

try:
    from googleapiclient.discovery import build
except ImportError:                                        # optional dependency
    build = None

import google_oauth

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)


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


def _service():
    if build is None:
        raise RuntimeError("googleapiclient is not installed. Run: pip install google-api-python-client")
    creds = google_oauth.get_credentials()
    if not creds:
        raise RuntimeError(
            "Google Calendar is not authorized yet. Run 'google_auth' first and "
            "follow the sign-in instructions.")
    return build("calendar", "v3", credentials=creds)


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _is_date_only(value) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "").strip()))


def _parse_datetime(value=None, default: datetime | None = None) -> datetime | None:
    """Parse ISO, natural dates/times and relative phrases into an aware datetime."""
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip()
    v = raw.lower()
    tz = (default or _local_now()).tzinfo
    now = (default or _local_now()).astimezone(tz)

    # Full ISO/RFC3339 strings carry their own offset — keep it intact.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?", raw, re.IGNORECASE):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)
        except ValueError:
            return None

    relative = re.fullmatch(r"in\s+(\d+)\s+(minute|hour|day)s?", v)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = {"minute": timedelta(minutes=amount),
                 "hour": timedelta(hours=amount),
                 "day": timedelta(days=amount)}[unit]
        return now + delta

    # Replace the words with a date where possible, then parse a time suffix.
    base = now
    modified = False
    day_tokens = {
        "day after tomorrow": now + timedelta(days=2),
        "tomorrow": now + timedelta(days=1),
        "today": now,
    }
    for phrase, target in day_tokens.items():
        if phrase in v:
            base = target
            v = v.replace(phrase, " ")
            modified = True
            break

    weekday_match = re.search(
        r"(?:next|upcoming|this)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", v)
    if weekday_match:
        wanted = _WEEKDAYS[weekday_match.group(1)]
        ahead = (wanted - now.weekday()) % 7
        if ahead == 0:
            ahead = 7 if ("next" in v or now.time() > base.time()) else 0
        if "next" in v and ahead < 7:
            ahead += 7
        base = now + timedelta(days=ahead)
        v = v[:weekday_match.start()] + " " + v[weekday_match.end():]
        modified = True

    date_match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", v)
    if date_match:
        try:
            base = base.replace(year=int(date_match.group(1)),
                                month=int(date_match.group(2)),
                                day=int(date_match.group(3)))
            v = v[:date_match.start()] + " " + v[date_match.end():]
            modified = True
        except ValueError:
            return None

    time_match = _TIME_RE.search(v)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        meridiem = (time_match.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return base.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if modified and not time_match:
        return base
    if _is_date_only(raw):
        try:
            return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=tz)
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)
    except ValueError:
        return None


def _parse_time(value=None, default: datetime | None = None) -> str:
    """Backward-compatible RFC3339 string helper."""
    fallback = (default or _local_now())
    parsed = _parse_datetime(value, fallback)
    return (parsed or fallback).isoformat()


def _fmt(value: str) -> str:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d.%m %H:%M")
    except Exception:
        return str(value)


def _event_bounds(event: dict) -> tuple[str, str, bool]:
    start = (event.get("start") or {})
    end = (event.get("end") or {})
    return (start.get("dateTime") or start.get("date") or "?",
            end.get("dateTime") or end.get("date") or "?",
            "date" in start)


def _parse_event_dt(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.astimezone()
    except Exception:
        return None


def _format_event(event: dict) -> str:
    start, end, all_day = _event_bounds(event)
    when = f"{_fmt(start)} – {_fmt(end)}" if not all_day else f"{start} (all day)"
    parts = [f"• [{event.get('id', '?')}] {event.get('summary', '(no title)')}", f"  When: {when}"]
    if event.get("location"):
        parts.append(f"  Where: {event['location']}")
    hangout = (event.get("conferenceData", {}).get("entryPoints") or [])
    if hangout:
        parts.append(f"  Meet: {hangout[0].get('uri', '')}")
    elif event.get("hangoutLink"):
        parts.append(f"  Meet: {event['hangoutLink']}")
    return "\n".join(parts)


def _list_events(svc, calendar_id: str, time_min: str, time_max: str,
                 max_results: int, query: str = "") -> list[dict]:
    kwargs = dict(
        calendarId=calendar_id, timeMin=time_min, timeMax=time_max,
        maxResults=max_results, singleEvents=True, orderBy="startTime",
    )
    if query:
        kwargs["q"] = query
    return svc.events().list(**kwargs).execute().get("items", [])


def _day_bounds(base: datetime) -> tuple[str, str]:
    start = base.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _attendees(value) -> list[dict]:
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        items = [str(part).strip() for part in value]
    else:
        items = []
    return [{"email": email} for email in items if _EMAIL_RE.match(email)]


def _rrule(value: str) -> dict | None:
    mapping = {
        "daily": "RRULE:FREQ=DAILY;COUNT=30",
        "weekly": "RRULE:FREQ=WEEKLY;COUNT=12",
        "monthly": "RRULE:FREQ=MONTHLY;COUNT=6",
        "weekdays": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;COUNT=20",
    }
    rule = mapping.get(str(value or "").strip().lower())
    return {"recurrence": [rule]} if rule else {}


def _find_by_summary(svc, calendar_id: str, summary: str) -> tuple[dict | None, list[dict]]:
    now = _local_now()
    events = _list_events(svc, calendar_id, now.isoformat(),
                          (now + timedelta(days=365)).isoformat(), 50)
    needle = str(summary or "").strip().lower()
    matches = [ev for ev in events if needle and needle == str(ev.get("summary", "")).strip().lower()]
    if not matches:
        matches = [ev for ev in events if needle in str(ev.get("summary", "")).lower()]
    return (matches[0] if len(matches) == 1 else None), matches


def _free_slots(busy: list[tuple[datetime, datetime]], window_start: datetime,
                window_end: datetime, minutes: int) -> list[tuple[datetime, datetime]]:
    duration = timedelta(minutes=minutes)
    cursor = window_start
    slots = []

    def fill(from_dt, until_dt):
        while until_dt - from_dt >= duration and len(slots) < 40:
            slots.append((from_dt, from_dt + duration))
            from_dt += duration

    for start, end in sorted(busy):
        if end <= cursor:
            continue
        if start > cursor:
            fill(cursor, start)
        cursor = max(cursor, end)
    fill(cursor, window_end)
    return slots


def calendar_action(parameters: dict, response=None, player=None, session_memory=None) -> str:
    """Full calendar suite: list/search/add/update/delete/free-busy/calendars."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action", "list") or "list").strip().lower().replace("-", "_")
    calendar_id = str(params.get("calendar_id", "primary") or "primary")

    try:
        svc = _service()
    except Exception as exc:
        return f"Calendar error: {exc}"

    try:
        max_results = _clamp(params.get("max_results", 10), 1, 50, 10)

        if action in ("list", "upcoming", "agenda", "today", "tomorrow", "week", "search"):
            now = _local_now()
            if action == "today":
                time_min, time_max = _day_bounds(now)
            elif action == "tomorrow":
                time_min, time_max = _day_bounds(now + timedelta(days=1))
            elif action == "week":
                time_min, time_max = now.isoformat(), (now + timedelta(days=7)).isoformat()
            elif params.get("start"):
                time_min = _parse_time(params.get("start"), now)
                base = _parse_datetime(params.get("start"), now) or now
                time_max = _parse_time(params.get("end"), base + timedelta(days=1))
            else:
                time_min, time_max = now.isoformat(), (now + timedelta(days=30)).isoformat()
            query = str(params.get("query", "") or "") if action == "search" else ""
            events = _list_events(svc, calendar_id, time_min, time_max, max_results, query)
            if not events:
                return "No matching events found, sir."
            header = "📅 Search results:" if query else "📅 Upcoming events:"
            return "\n".join([header] + [_format_event(ev) for ev in events])

        if action in ("calendars", "list_calendars"):
            items = svc.calendarList().list().execute().get("items", [])
            rows = [f"• {item.get('summary', '(untitled)')} [{item.get('id')}]"
                    + (" (primary)" if item.get("primary") else "") for item in items]
            return "\n".join(rows) if rows else "No calendars found."

        if action in ("add", "create", "new"):
            title = str(params.get("title", params.get("summary", "")) or "").strip()
            if not title:
                return "I need the event title, sir."
            all_day = _as_bool(params.get("all_day"), False)
            now = _local_now()
            start_value = params.get("start") or (now + timedelta(hours=1)).isoformat()
            start = _parse_datetime(start_value, now)
            if start is None:
                return f"I couldn't understand the start time '{start_value}', sir."
            if all_day or _is_date_only(start_value):
                start = start.replace(hour=0, minute=0, second=0, microsecond=0)
                if params.get("end"):
                    end_dt = _parse_datetime(params.get("end"), start + timedelta(days=1)) or (start + timedelta(days=1))
                else:
                    end_dt = start + timedelta(days=1)
                body = {
                    "summary": title,
                    "start": {"date": start.strftime("%Y-%m-%d")},
                    "end": {"date": end_dt.strftime("%Y-%m-%d")},
                }
            else:
                minutes = _clamp(params.get("duration_minutes", 60), 5, 1440, 60)
                end = _parse_datetime(params.get("end"), start + timedelta(minutes=minutes))
                if end is None or end <= start:
                    end = start + timedelta(minutes=minutes)
                # Conflict check before any insert.
                conflicting = []
                for ev in _list_events(svc, calendar_id, start.isoformat(), end.isoformat(), 50):
                    ev_start, ev_end, ev_all_day = _event_bounds(ev)
                    a, b = _parse_event_dt(ev_start), _parse_event_dt(ev_end)
                    if ev_all_day or a is None or b is None:
                        continue
                    if a < end and b > start:
                        conflicting.append(str(ev.get("summary", "(no title)")))
                if conflicting and not (_as_bool(params.get("allow_overlap"), False)
                                        or _as_bool(params.get("force"), False)):
                    return ("Conflict detected — event not created, sir. Overlaps: "
                            + ", ".join(conflicting)
                            + ". Pass allow_overlap=true or force=true to create anyway.")
                body = {
                    "summary": title,
                    "start": {"dateTime": start.isoformat(), "timeZone": str(start.tzinfo or "")},
                    "end": {"dateTime": end.isoformat(), "timeZone": str(end.tzinfo or "")},
                }
            if params.get("description"):
                body["description"] = str(params["description"])
            if params.get("location"):
                body["location"] = str(params["location"])
            attendees = _attendees(params.get("attendees"))
            if attendees:
                body["attendees"] = attendees
            body.update(_rrule(str(params.get("recurrence", ""))))
            reminders = params.get("reminder_minutes")
            if reminders is not None:
                body["reminders"] = {
                    "useDefault": False,
                    "overrides": [{"method": "popup",
                                   "minutes": _clamp(reminders, 0, 10080, 10)}],
                }
            kwargs = {"calendarId": calendar_id, "body": body}
            if _as_bool(params.get("conference"), False) or _as_bool(params.get("meet"), False):
                body["conferenceData"] = {
                    "createRequest": {"requestId": f"friday-{int(start.timestamp())}"}
                }
                kwargs["conferenceDataVersion"] = 1
            created = svc.events().insert(**kwargs).execute()
            link = created.get("htmlLink", "") or created.get("hangoutLink", "")
            return f"✅ Added: '{title}' at {_fmt(start.isoformat())}. {link}".strip()

        if action in ("update", "reschedule", "edit"):
            event_id = str(params.get("event_id", "") or "").strip()
            if not event_id:
                event, matches = _find_by_summary(svc, calendar_id,
                                                  params.get("title", params.get("summary", "")))
                if not event:
                    if len(matches) > 1:
                        return f"Multiple events match that title ({len(matches)}); pass event_id to be safe."
                    return "No matching event found, sir."
                event_id = event.get("id", "")
            body = {}
            for key in ("summary", "location", "description"):
                if params.get(key):
                    body[key if key != "summary" else "summary"] = str(params[key])
            if params.get("title") and "summary" not in body:
                body["summary"] = str(params["title"])
            now = _local_now()
            if params.get("start"):
                start = _parse_datetime(params.get("start"), now)
                if start is None:
                    return f"I couldn't understand the start time '{params['start']}', sir."
                body["start"] = {"dateTime": start.isoformat()}
                if params.get("end"):
                    end = _parse_datetime(params.get("end"), start + timedelta(hours=1))
                else:
                    end = start + timedelta(minutes=_clamp(params.get("duration_minutes", 60), 5, 1440, 60))
                body["end"] = {"dateTime": (end or start + timedelta(hours=1)).isoformat()}
            elif params.get("end"):
                end = _parse_datetime(params.get("end"), now + timedelta(hours=1))
                if end:
                    body["end"] = {"dateTime": end.isoformat()}
            attendees = _attendees(params.get("attendees"))
            if attendees:
                body["attendees"] = attendees
            if not body:
                return "Nothing to update — provide title, start, end, location or attendees."
            svc.events().patch(calendarId=calendar_id, eventId=event_id, body=body).execute()
            return f"Event {event_id} updated, sir."

        if action in ("delete", "cancel", "remove"):
            event_id = str(params.get("event_id", "") or "").strip()
            if not event_id:
                event, matches = _find_by_summary(svc, calendar_id,
                                                  params.get("summary", params.get("title", "")))
                if not event:
                    if len(matches) > 1:
                        return (f"{len(matches)} events match that title — refusing to delete "
                                "the wrong one. Pass event_id, sir.")
                    return "No matching event found, sir."
                event_id = event.get("id", "")
            svc.events().delete(calendarId=calendar_id, eventId=event_id).execute()
            return "Event deleted, sir."

        if action in ("free_busy", "find_slot", "free_slots"):
            now = _local_now()
            start = _parse_datetime(params.get("start"), now) or now
            end = _parse_datetime(params.get("end"), start + timedelta(days=1)) or (start + timedelta(days=1))
            try:
                response_body = svc.freebusy().query(body={
                    "timeMin": start.isoformat(), "timeMax": end.isoformat(),
                    "items": [{"id": calendar_id}],
                }).execute()
                raw_busy = response_body.get("calendars", {}).get(calendar_id, {}).get("busy", [])
                busy = [(parse_b[0], parse_b[1]) for parse_b in (
                    (_parse_event_dt(item.get("start", "")), _parse_event_dt(item.get("end", "")))
                    for item in raw_busy) if parse_b[0] and parse_b[1]]
            except Exception:
                events = _list_events(svc, calendar_id, start.isoformat(), end.isoformat(), 50)
                busy = []
                for ev in events:
                    a, b, _ = _event_bounds(ev)
                    pair = (_parse_event_dt(a), _parse_event_dt(b))
                    if all(pair):
                        busy.append(pair)
            minutes = _clamp(params.get("duration_minutes", 60 if action == "find_slot" else 30), 5, 480,
                             60 if action == "find_slot" else 30)
            work_start = str(params.get("work_start", "09:00"))
            work_end = str(params.get("work_end", "19:00"))
            try:
                sh, sm = [int(x) for x in work_start.split(":")]
                eh, em = [int(x) for x in work_end.split(":")]
                window_start = start.replace(hour=sh, minute=sm, second=0, microsecond=0)
                window_end = start.replace(hour=eh, minute=em, second=0, microsecond=0)
            except Exception:
                window_start = start.replace(hour=9, minute=0, second=0, microsecond=0)
                window_end = start.replace(hour=19, minute=0, second=0, microsecond=0)
            slots = _free_slots(busy, window_start, window_end, minutes)
            if not slots:
                return "No free slots in that window, sir."
            return "Free slots:\n" + "\n".join(
                f"• {a.strftime('%H:%M')} – {b.strftime('%H:%M')}" for a, b in slots)

        return (f"Unknown calendar action: {action}. Use list | today | tomorrow | week | "
                "search | add | update | delete | free_busy | find_slot | calendars.")
    except Exception as exc:
        return f"Calendar error: {exc}"
