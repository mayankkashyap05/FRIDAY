"""100%-offline deep tests for Google Calendar v3 action layer."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from actions import calendar as cal


class FakeEvents:
    def __init__(self, store):
        self.store = store

    def list(self, **kwargs):
        self.store["list"] = kwargs
        items = self.store.get("items", [])
        query = kwargs.get("q", "").lower()
        if query:
            items = [item for item in items if query in item.get("summary", "").lower()]
        result = MagicMock()
        result.execute.return_value = {"items": items}
        return result

    def insert(self, **kwargs):
        self.store["insert"] = kwargs
        result = MagicMock()
        result.execute.return_value = {"id": "evt-new", "htmlLink": "https://cal/event",
                                       "summary": kwargs["body"]["summary"]}
        return result

    def patch(self, **kwargs):
        self.store["patch"] = kwargs
        result = MagicMock()
        result.execute.return_value = {"id": kwargs["eventId"]}
        return result

    def delete(self, **kwargs):
        self.store["delete"] = kwargs
        result = MagicMock()
        result.execute.return_value = {}
        return result


class FakeFreeBusy:
    def __init__(self, store):
        self.store = store

    def query(self, **kwargs):
        self.store["freebusy"] = kwargs
        result = MagicMock()
        result.execute.return_value = self.store.get("busy_body", {"calendars": {"primary": {"busy": []}}})
        return result


class FakeService:
    def __init__(self, store):
        self.store = store
        self.events = lambda: FakeEvents(store)
        self.freebusy = lambda: FakeFreeBusy(store)
        self.calendarList = lambda: MagicMock(list=MagicMock(return_value=MagicMock(execute=MagicMock(
            return_value={"items": [{"id": "primary", "summary": "Main", "primary": True},
                                     {"id": "work", "summary": "Work"}]}))))


@pytest.fixture
def env(monkeypatch):
    store = {"items": []}
    monkeypatch.setattr(cal, "_service", lambda: FakeService(store))
    return store


def test_natural_dates_and_relative_times(monkeypatch):
    now = datetime(2026, 11, 24, 10, 0, tzinfo=cal._local_now().tzinfo)
    monkeypatch.setattr(cal, "_local_now", lambda: now)
    assert cal._parse_datetime("today 14:30").hour == 14
    assert cal._parse_datetime("tomorrow 9am").day == 25
    assert cal._parse_datetime("day after tomorrow").day == 26
    assert cal._parse_datetime("in 30 minutes").minute >= 30
    assert cal._parse_datetime("2026-12-01T09:15:00+05:30").isoformat().endswith("+05:30")
    assert cal._parse_datetime("nonsense-qq") is None
    assert cal._parse_time("2026-11-25") .startswith("2026-11-25")


def test_list_today_tomorrow_week_and_search(env):
    env["items"] = [{"id": "1", "summary": "Standup", "start": {"dateTime": "2026-11-25T09:00:00+05:30"},
                     "end": {"dateTime": "2026-11-25T09:30:00+05:30"}}]
    assert "Standup" in cal.calendar_action({"action": "list"})
    assert env["list"]["singleEvents"] is True
    assert "Standup" in cal.calendar_action({"action": "today"})
    assert "Standup" in cal.calendar_action({"action": "tomorrow"})
    assert "Standup" in cal.calendar_action({"action": "week"})
    assert "Standup" in cal.calendar_action({"action": "search", "query": "stand"})
    assert env["list"]["q"] == "stand"
    assert "No matching" in cal.calendar_action({"action": "search", "query": "xyz-no-match"})


def test_create_event_conference_recurrence_reminder_attendees(env):
    result = cal.calendar_action({
        "action": "add", "title": "Design review", "start": "2026-12-01 10:00",
        "duration_minutes": 45, "location": "Room 1",
        "attendees": "a@example.com, bad-address, b@example.com",
        "recurrence": "weekly", "reminder_minutes": "15", "conference": "true",
    })
    assert "Added" in result
    body = env["insert"]["body"]
    assert env["insert"]["conferenceDataVersion"] == 1
    assert body["conferenceData"]["createRequest"]["requestId"].startswith("friday-")
    assert [a["email"] for a in body["attendees"]] == ["a@example.com", "b@example.com"]
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=12"]
    assert body["reminders"]["overrides"][0]["minutes"] == 15
    assert body["location"] == "Room 1"


def test_all_day_event_and_missing_title(env):
    assert "title" in cal.calendar_action({"action": "add"}).lower()
    assert "Added" in cal.calendar_action({"action": "add", "title": "Holiday", "start": "2026-12-25"})
    assert "date" in env["insert"]["body"]["start"]


def test_conflict_guard_and_override(env):
    env["items"] = [{"id": "busy", "summary": "Existing meeting",
                     "start": {"dateTime": "2026-12-01T10:30:00+05:30"},
                     "end": {"dateTime": "2026-12-01T11:30:00+05:30"}}]
    base = {"action": "add", "title": "New meeting", "start": "2026-12-01 10:00+05:30",
            "duration_minutes": 60}
    result = cal.calendar_action(base)
    assert "Conflict" in result and "insert" not in env
    assert "Added" in cal.calendar_action({**base, "allow_overlap": "true"})


def test_update_and_delete_by_id_or_unique_summary(env):
    env["items"] = [{"id": "evt-1", "summary": "1:1", "start": {"dateTime": "2026-12-01T10:00:00+05:30"},
                     "end": {"dateTime": "2026-12-01T11:00:00+05:30"}}]
    assert "updated" in cal.calendar_action({"action": "reschedule", "event_id": "evt-1",
                                             "start": "2026-12-02 12:00"})
    assert env["patch"]["eventId"] == "evt-1"
    assert env["patch"]["body"]["start"]["dateTime"].startswith("2026-12-02T12:00")
    assert "updated" in cal.calendar_action({"action": "update", "title": "1:1", "location": "Cafe"})
    assert env["patch"]["body"]["location"] == "Cafe"
    assert "deleted" in cal.calendar_action({"action": "delete", "event_id": "evt-1"})
    assert env["delete"]["eventId"] == "evt-1"
    assert "deleted" in cal.calendar_action({"action": "cancel", "summary": "1:1"})


def test_ambiguous_delete_refuses(env):
    env["items"] = [{"id": "1", "summary": "Sync"}, {"id": "2", "summary": "Sync"}]
    result = cal.calendar_action({"action": "delete", "title": "Sync"})
    assert "refusing" in result.lower() and "delete" not in env


def test_free_busy_and_find_slot(env):
    env["busy_body"] = {"calendars": {"primary": {"busy": [
        {"start": "2026-12-01T10:00:00+05:30", "end": "2026-12-01T11:00:00+05:30"}]}}}
    result = cal.calendar_action({"action": "find_slot", "start": "2026-12-01 09:00+05:30",
                                  "end": "2026-12-01 14:00+05:30", "duration_minutes": 30})
    assert "Free slots" in result and "11:00" in result
    assert env["freebusy"]["body"]["items"] == [{"id": "primary"}]
    env["busy_body"] = {"calendars": {"primary": {"busy": []}}}
    assert "10:00" in cal.calendar_action({"action": "free_busy", "start": "2026-12-01 09:00"})


def test_free_busy_falls_back_to_events_when_api_rejects(monkeypatch, env):
    class Broken(FakeService):
        def __init__(self, store):
            super().__init__(store)
            self.freebusy = MagicMock()
            self.freebusy.return_value.query.side_effect = RuntimeError(" denied ")
    store = env
    monkeypatch.setattr(cal, "_service", lambda: Broken(store))
    store["items"] = [{"id": "e", "summary": "Busy", "start": {"dateTime": "2026-12-01T11:00:00+05:30"},
                       "end": {"dateTime": "2026-12-01T12:00:00+05:30"}}]
    assert "Free slots" in cal.calendar_action({"action": "find_slot",
                                                "start": "2026-12-01 09:00"})


def test_calendars_and_unknown_action(env):
    assert "primary" in cal.calendar_action({"action": "calendars"})
    assert "Unknown calendar action" in cal.calendar_action({"action": "teleport"})


def test_error_paths(monkeypatch):
    monkeypatch.setattr(cal, "_service", lambda: (_ for _ in ()).throw(RuntimeError("not authorized")))
    assert "Calendar error" in cal.calendar_action(None)
    monkeypatch.setattr(cal, "_service", lambda: FakeService({"items": []}))
    assert "start time" in cal.calendar_action({"action": "add", "title": "X", "start": "??"})


def test_timezone_and_bounds_contracts():
    start, end = cal._day_bounds(datetime(2026, 11, 24, 15, tzinfo=cal._local_now().tzinfo))
    assert "T00:00:00" in start and "T00:00:00" in end
    assert cal._clamp(9999, 1, 50, 10) == 50


def test_calendar_authorization_error_is_english(monkeypatch):
    monkeypatch.setattr(cal.google_oauth, "get_credentials", lambda: None)
    with pytest.raises(RuntimeError) as caught:
        cal._service()
    message = str(caught.value)
    assert "Google Calendar is not authorized yet" in message
    assert "googleni" not in message
