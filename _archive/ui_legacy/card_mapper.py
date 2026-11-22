"""FRIDAY UI — map structured envelopes & legacy strings to cards.

The backend (``main.py`` + ``actions/``) speaks two dialects:

1. **Legacy strings** — ``player.write_log("[YouTube] Searching: lofi")``.
   Thirty call sites, no structure. We must never break them.
2. **Structured envelopes** — dicts the tool layer can optionally emit, e.g.
   ``{"type": "weather", "city": "Dibrugarh", "temp_c": 29, ...}``.

This module normalises both into :class:`~ui.event_bus.Card` values plus
zero-or-more :class:`~ui.event_bus.Activity` values, with no I/O.

The heuristics are intentionally conservative: a card is only emitted when the
text clearly carries data a card renders better than a line of log. Everything
else stays a log line, which is why the snippet stack never becomes noise.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Iterable, Sequence

from .event_bus import (
    Activity,
    ActivityKind,
    Card,
    CardKind,
    LogEvent,
    Severity,
    classify_legacy,
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _uid(*parts: Any) -> str:
    seed = "|".join(str(p) for p in parts) + f"|{time.time():.6f}"
    return hashlib.blake2s(seed.encode("utf-8", "replace"), digest_size=8).hexdigest()


def _find_number(text: str, patterns: Sequence[str]) -> float | None:
    for pat in patterns:
        match = re.search(pat, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except (TypeError, ValueError, IndexError):
                continue
    return None


_TEMP_RE = (r"(-?\d+(?:\.\d+)?)\s*°?\s*C\b", r"(-?\d+(?:\.\d+)?)\s*°\s*[CF]?",
            r"temp\w*\D{0,12}(-?\d+(?:\.\d+)?)")
_PERCENT_RE = (r"(\d+(?:\.\d+)?)\s*%", r"(\d+(?:\.\d+)?)\s*percent")
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b")
_FLIGHT_RE = re.compile(r"\b([A-Z]{2,3}\d{2,4})\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_MONEY_RE = re.compile(r"(?:[$€£₹₽₸]|\bUSD\b|\bEUR\b)\s?(\d[\d,]*(?:\.\d+)?)")

_WEATHER_KEYS = ("weather", "temperature", "temp_c", "forecast", "humidity", "wind")
_CALENDAR_KEYS = ("events", "calendar", "meeting", "schedule", "appointment")
_EMAIL_KEYS = ("emails", "inbox", "unread", "mail")
_FLIGHT_KEYS = ("flight", "airline", "departure", "arrival", "gate", "boarding")
_TELEMETRY_KEYS = ("cpu", "ram", "memory", "disk", "battery", "gpu", "temperature_c")
_DOCUMENT_KEYS = ("document", "pdf", "page", "summary", "excerpt")


# ─────────────────────────────────────────────────────────────────────────────
# Structured envelopes
# ─────────────────────────────────────────────────────────────────────────────

def from_envelope(envelope: dict, *, ttl: float = 0.0) -> tuple[Card | None, list[Activity]]:
    """Map a structured dict emitted by a tool into card(s).

    Recognised shapes::

        {"type": "weather", "city": ..., "temp_c": ..., "condition": ...}
        {"type": "calendar", "events": [{"time": ..., "title": ...}]}
        {"type": "email", "emails": [{"from": ..., "subject": ...}]}
        {"type": "flight", "code": "AI101", "from": ..., "to": ..., "gate": ...}
        {"type": "telemetry", "cpu": 42, "ram": 71, ...}
        {"type": "control", "label": "Volume", "value": 60, "unit": "%"}
        {"type": "document", "title": ..., "pages": n, "excerpt": ...}
        {"type": "confirmation", "action": ..., "detail": ...}
        {"type": "text", "title": ..., "body": ...}

    Anything unrecognised degrades to a TEXT card so no information is lost.
    """
    if not isinstance(envelope, dict) or not envelope:
        return None, []

    kind_raw = str(envelope.get("type") or envelope.get("kind") or "text").lower()
    severity = Severity.coerce(envelope.get("severity", "info"))
    title = str(envelope.get("title") or "").strip()
    body = str(envelope.get("body") or envelope.get("text") or "").strip()
    footer = str(envelope.get("footer") or "").strip()
    payload = {k: v for k, v in envelope.items()
               if k not in ("type", "kind", "title", "body", "text", "footer", "severity")}

    kind_map = {
        "weather": CardKind.WEATHER, "forecast": CardKind.WEATHER,
        "calendar": CardKind.CALENDAR, "schedule": CardKind.CALENDAR,
        "email": CardKind.EMAIL, "inbox": CardKind.EMAIL, "mail": CardKind.EMAIL,
        "flight": CardKind.FLIGHT,
        "telemetry": CardKind.TELEMETRY, "system": CardKind.TELEMETRY,
        "control": CardKind.CONTROL,
        "document": CardKind.DOCUMENT, "pdf": CardKind.DOCUMENT,
        "confirmation": CardKind.CONFIRMATION, "confirm": CardKind.CONFIRMATION,
    }
    kind = kind_map.get(kind_raw, CardKind.TEXT)

    if kind is CardKind.WEATHER:
        title = title or f"Weather · {payload.get('city', 'Local')}"
        body = body or _weather_body(payload)
    elif kind is CardKind.CALENDAR:
        events = payload.get("events") or []
        title = title or "Calendar"
        body = body or _list_body(events, ("time", "start"), ("title", "summary", "name"))
        footer = footer or (f"{len(events)} event(s)" if events else "")
    elif kind is CardKind.EMAIL:
        emails = payload.get("emails") or []
        title = title or (f"Inbox · {payload.get('unread', len(emails))} unread"
                          if payload.get("unread") is not None else "Inbox")
        body = body or _list_body(emails, ("from", "sender"), ("subject", "title"))
    elif kind is CardKind.FLIGHT:
        code = payload.get("code") or payload.get("flight") or ""
        title = title or (f"Flight {code}" if code else "Flight")
        body = body or _flight_body(payload)
    elif kind is CardKind.TELEMETRY:
        title = title or "System telemetry"
        body = body or _telemetry_body(payload)
    elif kind is CardKind.CONTROL:
        label = payload.get("label") or title or "Control"
        value = payload.get("value")
        unit = payload.get("unit", "")
        title = title or str(label)
        body = body or (f"{value}{unit}" if value is not None else "")
    elif kind is CardKind.CONFIRMATION:
        title = title or "Confirmation required"
        body = body or str(payload.get("action") or payload.get("detail") or "")
        payload.setdefault("confirm_label", "Approve")
        payload.setdefault("deny_label", "Deny")

    if kind is CardKind.TEXT and not (title or body):
        # Unknown envelope shape with no renderable content: let the caller
        # fall back to the transcript instead of pushing an empty glass card.
        return None, []

    card = Card(
        kind=kind, title=title or "FRIDAY", body=body, footer=footer,
        payload=payload, severity=severity, uid=_uid("env", kind_raw, title, body),
        ttl=ttl or float(envelope.get("ttl", 0.0) or 0.0),
        dismissible=bool(envelope.get("dismissible", True)),
    )
    return card, []


def _weather_body(payload: dict) -> str:
    bits = []
    temp = payload.get("temp_c", payload.get("temperature"))
    if temp is not None:
        bits.append(f"{temp}°C")
    cond = payload.get("condition") or payload.get("summary")
    if cond:
        bits.append(str(cond))
    humid = payload.get("humidity")
    if humid is not None:
        bits.append(f"{humid}% humidity")
    wind = payload.get("wind")
    if wind is not None:
        bits.append(f"wind {wind}")
    return " · ".join(bits)


def _list_body(items: Iterable[Any], *field_pairs: tuple[str, ...]) -> str:
    lines = []
    for item in list(items)[:6]:
        if isinstance(item, dict):
            picked = []
            for pair in field_pairs:
                for key in pair:
                    if item.get(key):
                        picked.append(str(item[key]))
                        break
            lines.append(" · ".join(picked) if picked else str(item)[:80])
        else:
            lines.append(str(item)[:80])
    return "\n".join(lines)


def _flight_body(payload: dict) -> str:
    bits = []
    for key in ("from", "origin"):
        if payload.get(key):
            bits.append(f"from {payload[key]}")
            break
    for key in ("to", "destination"):
        if payload.get(key):
            bits.append(f"to {payload[key]}")
            break
    if payload.get("departure"):
        bits.append(f"departs {payload['departure']}")
    if payload.get("gate"):
        bits.append(f"gate {payload['gate']}")
    if payload.get("status"):
        bits.append(str(payload["status"]))
    return " · ".join(bits)


def _telemetry_body(payload: dict) -> str:
    order = ("cpu", "ram", "memory", "disk", "battery", "gpu")
    labels = {"cpu": "CPU", "ram": "RAM", "memory": "RAM", "disk": "Disk",
              "battery": "Battery", "gpu": "GPU"}
    bits = []
    for key in order:
        value = payload.get(key)
        if value is None:
            continue
        unit = "%" if key not in ("battery",) else "%"
        bits.append(f"{labels[key]} {value}{unit}")
    return " · ".join(bits)


# ─────────────────────────────────────────────────────────────────────────────
# Legacy log lines → optional card
# ─────────────────────────────────────────────────────────────────────────────

def from_log_event(event: LogEvent, *, ttl: float = 0.0) -> tuple[Card | None, list[Activity]]:
    """Promote a legacy log line to a card only when it carries real data."""
    text = event.text or ""
    low = text.lower()
    activities: list[Activity] = []

    # ── activities first: these also annotate the live capsule ───────────────
    act = _activity_from_text(text, low, event)
    if act is not None:
        activities.append(act)

    # ── weather ──────────────────────────────────────────────────────────────
    if any(k in low for k in ("°c", "°f", "weather", "forecast", "humidity")):
        temp = _find_number(text, _TEMP_RE)
        if temp is not None or "forecast" in low:
            payload = {}
            if temp is not None:
                payload["temp_c"] = temp
            city = _extract_city(text)
            if city:
                payload["city"] = city
            cond = _extract_condition(low)
            if cond:
                payload["condition"] = cond
            return Card(
                kind=CardKind.WEATHER,
                title=f"Weather · {city}" if city else "Weather",
                body=_weather_body(payload) or text[:120],
                payload=payload, severity=event.severity,
                uid=_uid("weather", text), ttl=ttl or 90.0,
            ), activities

    # ── telemetry ────────────────────────────────────────────────────────────
    if any(k in low for k in ("cpu", "ram", "memory usage", "disk", "battery")) and "%" in text:
        payload = {}
        for key in ("cpu", "ram", "disk", "battery"):
            if key in low:
                value = _find_number(text, _PERCENT_RE)
                if value is not None:
                    payload[key] = value
                    break
        if payload:
            return Card(
                kind=CardKind.TELEMETRY, title="System",
                body=_telemetry_body(payload), payload=payload,
                severity=event.severity, uid=_uid("telemetry", text), ttl=ttl or 20.0,
            ), activities

    # ── flight ───────────────────────────────────────────────────────────────
    flight = _FLIGHT_RE.search(text)
    if flight and any(k in low for k in ("flight", "boarding", "gate", "departure")):
        payload = {"code": flight.group(1)}
        gate = re.search(r"gate\s*([A-Z]?\d+)", text, re.IGNORECASE)
        if gate:
            payload["gate"] = gate.group(1)
        return Card(
            kind=CardKind.FLIGHT, title=f"Flight {flight.group(1)}",
            body=_flight_body(payload), payload=payload, severity=event.severity,
            uid=_uid("flight", text), ttl=ttl or 0.0,
        ), activities

    # ── calendar ─────────────────────────────────────────────────────────────
    if any(k in low for k in ("meeting", "calendar", "appointment", "scheduled")) \
            and _TIME_RE.search(text):
        match = _TIME_RE.search(text)
        payload = {"events": [{"time": match.group(0)}]}
        return Card(
            kind=CardKind.CALENDAR, title="Calendar", body=text[:200],
            payload=payload, severity=event.severity, uid=_uid("cal", text),
            ttl=ttl or 60.0,
        ), activities

    # ── email ────────────────────────────────────────────────────────────────
    if any(k in low for k in ("inbox", "unread", "new email", "email from")):
        if "unread" in low or "inbox" in low or _EMAIL_RE.search(text):
            payload = {"unread": _find_number(text, (r"(\d+)\s*(?:new\s*)?(?:unread|email)",))}
            return Card(
                kind=CardKind.EMAIL, title="Inbox", body=text[:200], payload=payload,
                severity=event.severity, uid=_uid("email", text), ttl=ttl or 60.0,
            ), activities

    # ── document Q&A ─────────────────────────────────────────────────────────
    if event.channel.value == "friday" and len(text) > 380:
        return Card(
            kind=CardKind.DOCUMENT, title="Long answer", body=text,
            payload={"excerpt": text[:400]}, severity=event.severity,
            uid=_uid("doc", text), ttl=ttl or 0.0,
        ), activities

    # ── security / confirmation ──────────────────────────────────────────────
    if event.channel.value == "security" or low.startswith("security:"):
        return Card(
            kind=CardKind.CONFIRMATION, title="Security",
            body=text.replace("SECURITY:", "").strip()[:240],
            payload={"action": text[:240], "confirm_label": "Approve",
                     "deny_label": "Deny"},
            severity=Severity.SECURITY, uid=_uid("sec", text), ttl=ttl or 0.0,
        ), activities

    return None, activities


def _extract_city(text: str) -> str:
    match = re.search(r"\b(?:in|at|for)\s+([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){0,2})", text)
    return match.group(1) if match else ""


def _extract_condition(low: str) -> str:
    for cond in ("clear", "sunny", "cloudy", "overcast", "rain", "rainy", "storm",
                 "thunder", "snow", "fog", "mist", "humid", "haze", "windy", "drizzle"):
        if cond in low:
            return cond
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Activity inference (Live Activity Bar)
# ─────────────────────────────────────────────────────────────────────────────

_ACTIVITY_PATTERNS: Sequence[tuple[tuple[str, ...], ActivityKind, str]] = (
    (("[code]", "build started", "compiling", "writing code", "fixing (attempt"), ActivityKind.CODE, "Code"),
    (("[browser]", "playwright", "navigating", "opening page"), ActivityKind.BROWSER, "Browser"),
    (("downloading", "download started", "saving media"), ActivityKind.DOWNLOAD, "Media"),
    (("[youtube]", "youtube summariz"), ActivityKind.BROWSER, "YouTube"),
    (("[gameupdater]", "steam", "updating game"), ActivityKind.DOWNLOAD, "Game updater"),
    (("recording",), ActivityKind.RECORDING, "Recording"),
    (("vision mode active", "start vision", "gesture control", "air mouse"), ActivityKind.VISION, "Vision"),
    (("[devagent]", "dev agent"), ActivityKind.CODE, "Dev agent"),
    (("task started", "agent task", "[task"), ActivityKind.TASK, "Agent task"),
    (("reminder", "⏰"), ActivityKind.REMINDER, "Reminder"),
    (("[flightfinder]", "searching flights"), ActivityKind.GENERIC, "Flight search"),
)

_ACTIVITY_END_MARKERS = (
    "done", "completed", "finished", "stopped", "cancelled", "failed",
    "vision mode active",  # start-only marker, handled above
)


def _activity_from_text(text: str, low: str, event: LogEvent) -> Activity | None:
    for needles, kind, label in _ACTIVITY_PATTERNS:
        if not any(n in low for n in needles):
            continue
        # Text that says "stopped/finished" terminates instead of starting.
        if kind is not ActivityKind.VISION and any(m in low for m in _ACTIVITY_END_MARKERS):
            if "start" not in low and "active" not in low:
                return Activity(
                    aid=_activity_id(kind), title=label, kind=kind,
                    detail=text[:160], active=False, progress=1.0,
                )
        progress = -1.0
        pct = _find_number(text, _PERCENT_RE)
        if pct is not None:
            progress = max(0.0, min(1.0, pct / 100.0))
        return Activity(
            aid=_activity_id(kind), title=label, kind=kind,
            detail=text[:160], active=True, progress=progress,
        )
    return None


def _activity_id(kind: ActivityKind) -> str:
    return f"act-{kind.value}"


# ─────────────────────────────────────────────────────────────────────────────
# Unified entry point used by the bridge
# ─────────────────────────────────────────────────────────────────────────────

def to_cards(payload: Any, *, ttl: float = 0.0) -> tuple[Card | None, list[Activity], LogEvent | None]:
    """Map *anything* the backend throws at us.

    Returns ``(card_or_None, activities, log_event_or_None)``. Callers use all
    three: the log event always exists for strings so the transcript keeps
    working even when a card is also produced.
    """
    if isinstance(payload, Card):
        return payload, [], None
    if isinstance(payload, dict):
        card, acts = from_envelope(payload, ttl=ttl)
        return card, acts, None
    event = payload if isinstance(payload, LogEvent) else classify_legacy(payload)
    card, acts = from_log_event(event, ttl=ttl)
    return card, acts, event


__all__ = ["from_envelope", "from_log_event", "to_cards"]
