# actions/send_message.py
# Friday — focus-verified, Unicode-safe messaging.
#
# Message text and contact names are pasted through the clipboard (never typed
# keystroke-by-keystroke), and no keystroke is sent until the target app window
# has been found, activated and verified.

from __future__ import annotations

import json
import os
import re
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

_PG = None
_POLL_S = 0.1


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


MEMORY_PATH = get_base_dir() / "memory" / "long_term.json"


def _get_pyautogui():
    """Lazily import pyautogui (unavailable on headless Linux)."""
    global _PG
    if _PG is None:
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.08
        _PG = pyautogui
    return _PG


def _clipboard_paste() -> str:
    try:
        import pyperclip
        return str(pyperclip.paste() or "")
    except Exception:
        return ""


def _clipboard_copy(text: str) -> bool:
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        return False


def _paste_text(text: str) -> bool:
    """Copy -> Ctrl+V -> restore the original clipboard in all cases."""
    original = _clipboard_paste()
    try:
        if not _clipboard_copy(text):
            return False
        pg = _get_pyautogui()
        if os.name == "darwin":
            pg.hotkey("command", "v")
        else:
            pg.hotkey("ctrl", "v")
        time.sleep(0.15)
        return True
    except Exception as e:
        print(f"[SendMessage] Clipboard paste failed: {e}")
        return False
    finally:
        _clipboard_copy(original)


def _get_windows() -> list:
    try:
        import pygetwindow as gw
        return list(gw.getAllWindows())
    except Exception:
        return []


def _window_title(window) -> str:
    return str(getattr(window, "title", "") or "")


def _matches(title: str, keywords) -> bool:
    low = title.lower()
    return any(str(word).lower() in low for word in (keywords or []) if word)


def _focus_window(window) -> bool:
    try:
        if getattr(window, "isMinimized", False):
            window.restore()
        window.activate()
        return True
    except Exception:
        return False


def _foreground_title() -> str:
    try:
        import pygetwindow as gw
        return str(gw.getActiveWindowTitle() or "")
    except Exception:
        return ""


def _wait_for_window(keywords, timeout: float = 8.0):
    """Poll for a visible matching window, activate it and verify foreground."""
    deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
    while time.monotonic() <= deadline:
        for window in _get_windows():
            title = _window_title(window)
            if not title or not _matches(title, keywords):
                continue
            if getattr(window, "visible", True) is False:
                continue
            if not _focus_window(window):
                continue
            foreground = _foreground_title()
            if not foreground or _matches(foreground, keywords):
                return window
        time.sleep(_POLL_S)
    return None


def _open_app(app_name: str) -> bool:
    """Launch via actions.open_app — no Windows-key typing."""
    try:
        from actions.open_app import open_app as _launch
        result = _launch({"app_name": app_name})
        return "successfully" in str(result).lower()
    except Exception as e:
        print(f"[SendMessage] Could not open {app_name}: {e}")
        return False


def _launch_url(url: str) -> bool:
    try:
        return bool(webbrowser.open(url))
    except Exception as e:
        print(f"[SendMessage] Could not open link: {e}")
        return False


def _press_enter() -> None:
    _get_pyautogui().press("enter")


def _search_contact(contact: str, platform: str, keywords) -> bool:
    """Paste a contact into the app's search box; never type Unicode."""
    try:
        pg = _get_pyautogui()
        pg.hotkey("ctrl", "f")
        time.sleep(0.3)
        pg.hotkey("ctrl", "a")
        if not _paste_text(contact):
            return False
        time.sleep(0.6)
        pg.press("enter")
        time.sleep(0.5)
        return True
    except Exception as e:
        print(f"[SendMessage] Contact search failed: {e}")
        return False


def _type_and_send(message: str, send: bool = True) -> bool:
    """Paste the message; press Enter only when the user authorised sending."""
    try:
        if not _paste_text(message):
            return False
        time.sleep(0.2)
        if send:
            _press_enter()
            time.sleep(0.3)
        return True
    except Exception as e:
        print(f"[SendMessage] Message entry failed: {e}")
        return False


