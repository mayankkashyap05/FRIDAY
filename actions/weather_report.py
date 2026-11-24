# actions/weather_report.py
# Friday — real-time weather intelligence via Open-Meteo (no API key needed).
#
# City resolution -> geocoding -> current conditions / 7-day forecast ->
# optional air quality. Browser opening is opt-in (open_browser=True).

from __future__ import annotations

import threading
import time
import webbrowser
from collections import OrderedDict
from datetime import datetime
from urllib.parse import quote_plus

_FORECAST_TTL_S = 600.0
_GEO_TTL_S = 86400.0
_CACHE_LOCK = threading.RLock()
_GEO_CACHE: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_FORECAST_CACHE: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()

_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

_CURRENT_FIELDS = [
    "temperature_2m", "apparent_temperature", "relative_humidity_2m", "is_day",
    "precipitation", "weather_code", "wind_speed_10m", "wind_direction_10m",
]
_HOURLY_FIELDS = ["temperature_2m", "precipitation_probability", "weather_code"]
_DAILY_FIELDS = [
    "weather_code", "temperature_2m_max", "temperature_2m_min", "sunrise",
    "sunset", "precipitation_probability_max", "uv_index_max",
]

# Complete WMO weather interpretation code mapping.
WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snowfall", 73: "Moderate snowfall", 75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}

_WIND_DIRECTIONS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]

_AQI_BANDS = [
    (50, "Good"),
    (100, "Moderate"),
    (150, "Unhealthy for Sensitive Groups"),
    (200, "Unhealthy"),
    (300, "Very Unhealthy"),
    (float("inf"), "Hazardous"),
]


def _speak_and_log(message: str, player=None):
    if player:
        try:
            player.write_log(f"FRIDAY: {message}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# HTTP + cache helpers
# ---------------------------------------------------------------------------

def _request_json(url: str, params: dict) -> dict:
    import requests

    response = requests.get(url, params=params, timeout=8)
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
    data = response.json() if hasattr(response, "json") else {}
    return data if isinstance(data, dict) else {}


def _cache_read(cache: OrderedDict, key, ttl: float):
    with _CACHE_LOCK:
        entry = cache.get(key)
        if not entry:
            return None
        created, value = entry
        if time.monotonic() - created > ttl:
            cache.pop(key, None)
            return None
        cache.move_to_end(key)
        return value


def _cache_write(cache: OrderedDict, key, value, limit: int = 64) -> None:
    with _CACHE_LOCK:
        cache[key] = (time.monotonic(), value)
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)


def _saved_city() -> str:
    try:
        from memory.memory_manager import load_memory
        identity = (load_memory() or {}).get("identity") or {}
        for key in ("city", "location"):
            entry = identity.get(key)
            value = entry.get("value") if isinstance(entry, dict) else entry
            if isinstance(value, str) and value.strip():
                return value.strip()
    except Exception as exc:
        print(f"[Weather] Saved city lookup failed: {exc}")
    return ""


def _normalize_units(value) -> str:
    unit = str(value or "metric").strip().lower()
    if unit in ("imperial", "fahrenheit", "f"):
        return "imperial"
    return "metric"


def _geocode(city: str) -> dict | None:
    city = str(city or "").strip()
    if not city:
        return None
    cached = _cache_read(_GEO_CACHE, city.lower(), _GEO_TTL_S)
    if cached:
        return cached

    parts = [part.strip() for part in city.split(",") if part.strip()]
    name = parts[0]
    qualifiers = [part.lower() for part in parts[1:]]
    data = _request_json(_GEO_URL, {
        "name": name, "count": 5, "language": "en", "format": "json",
    })
    results = data.get("results") or []
    if not isinstance(results, list) or not results:
        return None

    chosen = None
    if qualifiers:
        for candidate in results:
            fields = [str(candidate.get(key) or "").lower()
                      for key in ("admin1", "country", "country_code", "name")]
            if any(any(q in field for field in fields) for q in qualifiers):
                chosen = candidate
                break
    chosen = chosen or results[0]
    geo = {
        "latitude": chosen.get("latitude"),
        "longitude": chosen.get("longitude"),
        "name": str(chosen.get("name") or name),
        "admin1": str(chosen.get("admin1") or ""),
        "country": str(chosen.get("country") or ""),
        "timezone": str(chosen.get("timezone") or "auto"),
    }
    if geo["latitude"] is None or geo["longitude"] is None:
        return None
    _cache_write(_GEO_CACHE, city.lower(), geo, limit=128)
    return geo


