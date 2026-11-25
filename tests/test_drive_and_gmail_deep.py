"""Offline deep tests for drive.py and gmail.py (no network calls)."""

import base64
import email
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from actions import drive, gmail
import safety


# ── drive ───────────────────────────────────────────────────────────────────
class _Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _DriveFiles:
    def __init__(self):
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return _Request({"files": [
            {"id": "1", "name": "Notes.txt", "mimeType": "text/plain", "size": "120"},
        ]})

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        mime = "text/plain" if kwargs.get("fileId") == "1" else drive.GOOGLE_DOC
        return _Request({"name": "Notes", "mimeType": mime})

    def get_media(self, **kwargs):
        self.calls.append(("get_media", kwargs))
        return _Request(b"x" * 5000)

    def export(self, **kwargs):
        self.calls.append(("export", kwargs))
        return _Request(b"Google doc body")

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return _Request({"id": "new-id", "name": kwargs["body"]["name"]})

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return _Request({})


def test_drive_queries_always_exclude_trashed():
    query = drive._not_trashed("name contains 'Bob\\'s'")
    assert query.startswith("trashed = false") and "Bob\\'s" in query
    assert drive._escape_drive_q("Bob's \\ File") == "Bob\\'s \\\\ File"


def test_drive_list_uses_not_trashed_and_reports_files(monkeypatch):
    files = _DriveFiles()
    monkeypatch.setattr(drive, "_service", lambda: SimpleNamespace(files=lambda: files))
    result = drive.drive_action({"action": "list", "query": "Note", "max_results": "5"})
    assert "Notes.txt" in result
    q = files.calls[0][1]["q"]
    assert "trashed = false" in q and "name contains" in q


def test_drive_read_caps_text_to_4000_chars(monkeypatch):
    files = _DriveFiles()
    monkeypatch.setattr(drive, "_service", lambda: SimpleNamespace(files=lambda: files))
    result = drive.drive_action({"action": "read", "file_id": "1"})
    assert len(result.split(":\n", 1)[1]) <= drive.MAX_READ_CHARS


def test_drive_google_doc_export_and_folder_upload(monkeypatch):
    files = _DriveFiles()
    monkeypatch.setattr(drive, "_service", lambda: SimpleNamespace(files=lambda: files))
    monkeypatch.setitem(__import__("sys").modules, "googleapiclient", SimpleNamespace())
    monkeypatch.setitem(
        __import__("sys").modules, "googleapiclient.http",
        SimpleNamespace(MediaIoBaseUpload=lambda *a, **k: "media",
                        MediaFileUpload=lambda *a, **k: "media",
                        MediaIoBaseDownload=object))
    assert "Google doc body" in drive.drive_action({"action": "read", "file_id": "doc"})
    result = drive.drive_action({"action": "upload", "name": "new.txt",
                                 "content": "hello", "folder_id": "folder-1"})
    assert "new-id" in result
    create = [call for call in files.calls if call[0] == "create"][0][1]
    assert create["body"]["parents"] == ["folder-1"]


def test_drive_download_path_cannot_escape_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    target = drive._safe_download_path("../../secrets.txt")
    assert target.parent == (tmp_path / "Downloads" / "Friday").resolve()
    assert "secrets.txt" in target.name and ".." not in target.name


def test_drive_create_folder_and_trash(monkeypatch):
    files = _DriveFiles()
    monkeypatch.setattr(drive, "_service", lambda: SimpleNamespace(files=lambda: files))
    assert "Folder created" in drive.drive_action({"action": "create_folder", "name": "Reports"})
    assert "trashed" in drive.drive_action({"action": "trash", "file_id": "1"}).lower()
    update = [call for call in files.calls if call[0] == "update"][0][1]
    assert update["body"] == {"trashed": True}


# ── gmail ───────────────────────────────────────────────────────────────────
def test_gmail_unknown_action_does_not_need_credentials(monkeypatch):
    monkeypatch.setattr(gmail, "_oauth_available", lambda: True)
    result = gmail.gmail_action({"action": "bogus_action"})
    assert "Unknown gmail action" in result


def test_gmail_risk_classifications_are_never_weakened():
    assert safety.classify("gmail", {"action": "reply"}) is safety.Risk.HIGH
    assert safety.classify("gmail", {"action": "trash"}) is safety.Risk.HIGH
    assert safety.classify("gmail", {"action": "send"}) is safety.Risk.HIGH
    assert safety.classify("gmail", {"action": "read"}) is safety.Risk.LOW
    assert safety.classify("drive", {"action": "trash"}) is safety.Risk.HIGH
    assert safety.classify("file_controller", {"action": "delete"}) is safety.Risk.HIGH


