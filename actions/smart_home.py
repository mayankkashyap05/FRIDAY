# actions/smart_home.py
# Friday — domain-aware smart home control (Home Assistant, webhook, simulated).

from __future__ import annotations

import difflib
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = get_base_dir() / "config" / "smart_home.json"

DEFAULT_CONFIG = {
    "enabled": True,
    "mode": "simulated",
    "home_assistant": {"url": "", "token": ""},
    "webhook": {"url": ""},
    "devices": [],
}

# simulated mode keeps an in-memory state map so the tool is testable offline
_SIM_STATE: dict = {}

_COLORS = {
    "warm white": [255, 224, 170],
    "white": [255, 255, 255],
    "red": [255, 0, 0],
    "green": [0, 255, 0],
    "blue": [0, 0, 255],
    "purple": [128, 0, 128],
    "yellow": [255, 255, 0],
    "orange": [255, 165, 0],
    "cyan": [0, 255, 255],
    "pink": [255, 192, 203],
}

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday"]


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for key, value in DEFAULT_CONFIG.items():
                data.setdefault(key, value)
            return data
        except Exception as e:
            print(f"[SmartHome] Could not parse config: {e}")
    return json.loads(json.dumps(DEFAULT_CONFIG))


def _validate_url(url: str, purpose: str) -> str:
    text = str(url or "").strip()
    scheme = urllib.parse.urlparse(text).scheme.lower()
    if scheme not in ("http", "https"):
        raise RuntimeError(f"{purpose} URL must start with http:// or https://")
    return text.rstrip("/")


def _ha_credentials(cfg: dict) -> tuple[str, str]:
    ha = cfg.get("home_assistant", {}) if isinstance(cfg, dict) else {}
    url = os.environ.get("FRIDAY_HA_URL") or ha.get("url") or ""
    token = os.environ.get("FRIDAY_HA_TOKEN") or ha.get("token") or ""
    return str(url).strip().rstrip("/"), str(token).strip()


def _map_http_error(exc: Exception) -> RuntimeError:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 401:
            return RuntimeError("Home Assistant rejected the token (401).")
        if exc.code == 404:
            return RuntimeError("Home Assistant entity or endpoint not found (404).")
        if exc.code == 403:
            return RuntimeError("Home Assistant denied access (403).")
        return RuntimeError(f"Home Assistant returned HTTP {exc.code}.")
    if isinstance(exc, socket.timeout):
        return RuntimeError("Home Assistant request timed out.")
    if isinstance(exc, urllib.error.URLError):
        reason = str(getattr(exc, "reason", exc)).lower()
        if "refused" in reason:
            return RuntimeError("Home Assistant connection refused.")
        return RuntimeError(f"Home Assistant connection failed: {getattr(exc, 'reason', exc)}")
    return RuntimeError(f"Home Assistant request failed: {exc}")


