# actions/flight_finder.py
# Friday — Flight Finder: Google Flights search + multimodal-Gemini extraction.
#
# Dates are parsed deterministically first; only ambiguous phrases are sent to
# the cheap model. Result extraction uses the full flash model through the
# supported api_keys.generate_with_retry path (no deprecated SDK).

import json
import platform
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

from api_keys import generate_with_retry
from config.models import MODEL_FLASH, MODEL_FLASH_LITE

# Common city → IATA aliases, so "Bengaluru" and "BLR" behave identically.
_IATA = {
    "delhi": "DEL", "new delhi": "DEL", "mumbai": "BOM", "bombay": "BOM",
    "bangalore": "BLR", "bengaluru": "BLR", "bangaluru": "BLR",
    "hyderabad": "HYD", "chennai": "MAA", "madras": "MAA", "kolkata": "CCU",
    "calcutta": "CCU", "ahmedabad": "AMD", "pune": "PNQ", "goa": "GOI",
    "guwahati": "GAU", "kochi": "COK", "cochin": "COK", "jaipur": "JAI",
    "lucknow": "LKO", "chandigarh": "IXC", "srinagar": "SXR",
    "new york": "NYC", "london": "LON", "dubai": "DXB", "singapore": "SIN",
    "paris": "PAR", "tokyo": "TYO", "bangkok": "BKK", "istanbul": "IST",
    "los angeles": "LAX", "san francisco": "SFO", "chicago": "ORD",
    "sydney": "SYD", "toronto": "YYZ", "doha": "DOH", "colombo": "CMB",
    "kathmandu": "KTM", "dhaka": "DAC", "male": "MLE", "bali": "DPS",
}

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4,
    "mayıs": 5, "mayis": 5, "haziran": 6, "temmuz": 7, "ağustos": 8,
    "agustos": 8, "eylül": 9, "eylul": 9, "ekim": 10, "kasım": 11,
    "kasim": 11, "aralık": 12, "aralik": 12,
}
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()


def _alias(place: str) -> str:
    """Resolve city aliases to IATA codes when known (otherwise keep input)."""
    clean = str(place or "").strip()
    lower = clean.lower().rstrip(".,")
    if re.fullmatch(r"[A-Za-z]{3}", lower):
        return lower.upper()
    return _IATA.get(lower, clean)


def _parse_date(raw: str) -> str:
    """Convert a natural-language date to YYYY-MM-DD, deterministically when
    possible; ambiguous phrases fall back to MODEL_FLASH_LITE."""
    text = str(raw or "").strip()
    if not text:
        return datetime.now().strftime("%Y-%m-%d")
    today = datetime.now()

    # ISO / numeric formats first (day-first is the locale used by Friday).
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%d-%m-%Y",
                "%d %B %Y", "%B %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    lower = text.lower()
    if any(word in lower for word in ("today", "bugün", "bugun")):
        return today.strftime("%Y-%m-%d")
    if any(word in lower for word in ("tomorrow", "yarın", "yarin")):
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    # "next friday" / "friday" / "cumartesi" style.
    weekdays = dict(_WEEKDAYS, **{
        "pazartesi": 0, "salı": 1, "carsamba": 2, "çarşamba": 2,
        "perşembe": 3, "persembe": 3, "cum": 4, "cuma": 4,
        "cumartesi": 5, "pazar": 6,
    })
    for word, weekday in weekdays.items():
        if re.search(rf"\b{re.escape(word)}\b", lower):
            days = (weekday - today.weekday()) % 7
            if days == 0 or "next" in lower or "gelecek" in lower:
                days += 7
            return (today + timedelta(days=days)).strftime("%Y-%m-%d")

    # "15 March", "March 15", "15 Mart".
    for word, month in sorted(_MONTHS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(word)}\b", lower):
            numbers = re.findall(r"\d{1,2}", text)
            if numbers:
                day = int(numbers[0])
                year = today.year if month >= today.month else today.year + 1
                try:
                    return datetime(year, month, day).strftime("%Y-%m-%d")
                except ValueError:
                    pass

    # Ambiguous: ask the cheap model, but stay deterministic if it is absent.
    try:
        response = generate_with_retry(
            MODEL_FLASH_LITE,
            f"Today is {today:%Y-%m-%d}. Convert this date to YYYY-MM-DD: '{text}'. "
            "Reply with only the date string.",
        )
        result = str(getattr(response, "text", "") or "").strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}", result):
            return result[:10]
    except Exception:
        pass
    return today.strftime("%Y-%m-%d")