class _GmailMessages:
    def __init__(self):
        self.sent = []
        self.modified = []
        self.trashed = []
        self.get_result = {"id": "orig", "threadId": "thread-9",
                           "payload": {"headers": [
                               {"name": "Subject", "value": "Original topic"},
                               {"name": "From", "value": "boss@corp.com"},
                               {"name": "Message-ID", "value": "<abc@corp>"},
                           ]}}

    def send(self, **kwargs):
        self.sent.append(kwargs)
        return _Request({"id": "sent-id"})

    def get(self, **kwargs):
        return _Request(self.get_result)

    def modify(self, **kwargs):
        self.modified.append(kwargs)
        return _Request({})

    def trash(self, **kwargs):
        self.trashed.append(kwargs)
        return _Request({})

    def delete(self, **kwargs):
        self.trashed.append(kwargs)
        return _Request({})


def _fake_gmail_service(messages):
    drafts = SimpleNamespace(create=lambda **kwargs: _Request({"id": "draft"}))
    return SimpleNamespace(users=lambda: SimpleNamespace(
        messages=lambda: messages, drafts=lambda: drafts))


def test_gmail_api_send_with_cc_bcc_attachment_and_html(monkeypatch, tmp_path):
    messages = _GmailMessages()
    monkeypatch.setattr(gmail, "_gmail_service", lambda: _fake_gmail_service(messages))
    monkeypatch.setattr(gmail, "_oauth_available", lambda: True)
    attachment = tmp_path / "report.txt"
    attachment.write_text("report body")
    result = gmail.gmail_action({
        "action": "send", "to": "friend@example.com", "subject": "Hello",
        "body": "plain", "html": "<b>rich</b>", "cc": "cc@example.com",
        "bcc": "bcc@example.com", "attachments": [str(attachment)],
    })
    assert "sent-id" in result
    raw = base64.urlsafe_b64decode(messages.sent[0]["body"]["raw"] + "==")
    message = email.message_from_bytes(raw)
    assert message["Cc"] == "cc@example.com" and message["Bcc"] == "bcc@example.com"
    names = [part.get_filename() for part in message.walk() if part.get_filename()]
    assert names == ["report.txt"]


def test_gmail_threaded_reply_reuses_thread_id(monkeypatch):
    messages = _GmailMessages()
    monkeypatch.setattr(gmail, "_gmail_service", lambda: _fake_gmail_service(messages))
    monkeypatch.setattr(gmail, "_oauth_available", lambda: True)
    result = gmail.gmail_action({"action": "reply", "message_id": "orig",
                                 "body": "Thanks!", "to": ""})
    assert "sent-id" in result
    assert messages.sent[0]["body"]["threadId"] == "thread-9"
    raw = base64.urlsafe_b64decode(messages.sent[0]["body"]["raw"] + "==")
    message = email.message_from_bytes(raw)
    assert message["Subject"] == "Re: Original topic"
    assert message["In-Reply-To"] == "<abc@corp>"


def test_gmail_mark_trash_and_html_decoding(monkeypatch):
    messages = _GmailMessages()
    monkeypatch.setattr(gmail, "_gmail_service", lambda: _fake_gmail_service(messages))
    monkeypatch.setattr(gmail, "_oauth_available", lambda: True)
    assert "marked as unread" in gmail.gmail_action(
        {"action": "mark_unread", "message_id": "orig"}).lower()
    assert "trash" in gmail.gmail_action(
        {"action": "trash", "message_id": "orig"}).lower()
    assert messages.modified[0]["body"] == {"addLabelIds": ["UNREAD"]}
    assert messages.trashed[0] == {"userId": "me", "id": "orig"}
    html_message = email.message_from_string(
        "Content-Type: text/html\n\n<html><body><p>Hello &amp; welcome</p></body></html>")
    assert "Hello & welcome" in gmail._body_snippet(html_message)


def test_gmail_imap_smtp_fallback_has_parity(monkeypatch):
    cfg = {"gmail_user": "user@gmail.com", "gmail_app_password": "app-password"}
    sent = []

    class FakeSMTP:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, user, password):
            sent.append(("login", user, password))

        def sendmail(self, user, recipients, raw):
            sent.append(("send", user, recipients, raw))

    monkeypatch.setattr(gmail.smtplib, "SMTP_SSL",
                        lambda host, port, timeout=30: FakeSMTP())
    result = gmail._imap_send(cfg, {"to": "a@example.com", "subject": "Hi",
                                    "body": "Body", "cc": "cc@example.com"})
    assert "Email sent" in result
    assert sent[0][2] == "app-password"
    assert "cc@example.com" in sent[1][2]
    assert "Hi" in sent[1][3]


def test_gmail_env_overrides_are_read(monkeypatch, tmp_path):
    monkeypatch.setattr(gmail, "CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setenv("FRIDAY_GMAIL_USER", "env@gmail.com")
    monkeypatch.setenv("FRIDAY_GMAIL_APP_PASSWORD", "env-pass")
    cfg = gmail._load_config()
    assert cfg["gmail_user"] == "env@gmail.com"
    assert cfg["gmail_app_password"] == "env-pass"
