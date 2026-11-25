"""actions/gmail.py — Gmail via the OAuth API or app-password IMAP/SMTP.

Full parity is provided for both transports: read, search, read_message, send,
draft, threaded reply, mark read/unread, unread_count, trash and delete.
"""

import base64
import email
import html as html_module
import imaplib
import json
import os
import re
import smtplib
import sys
from email import encoders
from email.header import decode_header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = get_base_dir() / "config" / "api_keys.json"
_API_ACTIONS = {
    "read", "search", "read_message", "send", "compose", "draft", "reply",
    "mark_read", "mark_unread", "unread_count", "trash", "delete",
}


def _load_config() -> dict:
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    # Environment overrides are useful on servers/CI and never get written back.
    for key in ("gmail_user", "gmail_app_password"):
        env = os.environ.get("FRIDAY_" + key.upper())
        if env:
            cfg[key] = env
    for key, default in (("smtp_host", SMTP_HOST), ("imap_host", IMAP_HOST)):
        cfg[key] = os.environ.get("FRIDAY_" + key.upper(), cfg.get(key) or default)
    try:
        cfg["smtp_port"] = int(os.environ.get(
            "FRIDAY_SMTP_PORT", cfg.get("smtp_port", SMTP_PORT)))
        cfg["imap_port"] = int(os.environ.get(
            "FRIDAY_IMAP_PORT", cfg.get("imap_port", IMAP_PORT)))
    except (TypeError, ValueError):
        cfg["smtp_port"], cfg["imap_port"] = SMTP_PORT, IMAP_PORT
    return cfg


def _oauth_available() -> bool:
    try:
        import google_oauth
        return bool(google_oauth.has_token())
    except Exception:
        return False


def _creds_error(cfg: dict) -> str | None:
    if _oauth_available():
        return None
    user = (cfg.get("gmail_user") or "").strip()
    pwd = (cfg.get("gmail_app_password") or "").strip()
    if not user:
        return ("Gmail is not configured yet, sir. I need your Gmail address "
                "saved as 'gmail_user' in config/api_keys.json.")
    if not pwd:
        return ("Your Gmail app password is missing, sir. Save it as "
                "'gmail_app_password' in config/api_keys.json, or authorize "
                "Google OAuth with the 'google_auth' tool.")
    return None


def _decode(value: str) -> str:
    if not value:
        return ""
    out = []
    try:
        for text, encoding in decode_header(str(value)):
            if isinstance(text, bytes):
                try:
                    text = text.decode(encoding or "utf-8", errors="replace")
                except Exception:
                    text = text.decode("utf-8", errors="replace")
            out.append(str(text))
    except Exception:
        return str(value)
    return "".join(out)


def _strip_html(body: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body,
                  flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html_module.unescape(text).split())


def _payload_text(payload: dict) -> tuple[str, str]:
    """Return (plain_text, html_text) from a Gmail API payload hierarchy."""
    plain, rich = "", ""
    parts = [payload] if payload.get("mimeType") else payload.get("parts", [])
    for part in parts:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data") or ""
        if data and (mime == "text/plain" or mime.startswith("text/")):
            try:
                decoded = base64.urlsafe_b64decode(data + "==").decode(
                    "utf-8", errors="replace")
            except Exception:
                continue
            if mime == "text/plain":
                plain += decoded
            else:
                rich += decoded
        nested = part.get("parts") or []
        if nested:
            p, r = _payload_text(part)
            plain += p
            rich += r
    return plain, rich


def _body_snippet(msg) -> str:
    """IMAP message → readable snippet (HTML decoded when necessary)."""
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
            except Exception:
                continue
            return " ".join(text.split())[:400]
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            try:
                payload = part.get_payload(decode=True)
                text = payload.decode(part.get_content_charset() or "utf-8",
                                      errors="replace")
                return _strip_html(text)[:400]
            except Exception:
                continue
    return "(no text body)"


def _headers_map(message: dict) -> dict:
    headers = message.get("payload", {}).get("headers", [])
    return {h.get("name", "").lower(): h.get("value", "") for h in headers}


