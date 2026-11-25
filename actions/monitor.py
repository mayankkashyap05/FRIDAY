# actions/monitor.py
# Friday — PC monitoring rules + new-mail watcher.
#
# Rules are stored in config/monitor.json and checked by the background loop
# in main.py. Every rule supports sustained breaches and its own cooldown.

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    import psutil
    _PSUTIL = True
except ImportError:
    psutil = None
    _PSUTIL = False


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_FILE = get_base_dir() / "config" / "monitor.json"
# Historical name used by callers/tests to point at a temporary rules file.
RULES_FILE = CONFIG_FILE
STATE_FILE = get_base_dir() / "config" / "monitor_state.json"
WATCH_FILE = get_base_dir() / "config" / "email_watch.json"

_lock = threading.RLock()
_rules = None
_loaded = False
_paused = None
_sustained_since: dict[int, float] = {}

_WATCH_DEFAULT = {
    "enabled": False,
    "last_uid": 0,
    "interval": 120,
    "sender": "",
    "subject": "",
    "unread_only": False,
    "seeded": False,
}
_DEFAULT_COOLDOWN = 300
_KINDS = {"cpu", "ram", "disk", "battery", "temp", "process_exit"}


def _atomic_write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="friday-monitor-", suffix=".json",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load() -> list[dict]:
    global _rules, _loaded
    with _lock:
        if _loaded and isinstance(_rules, list):
            return _rules
        try:
            data = json.loads(RULES_FILE.read_text(encoding="utf-8")) if RULES_FILE.exists() else []
            _rules = data if isinstance(data, list) else []
        except Exception:
            _rules = []
        _loaded = True
        return _rules


def _save() -> None:
    with _lock:
        _atomic_write(RULES_FILE, _rules or [])


def _load_paused() -> bool:
    global _paused
    if _paused is None:
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
            _paused = bool(data.get("paused", False)) if isinstance(data, dict) else False
        except Exception:
            _paused = False
    return bool(_paused)


def _save_paused(paused: bool) -> None:
    global _paused
    _paused = bool(paused)
    try:
        _atomic_write(STATE_FILE, {"paused": _paused})
    except Exception:
        pass


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _read_value(kind: str, params: dict | None = None) -> tuple[float | None, str]:
    """Return (current_value, human_label). None means unavailable."""
    kind = str(kind or "").lower().strip()
    params = params or {}
    if not _PSUTIL:
        return None, "psutil not installed"
    try:
        if kind == "cpu":
            return float(psutil.cpu_percent(interval=0.4)), "CPU usage"
        if kind == "ram":
            return float(psutil.virtual_memory().percent), "RAM usage"
        if kind == "disk" or kind.startswith("disk:"):
            drive = kind.split(":", 1)[1].strip().upper() if ":" in kind else str(get_base_dir())[:3]
            if len(drive) == 1:
                drive += ":"  # disk:D → D:
            if not drive:
                drive = str(get_base_dir())[:3]
            return float(psutil.disk_usage(drive).percent), f"Disk ({drive})"
        if kind == "battery":
            battery = psutil.sensors_battery()
            if battery is None:
                return None, "Battery"
            return float(battery.percent), "Battery"
        if kind in ("temp", "temperature"):
            from actions.guardian import _temp_c
            value = _temp_c()
            return (float(value) if value is not None else None), "Temperature"
        if kind in ("process_exit", "process"):
            name = str(params.get("process", params.get("process_name", ""))).strip().lower()
            if not name:
                return None, "Process"
            alive = any(name in (p.info.get("name") or "").lower()
                        for p in psutil.process_iter(["name"]))
            return (1.0 if alive else 0.0), f"Process {name}"
    except Exception:
        pass
    return None, kind.title() or "Unknown metric"


def _valid_kind(kind: str) -> bool:
    return kind in _KINDS or kind.startswith("disk:")