def _build_google_flights_url(
    origin: str,
    destination: str,
    date: str,
    return_date: str | None = None,
    passengers: int = 1,
    cabin: str = "economy",
    currency: str = "INR",
    non_stop: bool = False,
    max_stops: int | None = None,
    sort_by: str = "",
) -> str:
    """Build a pre-filled Google Flights URL with encoded parameters."""
    codes = {"economy": "1", "premium": "2", "business": "3", "first": "4"}
    cabin_code = codes.get(str(cabin or "").lower(), "1")
    q_origin = quote_plus(str(origin))
    q_dest = quote_plus(str(destination))
    query = (f"Flights from {q_origin} to {q_dest} on {date}"
             + (f" returning {return_date}" if return_date else ""))
    if non_stop:
        query += " nonstop"
    elif max_stops is not None:
        query += f" up to {max_stops} stops"
    if sort_by:
        query += f" sort by {sort_by}"
    if passengers and int(passengers) > 1:
        query += f" for {int(passengers)} passengers"
    url = ("https://www.google.com/travel/flights?q=" + query
           + "&curr=" + quote_plus(str(currency or "INR"))
           + "&cabin=" + cabin_code)
    return url


def _search_flights_browser(
    origin: str,
    destination: str,
    date: str,
    return_date: str | None,
    passengers: int,
    cabin: str,
    **kwargs,
) -> tuple[str, str]:
    """Scrape Google Flights; if that fails, use grounded web_search."""
    from actions.browser_control import browser_control
    import time

    url = _build_google_flights_url(origin, destination, date, return_date,
                                    passengers, cabin, **kwargs)
    browser_control({"action": "go_to", "url": url})
    time.sleep(4)
    text = browser_control({"action": "get_text"}) or ""
    if text:
        return str(text), url

    # Grounded-search fallback — never invent flight prices.
    try:
        from actions.web_search import web_search
        query = (f"Google Flights {origin} to {destination} {date} "
                 f"{'return ' + return_date if return_date else ''}prices")
        result = web_search({"query": query, "max_results": 5})
        return str(result or ""), url
    except Exception:
        return "", url


def _price_number(flight: dict) -> float:
    text = str(flight.get("price", ""))
    match = re.search(r"[\d,]+(?:\.\d+)?", text.replace(" ", ""))
    if not match:
        return float("inf")
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return float("inf")


def _parse_flights_with_gemini(raw_text: str, origin: str, destination: str,
                               date: str, **filters) -> list[dict]:
    """Extract structured flights from page text using MODEL_FLASH."""
    prompt = (
        "You are a flight data extraction expert. Extract only flights that are "
        f"actually shown for {origin} to {destination} on {date}. "
        "Return ONLY a valid JSON array, no markdown. Up to 8 entries. "
        "Each entry: {\"airline\": str, \"departure\": \"HH:MM\", "
        "\"arrival\": \"HH:MM\", \"duration\": \"Xh Ym\", \"stops\": int, "
        "\"price\": \"number\", \"currency\": \"INR\"}. "
        "If none are shown, return [].\n\nPage text:\n" + raw_text[:14000]
    )
    try:
        response = generate_with_retry(MODEL_FLASH, prompt)
        text = str(getattr(response, "text", "") or "").strip()
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        data = json.loads(text)
        if not isinstance(data, list):
            return []
        flights = [item for item in data if isinstance(item, dict)]
    except Exception as exc:
        print(f"[FlightFinder] Parse failed: {exc}")
        return []

    non_stop = bool(filters.get("non_stop", False))
    max_stops = filters.get("max_stops")
    if non_stop:
        flights = [f for f in flights if int(f.get("stops", 0) or 0) == 0]
    elif max_stops is not None:
        try:
            limit = int(max_stops)
            flights = [f for f in flights if int(f.get("stops", 0) or 0) <= limit]
        except (TypeError, ValueError):
            pass
    if str(filters.get("currency", "INR")).upper() == "INR":
        for flight in flights:
            if str(flight.get("currency", "")).upper() in ("TRY", "$", "USD"):
                flight["currency"] = "INR"
    sort_by = str(filters.get("sort_by", "price")).lower()
    if "duration" in sort_by:
        flights.sort(key=lambda f: str(f.get("duration", "999h")))
    else:
        flights.sort(key=_price_number)
    return flights[:5]