def _ha_request(cfg: dict, method: str, path: str, body=None):
    base, token = _ha_credentials(cfg)
    if not base or not token:
        raise RuntimeError(
            "Home Assistant is not configured (url/token missing in config/smart_home.json).")
    url = _validate_url(base, "Home Assistant") + path
    req = urllib.request.Request(
        url, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    if body is not None:
        req.data = json.dumps(body).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except Exception as exc:
        raise _map_http_error(exc) from exc


def _find_devices(cfg: dict, query=None, room=None, device_type=None) -> list[dict]:
    """Exact id/name/alias first, then room/type and fuzzy/substring matching."""
    devices = cfg.get("devices", []) or []
    q = str(query or "").strip().lower()
    wanted_room = str(room or "").strip().lower()
    wanted_type = str(device_type or "").strip().lower()
    type_hints = [word for word in ("light", "fan", "cover", "lock", "switch",
                                    "media_player", "climate") if word in q]
    known_rooms = {
        word for item in devices
        for word in re.findall(r"\w+", str(item.get("room") or item.get("area") or "").lower())
        if len(word) > 2
    }
    room_words = [word for word in re.findall(r"\w+", q) if word in known_rooms]
    scored: list[tuple[int, dict]] = []

    for device in devices:
        name = str(device.get("name") or "").lower()
        did = str(device.get("id") or "").lower()
        aliases = [str(a).lower() for a in (device.get("aliases") or [])]
        actual_room = str(device.get("room") or device.get("area") or "").lower()
        dtype = str(device.get("type") or "").lower()
        entity = str(device.get("entity") or "").lower()
        if type_hints and not any(hint in dtype or hint in entity for hint in type_hints):
            continue
        if wanted_room and wanted_room not in actual_room:
            continue
        if room_words and not any(word in actual_room for word in room_words):
            continue
        if wanted_type and wanted_type not in dtype and wanted_type not in entity.split(".")[0]:
            continue

        score = 0
        if q:
            if q in (did, name) or q in aliases:
                score = 100
            elif any(q in alias or alias in q for alias in aliases if len(q) > 2):
                score = 80
            elif q in actual_room or (actual_room and actual_room in q):
                score = 60
            elif q in dtype or dtype in q:
                score = 50
            elif q in name or name in q or q in did or did in q:
                score = 40
            elif q in entity or difflib.get_close_matches(q, [name, did, entity], n=1, cutoff=0.72):
                score = 20
            elif not wanted_room and not wanted_type:
                continue
        scored.append((score, device))

    scored.sort(key=lambda row: -row[0])
    best = scored[0][0] if scored else 0
    if q and best >= 100:
        return [row[1] for row in scored if row[0] >= 100]
    if q and best > 0:
        return [row[1] for row in scored if row[0] >= max(20, best - 20)]
    return [row[1] for row in scored]


def _find_device(cfg: dict, key: str):
    """Back-compatible single-device lookup."""
    matches = _find_devices(cfg, key)
    return matches[0] if matches else None


def _sim_get(device_id: str) -> dict:
    return dict(_SIM_STATE.get(str(device_id), {"state": "off"}))


def _sim_set(device_id: str, state: dict):
    merged = _sim_get(device_id)
    merged.update(state)
    _SIM_STATE[str(device_id)] = merged


def _coerce_value(value):
    if isinstance(value, bool):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value


def _color_values(value, kwargs) -> dict:
    text = str(value or kwargs.get("color") or "").strip().lower()
    if "kelvin" in kwargs:
        return {"color_temp_kelvin": max(1000, min(10000, int(_coerce_value(kwargs["kelvin"]))))}
    if text.startswith("#") and re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        rgb = [int(text[i:i + 2], 16) for i in (1, 3, 5)]
        return {"rgb_color": rgb}
    if text in _COLORS:
        return {"rgb_color": _COLORS[text]}
    return {}


def _domain_of(device: dict) -> str:
    entity = str(device.get("entity") or "").lower()
    if "." in entity:
        return entity.split(".", 1)[0]
    return str(device.get("type") or "light").lower()


def _service(cfg: dict, domain: str, service: str, body: dict):
    return _ha_request(cfg, "POST", f"/api/services/{domain}/{service}", body)


def _control_home_assistant(device: dict, action: str, value, **kwargs):
    """Route an action to the correct Home Assistant domain/service."""
    cfg = kwargs.get("cfg") or _load_config()
    entity = str(device.get("entity") or "").strip()
    if not entity:
        raise RuntimeError(f"Device '{device.get('name')}' has no 'entity' set.")
    domain = _domain_of(device)
    numeric = _coerce_value(value)
    name = device.get("name") or device.get("id")

    if action == "status":
        state = _ha_request(cfg, "GET", f"/api/states/{entity}") or {}
        attrs = state.get("attributes", {}) or {}
        detail = ""
        if "brightness" in attrs:
            detail = f", brightness {round(float(attrs['brightness']) / 2.55)}%"
        elif "temperature" in attrs:
            detail = f", {attrs['temperature']}°"
        elif "current_position" in attrs:
            detail = f", position {attrs['current_position']}%"
        return f"{name}: {state.get('state', 'unknown')}{detail}"

    if action in ("turn_on", "turn_off", "toggle"):
        _service(cfg, domain, action, {"entity_id": entity})
        return f"{name}: {action} OK"

    if action in ("set_level", "brightness", "dim"):
        level = int(numeric) if isinstance(numeric, (int, float)) else 0
        if domain == "fan":
            _service(cfg, "fan", "set_percentage",
                     {"entity_id": entity, "percentage": max(0, min(100, level))})
        elif domain == "cover":
            _service(cfg, "cover", "set_cover_position",
                     {"entity_id": entity, "position": max(0, min(100, level))})
        elif domain == "media_player":
            volume = level if 0 <= level <= 1 else level / 100.0
            _service(cfg, "media_player", "volume_set",
                     {"entity_id": entity, "volume_level": max(0.0, min(1.0, volume))})
        else:
            _service(cfg, domain, "turn_on",
                     {"entity_id": entity, "brightness_pct": max(0, min(100, level))})
        return f"{name}: level set to {level}"

    if action == "set_temperature":
        temperature = kwargs.get("temperature", value)
        body = {"entity_id": entity, "temperature": _coerce_value(temperature)}
        if kwargs.get("hvac_mode"):
            body["hvac_mode"] = str(kwargs["hvac_mode"]).lower()
        _service(cfg, "climate", "set_temperature", body)
        return f"{name}: temperature set to {body['temperature']}"

    if action == "set_color":
        body = {"entity_id": entity}
        body.update(_color_values(value, kwargs))
        if len(body) == 1:
            raise RuntimeError(f"Unknown colour '{value}', sir.")
        _service(cfg, "light", "turn_on", body)
        return f"{name}: colour set"

    if action == "open":
        _service(cfg, "cover", "open_cover", {"entity_id": entity})
        return f"{name}: opened"
    if action == "close":
        _service(cfg, "cover", "close_cover", {"entity_id": entity})
        return f"{name}: closed"
    if action == "lock":
        _service(cfg, "lock", "lock", {"entity_id": entity})
        return f"{name}: locked"
    if action == "unlock":
        _service(cfg, "lock", "unlock", {"entity_id": entity})
        return f"{name}: unlocked"
    if action in ("activate", "scene", "script"):
        _service(cfg, domain, "turn_on", {"entity_id": entity})
        return f"{name}: activated"

    raise RuntimeError(f"Unknown smart-home action '{action}'.")


def _control_webhook(cfg: dict, device: dict, action: str, value, **kwargs):
    url = _validate_url((cfg.get("webhook", {}) or {}).get("url"), "Webhook")
    payload = {
        "device": device.get("id") or device.get("name"),
        "action": action,
        "value": value,
    }
    for key in ("room", "color", "temperature", "hvac_mode", "domain"):
        if kwargs.get(key) is not None:
            payload[key] = kwargs[key]
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:
        raise _map_http_error(exc) from exc
    return f"{device.get('name')}: {action} sent to webhook"


def _list_status(cfg: dict) -> str:
    devices = cfg.get("devices", []) or []
    if not devices:
        return "No devices configured. Add them to config/smart_home.json"
    mode = cfg.get("mode", "simulated")
    lines = []
    for device in devices:
        name = device.get("name") or device.get("id")
        if mode == "simulated":
            st = _sim_get(str(device.get("id")))
            extras = []
            for key in ("level", "temperature", "color", "position"):
                if key in st:
                    extras.append(f"{key} {st[key]}")
            if "locked" in st:
                extras.append("locked" if st["locked"] else "unlocked")
            lines.append(f"- {name} ({device.get('type')}): {st.get('state')}"
                         + (", " + ", ".join(extras) if extras else ""))
        elif mode == "webhook":
            # Webhook mode has no state API — list configuration, never call HA.
            lines.append(f"- {name} ({device.get('type')}): webhook target configured")
        else:
            try:
                lines.append(f"- {name}: {_control_home_assistant(device, 'status', None, cfg=cfg)}")
            except Exception as e:
                lines.append(f"- {name}: error ({e})")
    return "\n".join(lines)


def _simulate(device: dict, action: str, value, kwargs) -> str:
    did = str(device.get("id"))
    state = _sim_get(did)
    name = device.get("name") or did
    if action in ("turn_on", "activate", "scene", "script"):
        update = {"state": "on"}
        if value is not None:
            update["level"] = _coerce_value(value)
        _sim_set(did, update)
        return f"{name}: turned on"
    if action == "turn_off":
        _sim_set(did, {"state": "off"})
        return f"{name}: turned off"
    if action == "toggle":
        new_state = "off" if state.get("state") == "on" else "on"
        _sim_set(did, {"state": new_state})
        return f"{name}: toggled to {new_state}"
    if action in ("set_level", "brightness", "dim"):
        level = _coerce_value(value if value is not None else kwargs.get("level"))
        _sim_set(did, {"level": level, "state": "on" if level else "off"})
        return f"{name}: level set to {level}"
    if action == "set_temperature":
        temperature = _coerce_value(kwargs.get("temperature", value))
        update = {"temperature": temperature, "state": "on"}
        if kwargs.get("hvac_mode"):
            update["hvac_mode"] = str(kwargs["hvac_mode"]).lower()
        _sim_set(did, update)
        return f"{name}: temperature set to {temperature}"
    if action == "set_color":
        colors = _color_values(value, kwargs)
        if not colors:
            return f"Unknown colour '{value}', sir."
        _sim_set(did, {"color": colors.get("rgb_color") or colors.get("color_temp_kelvin"),
                       "state": "on"})
        return f"{name}: colour set"
    if action in ("open", "close"):
        _sim_set(did, {"position": 100 if action == "open" else 0,
                       "state": "on" if action == "open" else "off"})
        return f"{name}: {action}ed"
    if action in ("lock", "unlock"):
        _sim_set(did, {"locked": action == "lock"})
        return f"{name}: {action}ed"
    return f"{name}: {state.get('state')}"


def _discover(cfg: dict, kwargs) -> str:
    query = str(kwargs.get("query") or "").strip().lower()
    domain = str(kwargs.get("domain") or "").strip().lower()
    if cfg.get("mode") == "home_assistant":
        states = _ha_request(cfg, "GET", "/api/states") or []
        matches = []
        for item in states if isinstance(states, list) else []:
            entity = str(item.get("entity_id") or "")
            if domain and not entity.startswith(domain + "."):
                continue
            if query and query not in entity.lower() and query not in str(item.get("attributes", {}).get("friendly_name", "")).lower():
                continue
            matches.append(f"- {entity}: {item.get('state')}")
        return "\n".join(matches[:40]) if matches else "No matching Home Assistant entities."
    devices = _find_devices(cfg, query, kwargs.get("room"), domain or kwargs.get("device_type"))
    return "\n".join(
        f"- {d.get('name')} ({_domain_of(d)}): simulated"
        for d in devices[:40]) or "No matching devices."


def smart_home_control(action: str = "status", device: str = None, value=None,
                       **kwargs) -> str:
    """Backward-compatible public entry point for every smart-home action."""
    kwargs = dict(kwargs)
    cfg = kwargs.pop("cfg", None) or _load_config()
    if not cfg.get("enabled", True):
        return "Smart home is disabled in config/smart_home.json"

    action = str(action or "status").strip().lower()
    aliases = {"on": "turn_on", "off": "turn_off", "brightness": "set_level",
               "dim": "set_level", "temp": "set_temperature",
               "colour": "set_color", "list": "status", "all": "status"}
    action = aliases.get(action, action)
    mode = cfg.get("mode", "simulated")

    if action == "health":
        if mode == "home_assistant":
            try:
                _ha_request(cfg, "GET", "/api/")
                return "Home Assistant is healthy."
            except Exception as e:
                return f"Home Assistant health check failed: {e}"
        return f"Smart home is running in {mode} mode."
    if action == "discover":
        try:
            return _discover(cfg, kwargs)
        except Exception as e:
            return f"Smart home discovery failed: {e}"

    if action == "status" and not device:
        return _list_status(cfg)

    matches = _find_devices(cfg, device, kwargs.get("room"), kwargs.get("device_type"))
    if not matches:
        return f"Device '{device}' not found in config/smart_home.json" if device else \
            "Please specify a device id or name."

    if len(matches) > 1:
        results = []
        for item in matches:
            results.append(smart_home_control(action=action, device=str(item.get("id")),
                                              value=value, **{**kwargs, "cfg": cfg}))
        return "\n".join(results)

    chosen = matches[0]
    try:
        if mode == "home_assistant":
            return _control_home_assistant(chosen, action, value, cfg=cfg, **kwargs)
        if mode == "webhook":
            return _control_webhook(cfg, chosen, action, value, **kwargs)
        return _simulate(chosen, action, value, kwargs)
    except Exception as e:
        return f"Smart home error: {e}"