def _api_message(params: dict, *, reply_to: dict = None) -> bytes:
    """Build a RFC-822 message (with optional attachments) for the Gmail API."""
    to = str(params.get("to", "")).strip()
    subject = str(params.get("subject", "")).strip() or "From Friday"
    body = str(params.get("body", params.get("message", ""))).strip()
    html_body = params.get("html") or params.get("html_body")
    cc = str(params.get("cc", "")).strip()
    bcc = str(params.get("bcc", "")).strip()

    if reply_to:
        headers = _headers_map(reply_to)
        original_subject = headers.get("subject", subject)
        if not original_subject.lower().startswith("re:"):
            subject = "Re: " + original_subject
        if not to:
            to = headers.get("from", "")
    if not to:
        raise ValueError("I need the recipient's email address, sir.")
    if not body and not html_body:
        raise ValueError("I need the message body, sir.")

    msg = MIMEMultipart("mixed" if params.get("attachments") else "alternative")
    msg["To"] = to
    msg["Subject"] = subject
    msg["From"] = str(params.get("from", "me"))
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    if reply_to:
        headers = _headers_map(reply_to)
        message_id = headers.get("message-id", "")
        if message_id:
            msg["In-Reply-To"] = message_id
            msg["References"] = message_id
    if html_body:
        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(body or _strip_html(str(html_body)), "plain", "utf-8"))
        alternative.attach(MIMEText(str(html_body), "html", "utf-8"))
        msg.attach(alternative)
    else:
        msg.attach(MIMEText(body, "plain", "utf-8"))

    for attachment in params.get("attachments") or []:
        path = Path(str(attachment)).expanduser()
        if not path.is_file():
            raise ValueError(f"Attachment not found: {path}")
        if path.stat().st_size > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"Attachment too large (25 MB limit): {path.name}")
        part = MIMEBase("application", "octet-stream")
        part.set_payload(path.read_bytes())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=path.name)
        msg.attach(part)
    return msg.as_bytes()


# ── Gmail API transport ────────────────────────────────────────────────────
def _gmail_service():
    from googleapiclient.discovery import build
    import google_oauth
    creds = google_oauth.get_credentials()
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds)


def _send_gmail_api(creds, to: str, subject: str, body: str) -> str:
    """Back-compatible helper; full sends go through _api_send."""
    return _api_send({"to": to, "subject": subject, "body": body})


def _api_send(params: dict, reply_to: dict = None, draft: bool = False) -> str:
    svc = _gmail_service()
    if svc is None:
        return "Google authentication expired or missing. Please re-authenticate Google OAuth, sir."
    try:
        raw = base64.urlsafe_b64encode(_api_message(params, reply_to=reply_to)).decode("ascii")
        if draft:
            svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
            return "Draft saved in Gmail, sir."
        body_kwargs = {"raw": raw}
        if reply_to:
            thread_id = reply_to.get("threadId")
            if thread_id:
                body_kwargs["threadId"] = thread_id
        sent = svc.users().messages().send(userId="me", body=body_kwargs).execute()
        return f"Email sent (id {sent.get('id', '?')})."
    except Exception as exc:
        return f"Gmail API error: {exc}"