def add_rule(kind: str, threshold: float, above: bool = True,
             message: str = "", sustained: float = 0,
             cooldown: float = _DEFAULT_COOLDOWN,
             process: str = "") -> str:
    rules = _load()
    kind = str(kind or "").lower().strip()
    if not _valid_kind(kind):
        return (f"Unknown metric '{kind}'. Use: cpu | ram | disk | disk:D | "
                "battery | temp | process_exit.")
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return "Threshold must be a number."
    try:
        sustained = max(0.0, float(sustained or 0))
        cooldown = max(0.0, float(cooldown if cooldown is not None else _DEFAULT_COOLDOWN))
    except (TypeError, ValueError):
        return "Sustained and cooldown must be numbers."

    with _lock:
        rid = max([int(r.get("id", 0) or 0) for r in rules] or [0]) + 1
        rules.append({
            "id": rid, "kind": kind, "threshold": threshold,
            "above": bool(above), "message": str(message or ""),
            "sustained": sustained, "cooldown": cooldown,
            "process": process, "last_fired": 0,
        })
        _save()
    comparator = "above" if above else "below"
    extra = f" for {sustained:.0f} second(s)" if sustained else ""
    return (f"Alert added (ID {rid}): warn me when {kind} goes {comparator} "
            f"{threshold}%{extra}.")


def remove_rule(rid: int) -> str:
    with _lock:
        rules = _load()
        before = len(rules)
        rules[:] = [r for r in rules if int(r.get("id", 0) or 0) != int(rid)]
        if len(rules) == before:
            return f"No alert with ID {rid}, sir."
        _save()
    _sustained_since.pop(int(rid), None)
    return f"Alert ID {rid} removed, sir."


def clear_rules() -> str:
    with _lock:
        count = len(_load())
        _rules.clear()
        _save()
    _sustained_since.clear()
    return f"Cleared {count} monitoring alert(s), sir."


def list_rules() -> str:
    rules = _load()
    if not rules:
        return ("No monitoring alerts are set, sir. Say 'alert me when CPU goes "
                "above 90 percent' or use monitor_alerts add.")
    lines = []
    for rule in rules:
        comparator = "above" if rule.get("above") else "below"
        detail = f" — {rule['message']}" if rule.get("message") else ""
        if rule.get("sustained"):
            detail += f" (sustained {rule['sustained']}s)"
        lines.append(f"ID {rule['id']}: {rule['kind']} {comparator} "
                     f"{rule['threshold']}%{detail}")
    if _load_paused():
        lines.append("Monitoring is currently PAUSED.")
    return "Monitoring alerts:\n" + "\n".join(lines)


def check_rules(cooldown: float = _DEFAULT_COOLDOWN) -> list[str]:
    """Return alert texts for rules whose breach has been sustained."""
    if _load_paused():
        return []
    with _lock:
        rules = _load()
        alerts: list[str] = []
        now = time.time()
        changed = False
        for rule in rules:
            rid = int(rule.get("id", 0) or 0)
            value, label = _read_value(rule.get("kind", ""), rule)
            if value is None:
                continue
            above = bool(rule.get("above", True))
            hit = (value > float(rule.get("threshold", 100)) if above else
                   value < float(rule.get("threshold", 0)))
            if not hit:
                _sustained_since.pop(rid, None)
                continue
            required = float(rule.get("sustained", 0) or 0)
            first = _sustained_since.setdefault(rid, now)
            if now - first < required:
                continue
            rule_cooldown = float(rule.get("cooldown", cooldown) or cooldown)
            if now - float(rule.get("last_fired", 0) or 0) < rule_cooldown:
                continue
            rule["last_fired"] = now
            _sustained_since.pop(rid, None)
            message = rule.get("message") or (
                f"{label} is {'above' if above else 'below'} "
                f"{rule['threshold']}% right now ({value:.0f}%).")
            alerts.append(f"[Monitor] {message} (ID {rid})")
            changed = True
        if changed:
            _save()
        return alerts