def _format_spoken(
    flights: list[dict],
    origin: str,
    destination: str,
    date: str,
) -> str:
    """Format flights for natural spoken output."""
    if not flights:
        return (f"I couldn't find any flights from {origin} to {destination} "
                f"on {date}, sir. The page may not have loaded correctly.")

    lines = [f"Here are the flights from {origin} to {destination} on {date}, sir."]
    for i, flight in enumerate(flights[:5], 1):
        stops = int(flight.get("stops", 0) or 0)
        stop_text = "non-stop" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}"
        price = str(flight.get("price", "") or "").strip()
        currency = str(flight.get("currency", "INR") or "INR").strip()
        price_text = f"{price} {currency}".strip() if price else "price unavailable"
        lines.append(
            f"Option {i}: {flight.get('airline', 'Unknown airline')}, departing "
            f"{flight.get('departure', '--:--')}, arriving {flight.get('arrival', '--:--')}"
            f"{', ' + str(flight['duration']) if flight.get('duration') else ''}, "
            f"{stop_text}, {price_text}.")
    priced = [f for f in flights if f.get("price")]
    if priced:
        cheapest = min(priced, key=_price_number)
        lines.append(f"The cheapest option is {cheapest.get('airline')} at "
                     f"{cheapest.get('price')} {cheapest.get('currency', 'INR')}.")
    return " ".join(lines)


def _format_notepad(
    flights: list[dict],
    origin: str,
    destination: str,
    date: str,
    return_date: str | None,
    page_url: str,
) -> str:
    lines = ["Friday — Flight Search Results", "─" * 50,
             f"Route     : {origin} → {destination}", f"Date      : {date}"]
    if return_date:
        lines.append(f"Return    : {return_date}")
    lines += [f"Searched  : {datetime.now():%Y-%m-%d %H:%M}",
              f"Source    : {page_url}", "─" * 50, ""]
    if not flights:
        lines.append("No flights found.")
    for i, flight in enumerate(flights, 1):
        lines += [f"Flight {i}:",
                  f"  Airline   : {flight.get('airline', 'N/A')}",
                  f"  Departure : {flight.get('departure', 'N/A')}",
                  f"  Arrival   : {flight.get('arrival', 'N/A')}",
                  f"  Duration  : {flight.get('duration', 'N/A')}",
                  f"  Stops     : {flight.get('stops', 0)}",
                  f"  Price     : {flight.get('price', 'N/A')} "
                  f"{flight.get('currency', 'INR')}", ""]
    return "\n".join(lines)


def _save_to_notepad(content: str, origin: str, destination: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"flights_{origin}_{destination}_{timestamp}.txt".replace(" ", "_")
    desktop = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    path = desktop / filename
    path.write_text(content, encoding="utf-8")
    opener = {"Windows": "notepad.exe", "Darwin": "open", "Linux": "xdg-open"}.get(
        platform.system())
    if opener:
        try:
            args = [opener, str(path)] if opener != "open" else ["open", "-t", str(path)]
            subprocess.Popen(args)
        except Exception:
            pass
    return str(path)


def flight_finder(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    """Search flights and speak the best options grounded in real page data."""
    params = parameters or {}
    origin = str(params.get("origin", "")).strip()
    destination = str(params.get("destination", "")).strip()
    date_raw = str(params.get("date", "")).strip()
    return_raw = str(params.get("return_date", "")).strip()
    if not origin or not destination or not date_raw:
        return "Please provide origin, destination and departure date, sir."

    try:
        passengers = max(1, min(9, int(params.get("passengers", 1) or 1)))
    except (TypeError, ValueError):
        passengers = 1
    cabin = str(params.get("cabin", "economy") or "economy").lower().strip()
    currency = str(params.get("currency", "INR") or "INR").upper()
    non_stop = str(params.get("non_stop", False)).lower() in ("true", "1", "yes")
    try:
        max_stops = params.get("max_stops")
        max_stops = int(max_stops) if max_stops not in (None, "") else None
    except (TypeError, ValueError):
        max_stops = None
    sort_by = str(params.get("sort_by", "price") or "price").lower()
    save = str(params.get("save", False)).lower() in ("true", "1", "yes")

    origin_code, destination_code = _alias(origin), _alias(destination)
    date = _parse_date(date_raw)
    return_date = _parse_date(return_raw) if return_raw else None
    if player:
        player.write_log(f"[FlightFinder] {origin_code} → {destination_code} on {date}")
    if speak:
        speak(f"Searching flights from {origin} to {destination} on {date}, sir.")

    try:
        raw_text, page_url = _search_flights_browser(
            origin_code, destination_code, date, return_date, passengers, cabin,
            currency=currency, non_stop=non_stop, max_stops=max_stops, sort_by=sort_by,
        )
        if not raw_text:
            return "Could not retrieve flight data, sir. The page may not have loaded."
        flights = _parse_flights_with_gemini(
            raw_text, origin_code, destination_code, date,
            non_stop=non_stop, max_stops=max_stops, sort_by=sort_by, currency=currency,
        )
        spoken = _format_spoken(flights, origin, destination, date)
        if speak:
            speak(spoken)
        if save and flights:
            path = _save_to_notepad(
                _format_notepad(flights, origin, destination, date, return_date, page_url),
                origin, destination)
            spoken += f" Results saved to Desktop: {path}"
        return spoken
    except Exception as exc:
        return f"Flight search failed, sir: {exc}"
