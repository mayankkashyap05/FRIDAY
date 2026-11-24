"""Fully offline combinatorial tests for actions.weather_report."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from actions import weather_report as wr


@pytest.fixture(autouse=True)
def _clear_weather_caches():
    with wr._CACHE_LOCK:
        wr._GEO_CACHE.clear()
        wr._FORECAST_CACHE.clear()
    yield
    with wr._CACHE_LOCK:
        wr._GEO_CACHE.clear()
        wr._FORECAST_CACHE.clear()


def _dates(count=7):
    today = datetime.now().date()
    return [(today + timedelta(days=offset)).isoformat() for offset in range(count)]


def _geo_payload(name="Guwahati", admin1="Assam", country="India"):
    return {"results": [{
        "name": name, "latitude": 26.1445, "longitude": 91.7362,
        "admin1": admin1, "country": country, "country_code": "IN",
        "timezone": "Asia/Kolkata",
    }]}


def _forecast_payload(temp=30, code=0, wind=12, wind_dir=90):
    days = _dates()
    hourly_times = [f"{days[0]}T{hour:02d}:00" for hour in range(24)]
    return {
        "current": {
            "temperature_2m": temp, "apparent_temperature": temp - 1,
            "relative_humidity_2m": 55, "is_day": 1, "precipitation": 0,
            "weather_code": code, "wind_speed_10m": wind,
            "wind_direction_10m": wind_dir,
        },
        "hourly": {
            "time": hourly_times,
            "temperature_2m": [temp - 5] * 24,
            "precipitation_probability": [20] * 24,
            "weather_code": [code] * 24,
        },
        "daily": {
            "time": days,
            "weather_code": [code] * 7,
            "temperature_2m_max": [temp + 2] * 7,
            "temperature_2m_min": [temp - 8] * 7,
            "sunrise": [f"{day}T05:30" for day in days],
            "sunset": [f"{day}T18:30" for day in days],
            "precipitation_probability_max": [10] * 7,
            "uv_index_max": [3] * 7,
        },
    }


def _fake_requests(monkeypatch, *, forecast=None, geo=None, aqi=None, calls=None):
    import requests

    forecast = forecast or _forecast_payload()
    geo = geo if geo is not None else _geo_payload()
    aqi = aqi if aqi is not None else {"current": {"us_aqi": 42, "pm2_5": 12.5, "pm10": 20}}
    calls = calls if calls is not None else []

    def get(url, **kwargs):
        calls.append((url, kwargs.get("params") or {}))
        if "geocoding-api" in url:
            if isinstance(geo, Exception):
                raise geo
            data = geo
        elif "air-quality-api" in url:
            if isinstance(aqi, Exception):
                raise aqi
            data = aqi
        else:
            if isinstance(forecast, Exception):
                raise forecast
            data = forecast
        return SimpleNamespace(json=lambda: data, raise_for_status=lambda: None)

    monkeypatch.setattr(requests, "get", get)
    return calls


# ---------------------------------------------------------------------------
# Geocoding, current conditions and formatting
# ---------------------------------------------------------------------------

def test_now_report_uses_real_open_meteo_data(monkeypatch):
    _fake_requests(monkeypatch)
    result = wr.weather_action({"city": "Guwahati"})
    assert "Guwahati, Assam, India" in result
    assert "30°C" in result and "clear sky" in result
    assert "32°C" in result and "22°C" in result
    assert "12 km/h E" in result
    assert "Humidity 55%" in result


@pytest.mark.parametrize("code,label", [
    (0, "Clear sky"), (45, "Fog"), (61, "Slight rain"),
    (75, "Heavy snowfall"), (95, "Thunderstorm"), (99, "Thunderstorm with heavy hail"),
])
def test_wmo_codes_are_human_readable(monkeypatch, code, label):
    assert wr.WMO_CODES[code] == label
    _fake_requests(monkeypatch, forecast=_forecast_payload(code=code))
    assert label.lower() in wr.weather_action({"city": "Guwahati"}).lower()


@pytest.mark.parametrize("degrees,direction", [(0, "N"), (90, "E"), (180, "S"), (270, "W")])
def test_wind_cardinal_directions(degrees, direction):
    assert wr._wind_direction(degrees) == direction


def test_qualifier_disambiguates_city(monkeypatch):
    geo = {"results": [
        {"name": "Springfield", "latitude": 1, "longitude": 2,
         "admin1": "Illinois", "country": "United States"},
        {"name": "Springfield", "latitude": 3, "longitude": 4,
         "admin1": "Massachusetts", "country": "United States"},
    ]}
    calls = _fake_requests(monkeypatch, geo=geo)
    result = wr.weather_action({"city": "Springfield, Illinois"})
    assert "Springfield, Illinois" in result
    assert calls[0][1]["name"] == "Springfield"


def test_unknown_city_message(monkeypatch):
    _fake_requests(monkeypatch, geo={"results": []})
    result = wr.weather_action({"city": "Nowhereville"})
    assert "couldn't find" in result


# ---------------------------------------------------------------------------
# Units and time windows
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("units,symbol,speed", [
    ("metric", "°C", "km/h"), ("celsius", "°C", "km/h"), ("c", "°C", "km/h"),
    ("imperial", "°F", "mph"), ("fahrenheit", "°F", "mph"), ("f", "°F", "mph"),
])
def test_units_are_applied(monkeypatch, units, symbol, speed):
    calls = _fake_requests(monkeypatch)
    result = wr.weather_action({"city": "Guwahati", "units": units})
    assert symbol in result and speed in result
    params = calls[1][1]
    expected_temp = "fahrenheit" if units in ("imperial", "fahrenheit", "f") else "celsius"
    assert params["temperature_unit"] == expected_temp
    assert params["wind_speed_unit"] == ("mph" if speed == "mph" else "kmh")


@pytest.mark.parametrize("when,expected", [
    ("now", "30°C"), ("today", "30°C"), ("tonight", "Tonight"),
    ("morning", "This morning"), ("afternoon", "This afternoon"),
    ("tomorrow", "Tomorrow"), ("week", "Forecast"),
    ("7 days", "Forecast"), ("next 5 days", "Forecast"), ("weekend", "Forecast"),
])
def test_time_modes(monkeypatch, when, expected):
    _fake_requests(monkeypatch)
    assert expected in wr.weather_action({"city": "Guwahati", "time": when})


def test_specific_weekday(monkeypatch):
    _fake_requests(monkeypatch)
    weekday = datetime.fromisoformat(_dates()[2]).strftime("%A").lower()
    result = wr.weather_action({"city": "Guwahati", "time": weekday})
    assert weekday.capitalize() in result


# ---------------------------------------------------------------------------
# Air quality
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("aqi,band", [
    (25, "Good"), (75, "Moderate"), (120, "Unhealthy for Sensitive Groups"),
    (180, "Unhealthy"), (250, "Very Unhealthy"), (350, "Hazardous"),
])
def test_aqi_bands(monkeypatch, aqi, band):
    _fake_requests(monkeypatch, aqi={"current": {"us_aqi": aqi, "pm2_5": 9.0, "pm10": 15}})
    result = wr.weather_action({"city": "Guwahati", "include_aqi": True})
    assert band in result and "PM2.5" in result


def test_aqi_focus_and_time_keywords(monkeypatch):
    _fake_requests(monkeypatch)
    assert "AQI" in wr.weather_action({"city": "Guwahati", "focus": "air quality"})
    assert "AQI" in wr.weather_action({"city": "Guwahati", "time": "aqi"})


def test_aqi_failure_degrades_gracefully(monkeypatch):
    _fake_requests(monkeypatch, aqi=TimeoutError("air endpoint down"))
    result = wr.weather_action({"city": "Guwahati", "include_aqi": True})
    assert "30°C" in result and "AQI" not in result


# ---------------------------------------------------------------------------
# Cache, browser behaviour, failures and integration
# ---------------------------------------------------------------------------

def test_ttl_cache_avoids_repeat_http(monkeypatch):
    calls = _fake_requests(monkeypatch)
    wr.weather_action({"city": "Guwahati"})
    wr.weather_action({"city": "Guwahati"})
    assert len(calls) == 2  # one geocode + one forecast, then cached


def test_open_browser_defaults_false_and_can_be_enabled(monkeypatch):
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    _fake_requests(monkeypatch)
    wr.weather_action({"city": "Guwahati"})
    assert opened == []
    wr.weather_action({"city": "Delhi", "open_browser": True})
    assert len(opened) == 1 and "google.com" in opened[0]


def test_network_failure_is_honest(monkeypatch):
    _fake_requests(monkeypatch, geo=RuntimeError("offline"))
    assert "unavailable" in wr.weather_action({"city": "Guwahati"}).lower()


def test_saved_city_fallback(monkeypatch):
    import memory.memory_manager as mm

    monkeypatch.setattr(mm, "load_memory", lambda: {
        "identity": {"city": {"value": "Guwahati"}}
    })
    _fake_requests(monkeypatch)
    assert "Guwahati" in wr.weather_action({})
    assert "Guwahati" in wr.weather_action({"city": "here"})


def test_missing_city_prompt(monkeypatch):
    import memory.memory_manager as mm

    monkeypatch.setattr(mm, "load_memory", lambda: {"identity": {}})
    assert "which city" in wr.weather_action(None).lower()


def test_session_memory_and_daily_briefing_integration(monkeypatch):
    import requests

    calls = _fake_requests(monkeypatch)
    memory = SimpleNamespace(calls=[])

    def remember(**kwargs):
        memory.calls.append(kwargs)

    memory.set_last_search = remember
    wr.weather_action({"city": "Guwahati"}, session_memory=memory)
    assert memory.calls[0]["query"] == "weather in Guwahati now"

    from actions import daily_briefing

    monkeypatch.setattr(daily_briefing, "_get_memory", lambda: "")
    monkeypatch.setattr(daily_briefing, "_get_system", lambda: "")
    briefing = daily_briefing.daily_briefing({"city": "Guwahati"})
    assert "Guwahati" in briefing and "30°C" in briefing
    assert len(calls) >= 2
