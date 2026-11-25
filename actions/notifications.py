# actions/notifications.py
# Friday — notifications with history, deduplication and native fallbacks.

import hashlib
import json
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path

HISTORY_FILE = Path(__file__).resolve().parent.parent / "data" / "notification_history.json"

_DEDUP_WINDOW = 30.0  # seconds
_recent: dict[str, float] = {}


def _load_history() -> list[dict]:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _append_history(entry: dict) -> None:
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        items = _load_history()
        items.append(entry)
        if len(items) > 200:
            items = items[-200:]
        fd, tmp = tempfile.mkstemp(prefix="notify-", suffix=".json",
                                   dir=str(HISTORY_FILE.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(items, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, HISTORY_FILE)
    except Exception:
        pass


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _safe_url(value) -> str | None:
    """Only http(s) links are allowed in a toast (no file:/cmd:/javascript:)."""
    url = str(value or "").strip()
    if url.lower().startswith(("http://", "https://")):
        return url
    return None


def _windows_toast(title: str, message: str, params: dict) -> bool:
    try:
        from winotify import Notification
        try:
            from winotify import audio
        except ImportError:
            audio = None
        duration = str(params.get("duration", "short")).lower()
        if duration not in ("short", "long"):
            duration = "short"
        note = Notification(app_id="Friday", title=title, msg=message,
                            duration=duration)
        sound = str(params.get("sound", "default")).lower()
        sound_members = {
            "default": "Default", "none": "Silent",
            "alarm": "Alarm", "reminder": "Reminder",
        }
        if sound != "none":
            member = sound_members.get(sound, "Default")
            sound_value = getattr(audio, member, getattr(audio, "Default", None)) if audio else None
            if sound_value is not None and hasattr(note, "set_audio"):
                note.set_audio(sound_value, loop=False)
        url = _safe_url(params.get("url") or params.get("link"))
        if url:
            note.add_actions([
                ("Open link", url),
                ("Dismiss", ""),
            ]) if hasattr(note, "add_actions") else None
        icon = str(params.get("icon", "")).strip()
        if icon and hasattr(note, "set_icon") and Path(icon).is_file():
            note.set_icon(icon)
        note.show()
        return True
    except Exception as exc:
        print(f"[Notify] winotify unavailable: {exc}")
        return False


def _powershell_toast(title: str, message: str) -> bool:
    try:
        safe_title = title.replace("'", "''")
        safe_msg = message.replace("'", "''")
        script = (
            "[Windows.UI.Notifications.ToastNotificationManager, "
            "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
            "$t = [Windows.UI.Notifications.ToastNotificationManager]"
            "::GetTemplateContent([Windows.UI.Notifications.ToastType]::ToastText02); "
            f"$t.GetElementsByTagName('text')[0].AppendChild("
            f"$t.CreateTextNode('{safe_title}')) | Out-Null; "
            f"$t.GetElementsByTagName('text')[1].AppendChild("
            f"$t.CreateTextNode('{safe_msg}')) | Out-Null; "
            "$n = [Windows.UI.Notifications.ToastNotification]::new($t); "
            "[Windows.UI.Notifications.ToastNotificationManager]"
            "::CreateToastNotifier('Friday').Show($n)"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, timeout=10, check=False)
        return True
    except Exception:
        return False


def _mac_toast(title: str, message: str) -> bool:
    try:
        script = f'display notification "{message}" with title "{title}"'
        subprocess.run(["osascript", "-e", script], capture_output=True,
                       timeout=5, check=False)
        return True
    except Exception:
        return False


def _linux_toast(title: str, message: str, params: dict) -> bool:
    try:
        cmd = ["notify-send", title, message]
        urgency = str(params.get("urgency", "")).strip()
        if urgency in ("low", "normal", "critical"):
            cmd[1:1] = [f"--urgency={urgency}"]
        subprocess.run(cmd, capture_output=True, timeout=5, check=False)
        return True
    except Exception:
        return False


def notify(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """send | history | clear_history — toast notifications with dedup."""
    params = parameters or {}
    action = str(params.get("action", "send")).lower().strip()

    if action in ("history", "list", "recent"):
        items = _load_history()
        if not items:
            return "No notifications have been sent yet, sir."
        lines = [f"Notification history ({len(items)} entries):"]
        for item in items[-10:]:
            lines.append(f"• {item.get('time', '?')} — {item.get('title', 'Friday')}: "
                         f"{item.get('message', '')}")
        return "\n".join(lines)

    if action in ("clear_history", "clear", "reset"):
        try:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.write_text("[]", encoding="utf-8")
        except Exception:
            pass
        _recent.clear()
        return "Notification history cleared, sir."

    if action not in ("send", "show", "toast", "notify"):
        return f"Unknown notification action '{action}', sir. Use send | history | clear_history."

    title = str(params.get("title", "Friday")).strip() or "Friday"
    message = str(params.get("message", params.get("text", ""))).strip()
    if not message:
        return "No notification message provided, sir."

    force = _as_bool(params.get("force", False))
    digest = hashlib.sha256(f"{title}\x00{message}".encode("utf-8")).hexdigest()
    now = time.time()
    if not force and now - _recent.get(digest, 0) < _DEDUP_WINDOW:
        return "Duplicate notification skipped, sir."

    system = platform.system()
    sent = False
    if system == "Windows":
        sent = _windows_toast(title, message, params) or _powershell_toast(title, message)
    elif system == "Darwin":
        sent = _mac_toast(title, message)
    else:
        sent = _linux_toast(title, message, params)

    _recent[digest] = now
    _append_history({
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "title": title, "message": message, "sent": sent,
    })
    if sent:
        return f"Notification sent: {message[:80]}"
    return f"Notification queued (no native engine available): {message[:80]}"