def _forecast(lat, lon, units: str) -> dict | None:
    key = (round(float(lat), 4), round(float(lon), 4), units)
    cached = _cache_read(_FORECAST_CACHE, key, _FORECAST_TTL_S)
    if cached:
        return cached

    params = {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 7,
        "current": ",".join(_CURRENT_FIELDS),
        "hourly": ",".join(_HOURLY_FIELDS),
        "daily": ",".join(_DAILY_FIELDS),
        "temperature_unit": "fahrenheit" if units == "imperial" else "celsius",
        "wind_speed_unit": "mph" if units == "imperial" else "kmh",
        "precipitation_unit": "inch" if units == "imperial" else "mm",
    }
    data = _request_json(_FORECAST_URL, params)
    if not data.get("current"):
        return None
    _cache_write(_FORECAST_CACHE, key, data, limit=64)
    return data


def _aqi(lat, lon) -> dict | None:
    try:
        data = _request_json(_AIR_URL, {
            "latitude": lat, "longitude": lon,
            "current": "us_aqi,pm2_5,pm10", "timezone": "auto",
        })
        current = data.get("current")
        return current if isinstance(current, dict) else None
    except Exception as exc:
        print(f"[Weather] Air quality unavailable: {exc}")
        return None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _condition(code) -> str:
    try:
        return WMO_CODES.get(int(code), f"Unknown conditions (code {code})")
    except (TypeError, ValueError):
        return "Unknown conditions"


def _wind_direction(degrees) -> str:
    try:
        index = int((float(degrees) % 360) / 22.5 + 0.5) % 16
        return _WIND_DIRECTIONS[index]
    except (TypeError, ValueError):
        return ""


def _unit_labels(units: str) -> tuple[str, str]:
    return ("°F", "mph") if units == "imperial" else ("°C", "km/h")


