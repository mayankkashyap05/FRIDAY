"""100%-offline combinatorial tests for actions.smart_home."""

from __future__ import annotations

import json
import socket
import urllib.error
from pathlib import Path

import pytest

from actions import smart_home as sh


@pytest.fixture(autouse=True)
def _config(monkeypatch, tmp_path):
    sh._SIM_STATE.clear()
    monkeypatch.setattr(sh, "CONFIG_PATH", tmp_path / "smart_home.json", raising=False)
    monkeypatch.delenv("FRIDAY_HA_URL", raising=False)
    monkeypatch.delenv("FRIDAY_HA_TOKEN", raising=False)
    yield tmp_path
    sh._SIM_STATE.clear()


def _write(cfg, tmp_path):
    (tmp_path / "smart_home.json").write_text(json.dumps(cfg), encoding="utf-8")
    return cfg


def _ha_cfg(devices=None):
    return {
        "enabled": True, "mode": "home_assistant",
        "home_assistant": {"url": "http://ha.local:8123", "token": "token"},
        "webhook": {"url": "http://hooks.local/event"},
        "devices": devices if devices is not None else [
            {"id": "lamp", "name": "Desk Lamp", "entity": "light.desk", "type": "light"}],
    }


def _record_requests(monkeypatch, response=None):
    calls = []

    def fake(cfg, method, path, body=None):
        calls.append((method, path, body))
        return response

    monkeypatch.setattr(sh, "_ha_request", fake)
    return calls


# ---------------------------------------------------------------------------
# Domain-aware Home Assistant routing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action,value,kwargs,path,body", [
    ("turn_on", None, {}, "/api/services/light/turn_on", {"entity_id": "light.desk"}),
    ("turn_off", None, {}, "/api/services/light/turn_off", {"entity_id": "light.desk"}),
    ("toggle", None, {}, "/api/services/light/toggle", {"entity_id": "light.desk"}),
    ("set_level", 50, {}, "/api/services/light/turn_on",
     {"entity_id": "light.desk", "brightness_pct": 50}),
    ("set_color", "red", {}, "/api/services/light/turn_on",
     {"entity_id": "light.desk", "rgb_color": [255, 0, 0]}),
    ("set_color", "#00FF00", {}, "/api/services/light/turn_on",
     {"entity_id": "light.desk", "rgb_color": [0, 255, 0]}),
])
def test_light_domain_routing(action, value, kwargs, path, body, monkeypatch):
    calls = _record_requests(monkeypatch)
    sh._control_home_assistant(_ha_cfg()["devices"][0], action, value, **kwargs)
    assert calls[-1][1] == path
    assert calls[-1][2] == body


def test_color_kelvin_and_temperature(monkeypatch):
    calls = _record_requests(monkeypatch)
    sh._control_home_assistant(_ha_cfg()["devices"][0], "set_color", None,
                               kelvin=3000)
    assert calls[-1][2] == {"entity_id": "light.desk", "color_temp_kelvin": 3000}
    climate = {"id": "ac", "name": "AC", "entity": "climate.bedroom", "type": "climate"}
    sh._control_home_assistant(climate, "set_temperature", 24, hvac_mode="cool")
    assert calls[-1][1] == "/api/services/climate/set_temperature"
    assert calls[-1][2] == {"entity_id": "climate.bedroom", "temperature": 24,
                            "hvac_mode": "cool"}


@pytest.mark.parametrize("device,action,value,path,body", [
    ({"id": "fan", "entity": "fan.room", "name": "Fan"}, "set_level", 40,
     "/api/services/fan/set_percentage", {"entity_id": "fan.room", "percentage": 40}),
    ({"id": "blind", "entity": "cover.window", "name": "Blind"}, "open", None,
     "/api/services/cover/open_cover", {"entity_id": "cover.window"}),
    ({"id": "blind", "entity": "cover.window", "name": "Blind"}, "close", None,
     "/api/services/cover/close_cover", {"entity_id": "cover.window"}),
    ({"id": "blind", "entity": "cover.window", "name": "Blind"}, "set_level", 30,
     "/api/services/cover/set_cover_position", {"entity_id": "cover.window", "position": 30}),
    ({"id": "lock", "entity": "lock.front", "name": "Lock"}, "lock", None,
     "/api/services/lock/lock", {"entity_id": "lock.front"}),
    ({"id": "lock", "entity": "lock.front", "name": "Lock"}, "unlock", None,
     "/api/services/lock/unlock", {"entity_id": "lock.front"}),
    ({"id": "speaker", "entity": "media_player.living", "name": "Speaker"}, "set_level", 50,
     "/api/services/media_player/volume_set",
     {"entity_id": "media_player.living", "volume_level": 0.5}),
    ({"id": "scene", "entity": "scene.movie", "name": "Movie"}, "activate", None,
     "/api/services/scene/turn_on", {"entity_id": "scene.movie"}),
])
def test_other_domains_route_correctly(device, action, value, path, body, monkeypatch):
    calls = _record_requests(monkeypatch)
    sh._control_home_assistant(device, action, value)
    assert calls[-1][1] == path and calls[-1][2] == body


def test_status_reports_brightness(monkeypatch):
    _record_requests(monkeypatch, {"state": "on", "attributes": {"brightness": 128}})
    result = sh._control_home_assistant(_ha_cfg()["devices"][0], "status", None)
    assert "on" in result and "50%" in result


# ---------------------------------------------------------------------------
# Health, discovery, HTTP errors, env overrides
# ---------------------------------------------------------------------------