def _api_list(params: dict, unread_only: bool = False) -> str:
    svc = _gmail_service()
    if svc is None:
        return "Google authentication expired or missing. Please re-authenticate Google OAuth, sir."
    try:
        limit = max(1, min(int(params.get("limit", 5) or 5), 50))
    except (TypeError, ValueError):
        limit = 5
    query = str(params.get("query", "")).strip()
    if unread_only:
        query = (query + " is:unread").strip()
    try:
        result = svc.users().messages().list(
            userId="me", maxResults=limit, q=query,
            labelIds=["INBOX"] if not query else None,
        ).execute()
    except Exception as exc:
        return f"Gmail API error: {exc}"
    messages = result.get("messages", [])
    if unread_only:
        return f"Unread count: {result.get('resultSizeEstimate', len(messages))}."
    if not messages:
        return "No emails found, sir."
    lines = ["Recent emails:"]
    for item in messages:
        try:
            full = svc.users().messages().get(
                userId="me", id=item["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]).execute()
            headers = _headers_map(full)
            lines.append(f"• {headers.get('from', '?')} — {headers.get('subject', '(no subject)')}"
                         f" (id: {item['id']})")
        except Exception:
            continue
    return "\n".join(lines)


def _api_read_message(params: dict) -> str:
    svc = _gmail_service()
    if svc is None:
        return "Google authentication expired or missing. Please re-authenticate Google OAuth, sir."
    message_id = str(params.get("message_id", params.get("id", ""))).strip()
    if not message_id:
        return "I need a message_id, sir. Use 'gmail search' to find it."
    try:
        full = svc.users().messages().get(userId="me", id=message_id,
                                          format="full").execute()
        headers = _headers_map(full)
        plain, rich = _payload_text(full.get("payload", {}))
        text = plain.strip() or _strip_html(rich)
        result = (f"From: {headers.get('from', '?')}\n"
                  f"To: {headers.get('to', '?')}\n"
                  f"Subject: {headers.get('subject', '(no subject)')}\n\n"
                  + text[:6000])
        if params.get("mark_read", True):
            svc.users().messages().modify(
                userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}).execute()
        return result
    except Exception as exc:
        return f"Gmail API error: {exc}"


def _api_modify(params: dict, unread: bool) -> str:
    svc = _gmail_service()
    if svc is None:
        return "Google authentication expired or missing. Please re-authenticate Google OAuth, sir."
    message_id = str(params.get("message_id", params.get("id", ""))).strip()
    if not message_id:
        return "I need a message_id, sir."
    body = {"addLabelIds": ["UNREAD"]} if unread else {"removeLabelIds": ["UNREAD"]}
    try:
        svc.users().messages().modify(userId="me", id=message_id, body=body).execute()
        return f"Message marked as {'unread' if unread else 'read'}, sir."
    except Exception as exc:
        return f"Gmail API error: {exc}"


def _api_trash(params: dict, permanent: bool = False) -> str:
    svc = _gmail_service()
    if svc is None:
        return "Google authentication expired or missing. Please re-authenticate Google OAuth, sir."
    message_id = str(params.get("message_id", params.get("id", ""))).strip()
    if not message_id:
        return "I need a message_id, sir."
    try:
        if permanent:
            svc.users().messages().delete(userId="me", id=message_id).execute()
            return "Message permanently deleted, sir."
        svc.users().messages().trash(userId="me", id=message_id).execute()
        return "Message moved to Gmail trash, sir."
    except Exception as exc:
        return f"Gmail API error: {exc}"


def _read_gmail_api(creds, limit: int) -> str:
    """Back-compatible helper for the old 'read' code path."""
    return _api_list({"limit": limit})


# ── IMAP/SMTP transport ────────────────────────────────────────────────────
def _smtp_message(cfg: dict, params: dict) -> MIMEMultipart:
    to = str(params.get("to", "")).strip()
    if not to:
        raise ValueError("I need the recipient's email address, sir.")
    body = str(params.get("body", params.get("message", ""))).strip()
    html_body = params.get("html") or params.get("html_body")
    if not body and not html_body:
        raise ValueError("I need the message body, sir.")
    msg = MIMEMultipart("mixed" if params.get("attachments") else "alternative")
    msg["From"] = (cfg.get("gmail_user") or "").strip()
    msg["To"] = to
    msg["Subject"] = str(params.get("subject", "")).strip() or "From Friday"
    if params.get("cc"):
        msg["Cc"] = str(params["cc"]).strip()
    if params.get("bcc"):
        msg["Bcc"] = str(params["bcc"]).strip()
    if html_body:
        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(body or _strip_html(str(html_body)), "plain", "utf-8"))
        alternative.attach(MIMEText(str(html_body), "html", "utf-8"))
        msg.attach(alternative)
    else:
        msg.attach(MIMEText(body, "plain", "utf-8"))
    for attachment in params.get("attachments") or []:
        path = Path(str(attachment)).expanduser()
        if not path.is_file():
            raise ValueError(f"Attachment not found: {path}")
        if path.stat().st_size > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"Attachment too large (25 MB limit): {path.name}")
        part = MIMEBase("application", "octet-stream")
        part.set_payload(path.read_bytes())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=path.name)
        msg.attach(part)
    return msg