def _aqi_band(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "Unknown"
    for ceiling, label in _AQI_BANDS:
        if number <= ceiling:
            return label
    return "Hazardous"


def _daily(data: dict) -> list[dict]:
    daily = data.get("daily") or {}
    times = daily.get("time") or []
    rows = []
    for index, stamp in enumerate(times):
        row = {"date": str(stamp)}
        for field in _DAILY_FIELDS:
            values = daily.get(field) or []
            row[field] = values[index] if index < len(values) else None
        rows.append(row)
    return rows


def _first_number(value, default=None):
    if isinstance(value, list) and value:
        value = value[0]
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_number(value, decimals=0) -> str:
    number = _first_number(value)
    if number is None:
        return "?"
    return f"{number:.{decimals}f}"


def _aqi_line(lat, lon, requested: bool) -> str:
    if not requested:
        return ""
    current = _aqi(lat, lon)
    if not current:
        return ""
    aqi = current.get("us_aqi")
    if aqi is None:
        return ""
    line = f"AQI {_format_number(aqi)} ({_aqi_band(aqi)})"
    pm25 = current.get("pm2_5")
    if pm25 is not None:
        line += f", PM2.5 {_format_number(pm25, 1)} µg/m³"
    return line


def _uv_note(uv) -> str:
    number = _first_number(uv)
    return "" if number is None or number < 6 else f" UV index is {number:.1f} — use sun protection."


def _hourly_window(data: dict, hours: set[int], *, next_day: bool = False) -> list[int]:
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    today = datetime.now().strftime("%Y-%m-%d")
    indices = []
    for index, stamp in enumerate(times):
        text = str(stamp)
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            continue
        day = moment.strftime("%Y-%m-%d")
        if (day != today) and not next_day:
            continue
        if moment.hour in hours:
            indices.append(index)
    return indices


def _window_summary(data: dict, indices: list[int], label: str, temp_unit: str) -> str:
    hourly = data.get("hourly") or {}
    temps = hourly.get("temperature_2m") or []
    rains = hourly.get("precipitation_probability") or []
    codes = hourly.get("weather_code") or []
    selected_temps = [temps[i] for i in indices if i < len(temps)]
    selected_rains = [rains[i] for i in indices if i < len(rains)]
    selected_codes = [codes[i] for i in indices if i < len(codes)]
    if not selected_temps:
        return f"No {label} forecast is available right now, sir."
    avg_temp = sum(float(t) for t in selected_temps) / len(selected_temps)
    rain = max((float(r) for r in selected_rains), default=0)
    code = max(set(selected_codes), key=selected_codes.count) if selected_codes else 0
    return (
        f"{label}: {avg_temp:.0f}{temp_unit}, {_condition(code).lower()}, "
        f"chance of rain {rain:.0f}%."
    )


def _tomorrow_line(days: list[dict], temp_unit: str) -> str:
    if len(days) < 2:
        return "Tomorrow's forecast isn't available right now, sir."
    day = days[1]
    high = _format_number(day.get("temperature_2m_max"))
    low = _format_number(day.get("temperature_2m_min"))
    rain = _format_number(day.get("precipitation_probability_max"))
    uv = _format_number(day.get("uv_index_max"), 1)
    return (
        f"Tomorrow: {_condition(day.get('weather_code'))}, high {high}{temp_unit}, "
        f"low {low}{temp_unit}, chance of rain {rain}%, UV index {uv}."
    )


def _multi_day_line(days: list[dict], temp_unit: str, limit: int) -> str:
    chosen = days[:limit]
    if not chosen:
        return "The forecast isn't available right now, sir."
    parts = []
    for day in chosen:
        try:
            label = datetime.fromisoformat(day["date"]).strftime("%a")
        except (ValueError, KeyError):
            label = str(day.get("date", "?"))
        parts.append(
            f"{label} {_condition(day.get('weather_code'))} "
            f"{_format_number(day.get('temperature_2m_max'))}/{_format_number(day.get('temperature_2m_min'))}{temp_unit}"
        )
    return "Forecast: " + "; ".join(parts) + "."


def _weekend_line(days: list[dict], temp_unit: str) -> str:
    weekend = []
    for day in days:
        try:
            if datetime.fromisoformat(day["date"]).weekday() in (5, 6):
                weekend.append(day)
        except (ValueError, KeyError):
            continue
    return _multi_day_line(weekend, temp_unit, 2) if weekend else \
        "No weekend forecast is available right now, sir."


def _weekday_line(days: list[dict], weekday: str, temp_unit: str) -> str:
    wanted = weekday.strip().lower()[:3]
    for index, day in enumerate(days):
        if index == 0:
            continue  # an upcoming weekday never means today
        try:
            label = datetime.fromisoformat(day["date"]).strftime("%A").lower()
        except (ValueError, KeyError):
            continue
        if label.startswith(wanted):
            return _tomorrow_line([days[0], day], temp_unit).replace(
                "Tomorrow:", f"{label.capitalize()}:"
            )
    return f"I don't have a {weekday} in the 7-day forecast, sir."


def _format_weather(geo: dict, data: dict, when: str, units: str,
                    include_aqi: bool) -> str:
    current = data.get("current") or {}
    days = _daily(data)
    temp_unit, wind_unit = _unit_labels(units)
    location = geo.get("name") or "your city"
    region = ", ".join(part for part in (geo.get("admin1"), geo.get("country")) if part)
    if region:
        location += f", {region}"
    when = (when or "now").strip().lower()

    if when in ("now", "today", "current"):
        today = days[0] if days else {}
        line = (
            f"Weather in {location}: {_format_number(current.get('temperature_2m'))}{temp_unit}, "
            f"{_condition(current.get('weather_code')).lower()}. "
            f"Feels like {_format_number(current.get('apparent_temperature'))}{temp_unit}. "
            f"High {_format_number(today.get('temperature_2m_max'))}{temp_unit}, "
            f"low {_format_number(today.get('temperature_2m_min'))}{temp_unit}. "
            f"Humidity {_format_number(current.get('relative_humidity_2m'))}%, "
            f"wind {_format_number(current.get('wind_speed_10m'))} {wind_unit} "
            f"{_wind_direction(current.get('wind_direction_10m'))}. "
            f"Chance of rain {_format_number(today.get('precipitation_probability_max'))}%."
        )
        line += _uv_note(today.get("uv_index_max"))
    elif when in ("tonight", "evening"):
        hours = set(range(18, 24)) | set(range(0, 5)) if when == "tonight" else set(range(18, 24))
        line = _window_summary(data, _hourly_window(data, hours),
                               "Tonight" if when == "tonight" else "This evening", temp_unit)
    elif when == "morning":
        line = _window_summary(data, _hourly_window(data, set(range(6, 12))),
                               "This morning", temp_unit)
    elif when == "afternoon":
        line = _window_summary(data, _hourly_window(data, set(range(12, 18))),
                               "This afternoon", temp_unit)
    elif when == "tomorrow":
        line = _tomorrow_line(days, temp_unit)
    elif when in ("week", "forecast", "7 days", "next 7 days"):
        line = _multi_day_line(days, temp_unit, 7)
    elif when == "next 5 days":
        line = _multi_day_line(days, temp_unit, 5)
    elif when == "weekend":
        line = _weekend_line(days, temp_unit)
    elif _is_weekday(when):
        line = _weekday_line(days, when, temp_unit)
    else:
        line = (
            f"Weather in {location}: {_format_number(current.get('temperature_2m'))}{temp_unit}, "
            f"{_condition(current.get('weather_code')).lower()}."
        )

    aqi_line = _aqi_line(geo.get("latitude"), geo.get("longitude"), include_aqi)
    if aqi_line:
        line += " " + aqi_line
    return line


def _is_weekday(value: str) -> bool:
    """True for a specific upcoming weekday request."""
    names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    return any(str(value).strip().lower().startswith(name) for name in names)


# ---------------------------------------------------------------------------
# Public action
# ---------------------------------------------------------------------------

def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    """Live Open-Meteo weather report, spoken/logged and optionally browser-opened."""
    params = parameters if isinstance(parameters, dict) else {}
    when = params.get("time")
    when = "now" if not isinstance(when, str) or not when.strip() else when.strip().lower()

    city = params.get("city")
    if (not isinstance(city, str) or not city.strip()
            or city.strip().lower() in ("here", "my city", "current")):
        city = _saved_city()
    city = str(city or "").strip()
    if not city:
        msg = "Sir, which city should I check the weather for?"
        _speak_and_log(msg, player)
        return msg

    units = _normalize_units(params.get("units"))
    focus = str(params.get("focus") or "").strip().lower()
    include_aqi = bool(
        params.get("include_aqi", False) or focus in ("aqi", "air quality")
        or "aqi" in when or "air" in when
    )
    open_browser = bool(params.get("open_browser", False))

    try:
        geo = _geocode(city)
        if not geo:
            msg = f"Sir, I couldn't find '{city}'. Try adding the country, for example '{city}, India'."
            _speak_and_log(msg, player)
            return msg
        data = _forecast(geo["latitude"], geo["longitude"], units)
        if not data:
            raise RuntimeError("forecast unavailable")
        msg = _format_weather(geo, data, when, units, include_aqi)
    except Exception as exc:
        print(f"[Weather] Live weather unavailable: {exc}")
        msg = "Weather service is unavailable right now, sir. Please try again shortly."
        if open_browser:
            try:
                webbrowser.open(
                    "https://www.google.com/search?q=" +
                    quote_plus(f"weather in {city} {when}")
                )
            except Exception:
                pass
        _speak_and_log(msg, player)
        return msg

    if open_browser:
        try:
            webbrowser.open(
                "https://www.google.com/search?q=" +
                quote_plus(f"weather in {city} {when}")
            )
        except Exception:
            pass

    _speak_and_log(msg, player)
    if session_memory:
        try:
            session_memory.set_last_search(query=f"weather in {city} {when}", response=msg)
        except Exception:
            pass
    return msg