def test_health_and_discover(monkeypatch, tmp_path):
    cfg = _ha_cfg()
    _write(cfg, tmp_path)
    calls = _record_requests(monkeypatch, [{"entity_id": "light.desk", "state": "on"}])
    assert "healthy" in sh.smart_home_control(action="health").lower()
    assert calls[-1][1] == "/api/"
    result = sh.smart_home_control(action="discover", domain="light")
    assert "light.desk" in result
    assert calls[-1][1] == "/api/states"


@pytest.mark.parametrize("error,expected", [
    (urllib.error.HTTPError("http://x", 401, "no", {}, None), "token"),
    (urllib.error.HTTPError("http://x", 404, "no", {}, None), "not found"),
    (socket.timeout(), "timed out"),
    (urllib.error.URLError("connection refused"), "refused"),
])
def test_http_errors_are_clean(error, expected, monkeypatch):
    def broken(*a, **k):
        raise error

    monkeypatch.setattr(sh.urllib.request, "urlopen", broken)
    with pytest.raises(RuntimeError) as exc:
        sh._ha_request(_ha_cfg(), "GET", "/api/")
    assert expected in str(exc.value)


def test_environment_overrides_url_and_token(monkeypatch):
    monkeypatch.setenv("FRIDAY_HA_URL", "https://env-ha.local/")
    monkeypatch.setenv("FRIDAY_HA_TOKEN", "env-token")
    captured = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def fake(req, timeout):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        return Resp()

    monkeypatch.setattr(sh.urllib.request, "urlopen", fake)
    sh._ha_request(_ha_cfg(), "GET", "/api/")
    assert captured["url"] == "https://env-ha.local/api/"
    assert captured["auth"] == "Bearer env-token"


@pytest.mark.parametrize("cfg,action", [
    ({"mode": "home_assistant", "home_assistant": {"url": "ftp://bad", "token": "t"}}, "health"),
    ({"mode": "webhook", "webhook": {"url": "file:///etc/passwd"}}, "turn_on"),
])
def test_invalid_url_schemes_rejected(cfg, action, monkeypatch, tmp_path):
    cfg["enabled"] = True
    cfg["devices"] = [{"id": "lamp", "name": "Lamp", "entity": "light.lamp"}]
    _write(cfg, tmp_path)
    result = sh.smart_home_control(action=action, device="lamp")
    assert "http" in result.lower() or "scheme" in result.lower()


# ---------------------------------------------------------------------------
# Fuzzy/group control, webhook list, simulated state
# ---------------------------------------------------------------------------

def test_room_group_control(monkeypatch, tmp_path):
    devices = [
        {"id": f"bed_light_{i}", "name": f"Bedroom Light {i}",
         "entity": f"light.bed{i}", "room": "bedroom", "type": "light"}
        for i in range(3)
    ] + [{"id": "kitchen", "name": "Kitchen Light", "entity": "light.kitchen",
          "room": "kitchen", "type": "light"}]
    _write({"enabled": True, "mode": "simulated", "devices": devices}, tmp_path)
    for d in devices:
        sh._SIM_STATE[d["id"]] = {"state": "on"}
    result = sh.smart_home_control(action="turn_off", device="all bedroom lights")
    assert result.count("off") >= 3
    assert all(sh._SIM_STATE[d["id"]]["state"] == "off" for d in devices[:3])
    assert sh._SIM_STATE["kitchen"]["state"] == "on"


def test_webhook_list_never_calls_home_assistant(monkeypatch, tmp_path):
    _write({"enabled": True, "mode": "webhook", "webhook": {"url": "http://hook"},
            "devices": [{"id": "lamp", "name": "Lamp", "type": "light"}]}, tmp_path)

    def forbidden(*a, **k):
        raise AssertionError("webhook list must not call Home Assistant")

    monkeypatch.setattr(sh, "_control_home_assistant", forbidden)
    result = sh.smart_home_control(action="list")
    assert "Lamp" in result and "webhook" in result


def test_simulated_mode_stores_rich_state_and_respects_zero(monkeypatch, tmp_path):
    _write({"enabled": True, "mode": "simulated", "devices": [
        {"id": "lamp", "name": "Lamp", "type": "light"}]}, tmp_path)
    sh.smart_home_control(action="set_level", device="lamp", value=0)
    assert sh._SIM_STATE["lamp"]["level"] == 0
    sh.smart_home_control(action="set_temperature", device="lamp", value=22,
                          hvac_mode="heat")
    assert sh._SIM_STATE["lamp"]["temperature"] == 22
    assert sh._SIM_STATE["lamp"]["hvac_mode"] == "heat"
    sh.smart_home_control(action="set_color", device="lamp", value="warm white")
    assert sh._SIM_STATE["lamp"]["color"] == [255, 224, 170]
    sh.smart_home_control(action="open", device="lamp")
    assert sh._SIM_STATE["lamp"]["position"] == 100
    sh.smart_home_control(action="lock", device="lamp")
    assert sh._SIM_STATE["lamp"]["locked"] is True
    listed = sh.smart_home_control(action="status")
    assert "level 0" in listed and "locked" in listed


def test_disabled_config_and_safe_defaults(monkeypatch, tmp_path):
    _write({"enabled": False, "mode": "simulated", "devices": []}, tmp_path)
    assert "disabled" in sh.smart_home_control().lower()
    _write({"enabled": True, "mode": "simulated", "devices": []}, tmp_path)
    assert "No devices" in sh.smart_home_control()
