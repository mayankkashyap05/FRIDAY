"""Offline tests for the localhost OAuth callback and legacy compatibility."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import sys
import threading
import time
import types
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock

import pytest

# Prefer real Google auth packages when available. Only create a minimal
# fallback for minimal test installations, preserving the google namespace
# (notably google.genai) if it was already imported.
try:
    import google.auth.transport.requests  # noqa: F401
    import google.oauth2.credentials  # noqa: F401
except ImportError:
    google = sys.modules.setdefault("google", types.ModuleType("google"))
    auth = sys.modules.setdefault("google.auth", types.ModuleType("google.auth"))
    transport = sys.modules.setdefault("google.auth.transport", types.ModuleType("google.auth.transport"))
    transport_requests = sys.modules.setdefault("google.auth.transport.requests", types.ModuleType("google.auth.transport.requests"))
    oauth2 = sys.modules.setdefault("google.oauth2", types.ModuleType("google.oauth2"))
    credentials = sys.modules.setdefault("google.oauth2.credentials", types.ModuleType("google.oauth2.credentials"))
    credentials.Credentials = MagicMock
    google.auth = auth
    auth.transport = transport
    transport.requests = transport_requests
    google.oauth2 = oauth2
    oauth2.credentials = credentials

import google_oauth as oauth


@pytest.fixture
def oauth_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, "CONFIG_PATH", tmp_path / "config" / "api_keys.json")
    monkeypatch.setattr(oauth, "OAUTH_CONFIG_PATH", tmp_path / "config" / "google_oauth.json")
    monkeypatch.setattr(oauth, "TOKEN_PATH", tmp_path / "config" / "google_token.json")
    monkeypatch.setattr(oauth, "_cached", None)
    with oauth._active_lock:
        for session in list(oauth._active_sessions.values()):
            session.complete({"ok": False, "error": "test cleanup"})
        oauth._active_sessions.clear()
    yield tmp_path
    with oauth._active_lock:
        for session in list(oauth._active_sessions.values()):
            session.complete({"ok": False, "error": "test cleanup"})


def set_client(oauth_paths, installed=False):
    path = oauth.OAUTH_CONFIG_PATH if installed else oauth.CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"installed": {"client_id": "desktop-client.apps.googleusercontent.com",
                             "client_secret": "desktop-secret"}} if installed else {
        "google_client_id": "desktop-client.apps.googleusercontent.com",
        "google_client_secret": "desktop-secret",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def callback(session, query):
    conn = http.client.HTTPConnection("127.0.0.1", session.server.server_address[1], timeout=3)
    conn.request("GET", "/callback?" + query)
    response = conn.getresponse()
    body = response.read().decode("utf-8")
    status = response.status
    conn.close()
    return status, body


def stop_session(session):
    if session and not session.done.is_set():
        session.complete({"ok": False, "error": "test cleanup"})
    if session and session.server_thread:
        session.server_thread.join(timeout=3)


def test_pkce_verifier_challenge_rfc7636_format_and_hash():
    verifier, challenge = oauth._pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9._~-]+", verifier)
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    assert challenge == expected
    assert len(challenge) == 43


def test_installed_client_credentials_are_loaded(oauth_paths):
    set_client(oauth_paths, installed=True)
    assert oauth.client_ids() == ("desktop-client.apps.googleusercontent.com", "desktop-secret")
    assert oauth.is_configured()


def test_pkce_loopback_valid_callback_exchanges_and_persists(oauth_paths, monkeypatch):
    set_client(oauth_paths)
    oauth._cached = "stale"
    opened = []
    monkeypatch.setattr(oauth.webbrowser, "open", lambda url: opened.append(url) or True)
    post_calls = []
    class Response:
        status_code = 200
        text = "ok"
        def json(self): return {"access_token": "access-1", "refresh_token": "refresh-1", "scope": "mail"}
    monkeypatch.setattr(oauth.requests, "post", lambda url, data, timeout: post_calls.append((url, data)) or Response())

    info = oauth.auth_pkce_start(timeout=10, open_browser=True)
    assert info["ok"] and opened == [info["auth_url"]]
    params = parse_qs(urlparse(info["auth_url"]).query)
    assert params["redirect_uri"] == [info["redirect_uri"]]
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"] == [info["state"]]
    session = oauth._active_sessions[info["state"]]
    try:
        status, body = callback(session, "state=" + info["state"] + "&code=auth-code")
        assert status == 200 and "FRIDAY is connected" in body
        assert session.done.wait(2) and session.result["ok"]
        assert post_calls[0][0] == oauth.TOKEN_URI
        assert post_calls[0][1]["code"] == "auth-code"
        assert post_calls[0][1]["code_verifier"] == session.verifier
        assert post_calls[0][1]["client_id"] == "desktop-client.apps.googleusercontent.com"
        assert post_calls[0][1]["redirect_uri"] == info["redirect_uri"]
        token = json.loads(oauth.TOKEN_PATH.read_text(encoding="utf-8"))
        assert token["refresh_token"] == "refresh-1"
        assert oauth._cached is None
        assert list(oauth.TOKEN_PATH.parent.glob(".google_token_*.tmp")) == []
    finally:
        stop_session(session)


def test_state_mismatch_rejected_and_does_not_save_token(oauth_paths, monkeypatch):
    set_client(oauth_paths)
    monkeypatch.setattr(oauth.webbrowser, "open", lambda _url: True)
    monkeypatch.setattr(oauth.requests, "post", lambda *a, **k: pytest.fail("must not exchange without valid state"))
    info = oauth.auth_pkce_start(timeout=10, open_browser=False)
    session = oauth._active_sessions[info["state"]]
    try:
        status, body = callback(session, "state=attacker&code=stolen")
        assert status == 400 and "security state did not match" in body
        assert not session.done.is_set()
        assert not oauth.TOKEN_PATH.exists()
    finally:
        stop_session(session)


def test_access_denied_has_friendly_callback_response(oauth_paths, monkeypatch):
    set_client(oauth_paths)
    info = oauth.auth_pkce_start(timeout=10, open_browser=False)
    session = oauth._active_sessions[info["state"]]
    try:
        status, body = callback(session, "state=" + info["state"] + "&error=access_denied")
        assert status == 400 and "Google sign-in cancelled" in body
        assert session.done.wait(2)
        assert not session.result["ok"]
        assert "denied" in session.result["error"].lower()
        assert not oauth.TOKEN_PATH.exists()
    finally:
        stop_session(session)


def test_authorize_browser_timeout_closes_loopback_socket(oauth_paths, monkeypatch):
    set_client(oauth_paths)
    monkeypatch.setattr(oauth.webbrowser, "open", lambda _url: True)
    started = time.monotonic()
    result = oauth.authorize_browser(timeout=0.05, open_browser=True)
    assert time.monotonic() - started < 4
    assert not result["ok"] and "timed out" in result["error"]
    # Timer expiration triggers shutdown; allow its serving thread to finish.
    deadline = time.monotonic() + 2
    while oauth._active_sessions and time.monotonic() < deadline:
        time.sleep(.01)
    assert not oauth._active_sessions


def test_token_refresh_and_sign_out_cleanup(oauth_paths, monkeypatch):
    set_client(oauth_paths)
    oauth._save_token({"client_id": "desktop-client", "client_secret": "desktop-secret",
                       "refresh_token": "refresh", "token": "expired", "scopes": oauth.SCOPES})
    oauth._cached = None
    instances = []
    class FakeCredentials:
        expired = True
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            instances.append(self)
        def refresh(self, request): self.token = "refreshed"
    monkeypatch.setattr(oauth.gcreds, "Credentials", FakeCredentials)
    monkeypatch.setattr(oauth.gtr, "Request", lambda: object(), raising=False)
    creds = oauth.get_credentials()
    assert creds.token == "refreshed"
    assert json.loads(oauth.TOKEN_PATH.read_text())["token"] == "refreshed"

    class RevokeResponse: status_code = 200
    calls = []
    monkeypatch.setattr(oauth.requests, "post", lambda url, data, timeout: calls.append((url, data)) or RevokeResponse())
    oauth._cached = creds
    assert oauth.revoke()
    assert calls[0][0] == oauth.REVOKE_URI and calls[0][1]["token"] == "refresh"
    assert not oauth.TOKEN_PATH.exists() and oauth._cached is None
    assert oauth.sign_out() is False


def test_legacy_auth_start_and_poll_still_work(oauth_paths, monkeypatch):
    set_client(oauth_paths)
    class DeviceResponse:
        status_code = 200
        text = ""
        def json(self): return {"device_code": "device", "user_code": "ABCD", "interval": 1}
    class TokenResponse:
        status_code = 200
        text = ""
        def json(self): return {"access_token": "token", "refresh_token": "refresh", "scope": "mail"}
    monkeypatch.setattr(oauth.requests, "post", lambda url, **kwargs: DeviceResponse() if url == oauth.DEVICE_URI else TokenResponse())
    monkeypatch.setattr(oauth, "_fetch_email", lambda _token: "person@example.com")
    assert oauth.auth_start()["user_code"] == "ABCD"
    result = oauth.auth_poll("device", interval=1, timeout=1)
    assert result == {"ok": True, "email": "person@example.com"}
    assert oauth._load_token()["refresh_token"] == "refresh"