def _send_gmail(cfg: dict, to: str, subject: str, body: str) -> str:
    return _imap_send(cfg, {"to": to, "subject": subject, "body": body})


def _imap_send(cfg: dict, params: dict) -> str:
    msg = _smtp_message(cfg, params)
    recipients = [addr.strip() for addr in re.split(r"[,;]", msg["To"] or "") if addr.strip()]
    recipients += [addr.strip() for addr in re.split(r"[,;]", msg.get("Cc") or "") if addr.strip()]
    recipients += [addr.strip() for addr in re.split(r"[,;]", msg.get("Bcc") or "") if addr.strip()]
    with smtplib.SMTP_SSL(cfg.get("smtp_host", SMTP_HOST),
                          int(cfg.get("smtp_port", SMTP_PORT)), timeout=30) as server:
        server.login(cfg["gmail_user"], cfg["gmail_app_password"])
        server.sendmail(cfg["gmail_user"], recipients, msg.as_string())
    return f"Email sent to {msg['To']} with subject '{msg['Subject']}'."


def _imap_connection(cfg: dict):
    conn = imaplib.IMAP4_SSL(cfg.get("imap_host", IMAP_HOST),
                            int(cfg.get("imap_port", IMAP_PORT)), timeout=30)
    conn.login(cfg["gmail_user"], cfg["gmail_app_password"])
    conn.select("INBOX")
    return conn


def _imap_search(conn, query: str) -> list[bytes]:
    criteria = "ALL"
    if query:
        lower = query.lower()
        if "unread" in lower or "unseen" in lower:
            criteria = "UNSEEN"
        elif "from:" in lower:
            criteria = f'FROM "{query.split("from:", 1)[1].strip().split()[0]}"'
        elif "subject:" in lower:
            criteria = f'SUBJECT "{query.split("subject:", 1)[1].strip()}"'
        elif lower.strip() not in ("all", ""):
            criteria = f'TEXT "{query.replace(chr(34), "")}"'
    typ, data = conn.search(None, criteria)
    return data[0].split() if typ == "OK" and data and data[0] else []


def _read_gmail(cfg: dict, limit: int) -> str:
    return _imap_list(cfg, {"limit": limit})


def _imap_list(cfg: dict, params: dict, unread_only: bool = False) -> str:
    try:
        limit = max(1, min(int(params.get("limit", 5) or 5), 50))
    except (TypeError, ValueError):
        limit = 5
    query = str(params.get("query", "")).strip()
    if unread_only:
        query = (query + " unread").strip()
    with _imap_connection(cfg) as conn:
        ids = _imap_search(conn, query)
        if unread_only:
            return f"Unread count: {len(ids)}."
        if not ids:
            return "No emails found, sir."
        lines = ["Recent emails:"]
        for num in reversed(ids[-limit:]):
            typ, msg_data = conn.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data:
                continue
            message = email.message_from_bytes(msg_data[0][1])
            lines.append(f"• {_decode(message.get('From', '?'))} — "
                         f"{_decode(message.get('Subject', '(no subject)'))}\n"
                         f"   {_body_snippet(message)}")
        return "\n".join(lines)


def _imap_read_message(cfg: dict, params: dict) -> str:
    message_id = str(params.get("message_id", params.get("id", ""))).strip()
    if not message_id:
        return "I need a message_id, sir. Use 'gmail search' to find it."
    with _imap_connection(cfg) as conn:
        typ, data = conn.fetch(message_id.encode(), "(RFC822)")
        if typ != "OK" or not data:
            return f"Message {message_id} not found, sir."
        message = email.message_from_bytes(data[0][1])
        attachments = [part.get_filename() for part in message.walk()
                       if part.get_filename()]
        result = (f"From: {_decode(message.get('From', '?'))}\n"
                  f"Subject: {_decode(message.get('Subject', '(no subject)'))}\n\n"
                  + _body_snippet(message))
        if attachments:
            result += "\nAttachments: " + ", ".join(filter(None, attachments))
        return result