def monitor_action(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """list | add | remove | check | status | pause | resume | clear"""
    params = parameters or {}
    action = str(params.get("action", "list")).lower().strip()
    kind = str(params.get("kind", "")).lower().strip()
    above = _as_bool(params.get("above", "true"))
    threshold = params.get("threshold")

    if action in ("list", "show"):
        return list_rules()
    if action in ("add", "create", "set"):
        if not kind:
            return "I need a metric, sir: cpu, ram, disk, disk:D, battery, temp or process_exit."
        if threshold is None:
            return "I need a threshold, sir — e.g. 'alert me when cpu is above 90'."
        return add_rule(kind, threshold, above, str(params.get("message", "")).strip(),
                        sustained=params.get("sustained", 0),
                        cooldown=params.get("cooldown", _DEFAULT_COOLDOWN),
                        process=str(params.get("process", "")))
    if action in ("remove", "delete", "cancel"):
        try:
            rid = int(params.get("id", 0))
        except (TypeError, ValueError):
            return "I need the alert ID to remove, sir."
        return remove_rule(rid)
    if action in ("check", "test", "now"):
        alerts = check_rules(cooldown=0)
        return " | ".join(alerts) if alerts else "Nothing is crossing any threshold right now, sir."
    if action in ("pause", "snooze"):
        _save_paused(True)
        return "Monitoring paused, sir."
    if action in ("resume", "unpause"):
        _save_paused(False)
        return "Monitoring resumed, sir."
    if action in ("clear", "clear_all"):
        return clear_rules()
    if action in ("status", "info"):
        rules = _load()
        state = "PAUSED" if _load_paused() else "on"
        if not rules:
            return f"Monitoring is {state} but no alerts are set, sir."
        return f"Monitoring is {state} with {len(rules)} alert rule(s)."
    return f"Unknown action '{action}', sir. Use: list | add | remove | check | status | pause | resume | clear."


# ── Gmail new-mail watcher ───────────────────────────────────────────────────
def _watch_state() -> dict:
    state = dict(_WATCH_DEFAULT)
    try:
        if WATCH_FILE.exists():
            data = json.loads(WATCH_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                state.update(data)
    except Exception:
        pass
    return state


def _save_watch(state: dict) -> None:
    try:
        _atomic_write(WATCH_FILE, state)
    except Exception:
        pass


def _mail_credentials() -> tuple[str, str]:
    cfg_path = get_base_dir() / "config" / "api_keys.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    user = str(os.environ.get("FRIDAY_GMAIL_USER") or cfg.get("gmail_user") or "").strip()
    pwd = str(os.environ.get("FRIDAY_GMAIL_APP_PASSWORD") or
              cfg.get("gmail_app_password") or "").strip()
    return user, pwd


def _oauth_ready() -> bool:
    try:
        import google_oauth
        return bool(google_oauth.has_token())
    except Exception:
        return False


def _decode_header(value: str) -> str:
    from email.header import decode_header as _dh
    parts = _dh(value or "")
    out = []
    for text, encoding in parts:
        if isinstance(text, bytes):
            try:
                text = text.decode(encoding or "utf-8", errors="replace")
            except Exception:
                text = text.decode("utf-8", errors="replace")
        out.append(str(text))
    return "".join(out)


def _passes_filters(state: dict, sender: str, subject: str, is_unread: bool = True) -> bool:
    from_sender = str(state.get("sender", "")).strip().lower()
    subject_filter = str(state.get("subject", "")).strip().lower()
    if from_sender and from_sender not in sender.lower():
        return False
    if subject_filter and subject_filter not in subject.lower():
        return False
    if state.get("unread_only") and not is_unread:
        return False
    return True


def _poll_api(state: dict, seed_only: bool = False) -> tuple[int, list[str]]:
    """Poll Gmail API. Returns (max_uid, alerts). Offline tests mock this."""
    from actions.gmail import _gmail_service, _headers_map, _payload_text
    svc = _gmail_service()
    if svc is None:
        return int(state.get("last_uid", 0) or 0), []
    query = "in:inbox"
    if state.get("unread_only"):
        query += " is:unread"
    result = svc.users().messages().list(userId="me", q=query, maxResults=20).execute()
    messages = result.get("messages", [])
    try:
        max_uid = max([int(m.get("id", 0)) for m in messages] or [0])
    except (TypeError, ValueError):
        max_uid = 0
    if seed_only or not messages:
        return max_uid, []
    last = int(state.get("last_uid", 0) or 0)
    alerts = []
    for item in messages:
        try:
            uid = int(item.get("id", 0))
        except (TypeError, ValueError):
            continue
        if uid <= last:
            continue
        message = svc.users().messages().get(
            userId="me", id=item["id"], format="full").execute()
        headers = _headers_map(message)
        plain, rich = _payload_text(message.get("payload", {}))
        if not _passes_filters(state, headers.get("from", ""),
                               headers.get("subject", ""), "UNREAD" in
                               (message.get("labelIds") or [])):
            continue
        name = headers.get("from", "?").split("<")[0].strip().rstrip(",") or headers.get("from", "?")
        alerts.append(f"[Email] New mail from {name}: {headers.get('subject', '(no subject)')}")
        _ = plain or rich  # body fetched for completeness; alerts stay brief
    return max_uid, alerts[:5]


def _poll_imap(state: dict, seed_only: bool = False) -> tuple[int, list[str]]:
    """Poll IMAP UIDs newer than the watermark. Offline tests mock this."""
    import imaplib
    import email as email_mod
    user, pwd = _mail_credentials()
    if not (user and pwd):
        return int(state.get("last_uid", 0) or 0), []
    criteria = "UNSEEN" if state.get("unread_only") else "ALL"
    with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=20) as conn:
        conn.login(user, pwd)
        conn.select("INBOX")
        typ, data = conn.uid("search", None, criteria)
        if typ != "OK" or not data or not data[0]:
            return int(state.get("last_uid", 0) or 0), []
        uids = [int(uid) for uid in data[0].split()]
        if not uids:
            return int(state.get("last_uid", 0) or 0), []
        max_uid = max(uids)
        last = int(state.get("last_uid", 0) or 0)
        if seed_only:
            return max_uid, []
        alerts = []
        for uid in [u for u in uids if u > last][-5:]:
            typ2, msg_data = conn.uid(
                "fetch", str(uid).encode(),
                "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            if typ2 != "OK" or not msg_data:
                continue
            message = email_mod.message_from_bytes(msg_data[0][1])
            sender = _decode_header(message.get("From", ""))
            subject = _decode_header(message.get("Subject", "(no subject)"))
            if not _passes_filters(state, sender, subject):
                continue
            name = sender.split("<")[0].strip().rstrip(",") or sender
            alerts.append(f"[Email] New mail from {name}: {subject}")
        return max_uid, alerts


def email_watch_action(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """enable | disable | status | pause | resume — OAuth or IMAP watcher."""
    params = parameters or {}
    action = str(params.get("action", "status")).lower().strip()
    user, pwd = _mail_credentials()
    if not (user and pwd) and not _oauth_ready():
        return ("Email watching needs Gmail configured, sir: authorize Google "
                "OAuth or save 'gmail_user' and 'gmail_app_password' in "
                "config/api_keys.json.")

    state = _watch_state()
    for key in ("sender", "subject"):
        if key in params:
            state[key] = str(params[key] or "").strip()
    if "unread_only" in params:
        state["unread_only"] = _as_bool(params["unread_only"])

    if action in ("enable", "on", "start"):
        state["enabled"] = True
        if not state.get("seeded") or not state.get("last_uid"):
            # First run: seed the watermark so old mail never fires an alert.
            try:
                state["last_uid"], _ = (_poll_api(state, seed_only=True) if _oauth_ready()
                                        else _poll_imap(state, seed_only=True))
                state["seeded"] = True
            except Exception:
                pass
        _save_watch(state)
        return ("Email watching enabled, sir. I'll alert you when new mail "
                "arrives in your inbox.")

    if action in ("disable", "off", "stop"):
        state["enabled"] = False
        _save_watch(state)
        return "Email watching disabled, sir."

    if action in ("pause", "snooze"):
        state["enabled"] = False
        _save_watch(state)
        return "Email watching paused, sir."

    if action in ("resume", "unpause"):
        state["enabled"] = True
        _save_watch(state)
        return "Email watching resumed, sir."

    if action in ("status", "info"):
        filters = []
        if state.get("sender"):
            filters.append(f"sender={state['sender']}")
        if state.get("subject"):
            filters.append(f"subject={state['subject']}")
        if state.get("unread_only"):
            filters.append("unread only")
        return ("Email watching is " + ("ON" if state.get("enabled") else "OFF") +
                f" (last seen UID {state.get('last_uid', 0)}). " +
                ("Filters: " + ", ".join(filters) + "." if filters else ""))

    return f"Unknown email_watch action '{action}', sir. Use enable | disable | status."


def check_email_watch() -> list[str]:
    """Poll for new mail and return alert texts (watermark advanced safely)."""
    state = _watch_state()
    if not state.get("enabled"):
        return []
    try:
        if _oauth_ready():
            max_uid, alerts = _poll_api(state)
        else:
            max_uid, alerts = _poll_imap(state)
        if max_uid > int(state.get("last_uid", 0) or 0):
            state["last_uid"] = max_uid
            state["seeded"] = True
            _save_watch(state)
        return alerts
    except Exception:
        return []