def _phone_digits(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if 7 <= len(digits) <= 15 else ""


def _looks_like_phone(receiver: str) -> bool:
    text = str(receiver or "").strip()
    return bool(re.fullmatch(r"\+?[\d\s\-().]{7,20}", text)) and bool(_phone_digits(text))


def _resolve_phone(receiver: str) -> str:
    """Resolve a receiver name to a phone number from long-term memory."""
    if _looks_like_phone(receiver):
        return _phone_digits(receiver)
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""
    needle = str(receiver or "").strip().lower()
    relationships = data.get("relationships", {}) if isinstance(data, dict) else {}
    for key, entry in (relationships or {}).items():
        value = entry.get("value") if isinstance(entry, dict) else entry
        text = f"{key} {value}"
        if needle and needle in text.lower():
            phone = _phone_digits(value)
            if phone:
                return phone
    return ""


def _send_whatsapp(receiver: str, message: str, send: bool = True,
                   dry_run: bool = False, web: bool = False) -> str:
    """Send via a phone deep link when possible, otherwise the desktop app."""
    phone = _resolve_phone(receiver)
    keywords = ["whatsapp"] if not web else ["whatsapp", "chrome", "edge", "browser"]
    if phone:
        if web:
            url = f"https://wa.me/{phone}?text={quote(message)}"
        else:
            url = f"whatsapp://send?phone={phone}&text={quote(message)}"
        if not _launch_url(url):
            return "Could not open the WhatsApp chat link."
        if not _wait_for_window(keywords):
            return "WhatsApp did not come to the front, so nothing was sent, sir."
        if send and not dry_run:
            _press_enter()
            return f"Message sent to {receiver} via WhatsApp."
        return f"Message prepared for {receiver} in WhatsApp — review and send it yourself, sir."

    if not _open_app("WhatsApp"):
        return "Could not open WhatsApp."
    if not _wait_for_window(["whatsapp"]):
        return "WhatsApp could not be focused, so nothing was sent, sir."
    if not _search_contact(receiver, "whatsapp", ["whatsapp"]):
        return f"Could not find {receiver} in WhatsApp, sir."
    if not _type_and_send(message, send and not dry_run):
        return "Could not enter the WhatsApp message, sir."
    if not (send and not dry_run):
        return f"Message prepared for {receiver} in WhatsApp — review and send it yourself, sir."
    return f"Message sent to {receiver} via WhatsApp."


def _send_instagram(receiver: str, message: str, send: bool = True,
                    dry_run: bool = False, web: bool = True) -> str:
    """Send an Instagram DM through a verified browser/desktop window."""
    if not _launch_url("https://www.instagram.com/direct/new/"):
        return "Could not open Instagram."
    if not _wait_for_window(["instagram", "chrome", "edge", "browser"]):
        return "Instagram could not be focused, so nothing was sent, sir."
    if not _search_contact(receiver, "instagram", ["instagram", "chrome", "browser"]):
        return f"Could not find {receiver} on Instagram, sir."
    if not _type_and_send(message, send and not dry_run):
        return "Could not enter the Instagram message, sir."
    if not (send and not dry_run):
        return f"Message prepared for {receiver} in Instagram — review it yourself, sir."
    return f"Message sent to {receiver} via Instagram."


def _send_telegram(receiver: str, message: str, send: bool = True,
                   dry_run: bool = False, web: bool = False) -> str:
    """Send via tg:// for @usernames, otherwise the verified desktop app."""
    if str(receiver or "").startswith("@"):
        domain = re.sub(r"[^A-Za-z0-9_]", "", str(receiver)[1:])
        if domain:
            url = f"tg://resolve?domain={domain}&text={quote(message)}"
            if not _launch_url(url):
                return "Could not open the Telegram chat link."
            if not _wait_for_window(["telegram", "chrome", "edge", "browser"]):
                return "Telegram did not come to the front, so nothing was sent, sir."
            if send and not dry_run:
                _press_enter()
                return f"Message sent to {receiver} via Telegram."
            return f"Message prepared for {receiver} in Telegram — review it yourself, sir."

    if not _open_app("Telegram"):
        return "Could not open Telegram."
    if not _wait_for_window(["telegram"]):
        return "Telegram could not be focused, so nothing was sent, sir."
    if not _search_contact(receiver, "telegram", ["telegram"]):
        return f"Could not find {receiver} in Telegram, sir."
    if not _type_and_send(message, send and not dry_run):
        return "Could not enter the Telegram message, sir."
    if not (send and not dry_run):
        return f"Message prepared for {receiver} in Telegram — review it yourself, sir."
    return f"Message sent to {receiver} via Telegram."


def _send_generic(platform: str, receiver: str, message: str, send: bool = True,
                  dry_run: bool = False, web: bool = False) -> str:
    """Open a generic messaging app, verify focus, then paste and send."""
    if not _open_app(platform):
        return f"Could not open {platform}."
    if not _wait_for_window([platform]):
        return f"{platform} could not be focused, so nothing was sent, sir."
    if not _search_contact(receiver, platform, [platform]):
        return f"Could not find {receiver} in {platform}, sir."
    if not _type_and_send(message, send and not dry_run):
        return f"Could not enter the {platform} message, sir."
    if not (send and not dry_run):
        return f"Message prepared for {receiver} in {platform} — review it yourself, sir."
    return f"Message sent to {receiver} via {platform}."


def send_message(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Send (or draft) a message after verifying the target window."""
    params = parameters if isinstance(parameters, dict) else {}
    receiver = str(params.get("receiver") or params.get("recipient") or "").strip()
    message_text = str(params.get("message_text") or params.get("message") or "").strip()
    platform = str(params.get("platform") or "whatsapp").strip().lower()
    send = params.get("send", True)
    if isinstance(send, str):
        send = send.strip().lower() not in ("false", "0", "no", "off")
    dry_run = params.get("dry_run", False)
    if isinstance(dry_run, str):
        dry_run = dry_run.strip().lower() in ("true", "1", "yes", "on")
    web = params.get("web", False)
    if isinstance(web, str):
        web = web.strip().lower() in ("true", "1", "yes", "on")
    do_send = bool(send) and not dry_run

    if not receiver:
        return "Please specify who to send the message to, sir."
    if not message_text:
        return "Please specify what message to send, sir."

    print(f"[SendMessage] {platform} -> {receiver}: {message_text[:40]}")
    if player:
        try:
            player.write_log(f"[msg] Preparing message for {receiver} via {platform}")
        except Exception:
            pass

    if "whatsapp" in platform or platform in ("wp", "wapp"):
        result = _send_whatsapp(receiver, message_text, do_send, dry_run, web)
    elif "instagram" in platform or platform in ("ig", "insta"):
        result = _send_instagram(receiver, message_text, do_send, dry_run, web)
    elif "telegram" in platform or platform in ("tg",):
        result = _send_telegram(receiver, message_text, do_send, dry_run, web)
    else:
        result = _send_generic(platform, receiver, message_text, do_send, dry_run, web)

    print(f"[SendMessage] [ok] {result}")
    if player:
        try:
            player.write_log(f"[msg] {result}")
        except Exception:
            pass
    return result