def _imap_modify(cfg: dict, message_ids: list[str], unread: bool) -> str:
    if not message_ids:
        return "I need a message_id, sir."
    with _imap_connection(cfg) as conn:
        for message_id in message_ids:
            conn.store(message_id.encode(), "+FLAGS" if unread else "-FLAGS",
                       r"(\Seen)")
    return f"Message(s) marked as {'unread' if unread else 'read'}, sir."


def _imap_delete(cfg: dict, message_ids: list[str], permanent: bool = False) -> str:
    if not message_ids:
        return "I need a message_id, sir."
    with _imap_connection(cfg) as conn:
        for message_id in message_ids:
            conn.store(message_id.encode(), "+FLAGS", r"(\Deleted)")
        conn.expunge()
    return "Message(s) moved to trash, sir." if not permanent else "Message(s) deleted, sir."


def _message_ids(params: dict) -> list[str]:
    value = params.get("message_id", params.get("id", ""))
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def gmail_action(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Gmail operations via OAuth (preferred) or app-password IMAP/SMTP."""
    params = parameters or {}
    action = str(params.get("action", "send")).lower().strip()
    aliases = {
        "check": "read", "inbox": "read", "latest": "read", "list": "read",
        "compose": "send", "find": "search", "open": "read_message",
        "mark_as_read": "mark_read", "mark_as_unread": "mark_unread",
    }
    action = aliases.get(action, action)
    if action not in _API_ACTIONS:
        return f"Unknown gmail action: '{action}'."

    cfg = _load_config()
    err = _creds_error(cfg)
    if err:
        return err

    oauth = _oauth_available()
    try:
        if action == "send":
            return (_api_send(params) if oauth else _imap_send(cfg, params))
        if action == "draft":
            return (_api_send(params, draft=True) if oauth else
                    "Drafts require Google OAuth, sir. Authorize with 'google_auth'.")
        if action == "reply":
            message_id = str(params.get("message_id", params.get("id", ""))).strip()
            if not message_id:
                return "I need the message_id to reply to, sir."
            if not oauth:
                return "Threaded replies require Google OAuth, sir."
            original = _gmail_service().users().messages().get(
                userId="me", id=message_id, format="metadata",
                metadataHeaders=["From", "Subject", "Message-ID"]).execute()
            return _api_send(params, reply_to=original)
        if action in ("read", "search"):
            return (_api_list(params) if oauth else _imap_list(cfg, params))
        if action == "read_message":
            return (_api_read_message(params) if oauth else
                    _imap_read_message(cfg, params))
        if action == "unread_count":
            return (_api_list(params, unread_only=True) if oauth else
                    _imap_list(cfg, params, unread_only=True))
        if action in ("mark_read", "mark_unread"):
            unread = action == "mark_unread"
            if oauth:
                results = [_api_modify({"message_id": mid}, unread)
                           for mid in _message_ids(params)]
                return " ".join(results)
            return _imap_modify(cfg, _message_ids(params), unread)
        if action in ("trash", "delete"):
            if oauth:
                results = [_api_trash({"message_id": mid}, permanent=action == "delete")
                           for mid in _message_ids(params)]
                return " ".join(results)
            return _imap_delete(cfg, _message_ids(params), permanent=action == "delete")
    except smtplib.SMTPAuthenticationError:
        return ("Gmail login failed, sir. Make sure 'gmail_app_password' is a real "
                "16-character app password (not your normal password) and that "
                "2-Step Verification is enabled.")
    except Exception as exc:
        return f"Gmail operation failed: {exc}"
    return f"Unknown Gmail action: {action}."
