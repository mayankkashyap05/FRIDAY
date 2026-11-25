"""Google Workspace OAuth with installed-app PKCE and legacy device-flow APIs.

Desktop OAuth clients use a cryptographically-bound PKCE authorization-code
flow over an ephemeral 127.0.0.1 callback. ``auth_start`` / ``auth_poll`` remain
available for integrations that still use Google's device authorization flow.
Tokens are atomically persisted in ``config/google_token.json``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
import google.auth.transport.requests as gtr
from google.oauth2 import credentials as gcreds

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/userinfo.email",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"
DEVICE_URI = "https://oauth2.googleapis.com/device/code"
USER_URI = "https://www.googleapis.com/oauth2/v2/userinfo"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR = get_base_dir()
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
OAUTH_CONFIG_PATH = BASE_DIR / "config" / "google_oauth.json"
TOKEN_PATH = BASE_DIR / "config" / "google_token.json"
_cached = None
_token_lock = threading.RLock()
_active_lock = threading.Lock()
_active_sessions: dict[str, "_OAuthSession"] = {}


def _credential_pair(data: dict) -> tuple[str, str]:
    installed = data.get("installed") if isinstance(data.get("installed"), dict) else data
    cid = str(installed.get("client_id") or installed.get("google_client_id") or "").strip()
    secret = str(installed.get("client_secret") or installed.get("google_client_secret") or "").strip()
    return cid, secret


def _load_config() -> dict:
    """Load client credentials from either supported desktop config location."""
    merged: dict = {}
    for path in (CONFIG_PATH, OAUTH_CONFIG_PATH):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cid, secret = _credential_pair(data)
                if cid:
                    merged["google_client_id"] = cid
                if secret:
                    merged["google_client_secret"] = secret
                merged.update({k: v for k, v in data.items()
                               if k in ("google_client_id", "google_client_secret") and v})
        except (OSError, ValueError, TypeError):
            continue
    return merged


def _load_token() -> dict:
    try:
        data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_token(tok: dict) -> None:
    global _cached
    with _token_lock:
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(TOKEN_PATH.parent),
                                        prefix=".google_token_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(tok, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, TOKEN_PATH)
            _cached = None
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def client_ids() -> tuple[str, str]:
    config = _load_config()
    return (str(config.get("google_client_id") or "").strip(),
            str(config.get("google_client_secret") or "").strip())


def is_configured() -> bool:
    # Desktop app clients may legitimately omit a client secret.
    return bool(client_ids()[0])


def has_token() -> bool:
    return bool(_load_token().get("refresh_token"))


def _page(title: str, message: str, ok: bool) -> bytes:
    color = "#21b879" if ok else "#db6570"
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_message = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return ("<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>FRIDAY</title><style>body{font:16px Segoe UI,Arial;background:#10121b;color:#f5f6ff;"
            "display:grid;place-items:center;min-height:90vh}.card{max-width:480px;padding:38px;"
            "border:1px solid #34384b;border-radius:20px;background:#1b1e2b;text-align:center}"
            f"h1{{color:{color}}}p{{line-height:1.6;color:#c5c9dc}}</style>"
            f"<main class='card'><h1>{safe_title}</h1><p>{safe_message}</p></main></html>").encode("utf-8")


_SUCCESS_HTML = _page("FRIDAY is connected — you may close this tab.",
                      "Google Workspace access was connected successfully.", True)
_ERROR_HTML = _page("Google sign-in was not completed", "Return to FRIDAY and try again.", False)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


class _OAuthSession:
    def __init__(self, cid: str, secret: str, timeout: float):
        self.client_id = cid
        self.client_secret = secret
        self.verifier, self.challenge = _pkce_pair()
        self.state = secrets.token_urlsafe(24)
        self.result: dict = {"ok": False, "pending": True}
        self.done = threading.Event()
        self.timeout = max(1.0, float(timeout))
        self.server: http.server.HTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self.timer: threading.Timer | None = None
        self.auth_url = ""
        self.redirect_uri = ""

    def complete(self, result: dict) -> None:
        self.result = result
        self.done.set()
        server = self.server
        if server:
            threading.Thread(target=server.shutdown, daemon=True).start()

    def exchange(self, code: str) -> dict:
        payload = {
            "client_id": self.client_id,
            "code": code,
            "code_verifier": self.verifier,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret
        try:
            response = requests.post(TOKEN_URI, data=payload, timeout=30)
            try:
                data = response.json()
            except ValueError:
                data = {}
            if response.status_code != 200:
                return {"ok": False, "error": f"token exchange failed: {data.get('error', response.text[:200])}"}
            token = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": data.get("refresh_token"),
                "token": data.get("access_token"),
                "scopes": data.get("scope", " ".join(SCOPES)),
            }
            if data.get("email"):
                token["email"] = data["email"]
            if not token["refresh_token"]:
                return {"ok": False, "error": "Google did not return a refresh token; retry consent with prompt=consent."}
            _save_token(token)
            return {"ok": True, "email": data.get("email", "")}
        except Exception as exc:
            return {"ok": False, "error": f"token exchange failed: {exc}"}


def _start_pkce_session(timeout: float, open_browser: bool) -> tuple[dict, _OAuthSession | None]:
    cid, secret = client_ids()
    if not cid:
        return {"ok": False, "error": "Google Desktop OAuth client_id is not configured."}, None
    session = _OAuthSession(cid, secret, timeout)

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _reply(self, status: int, body: bytes):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self._reply(404, _ERROR_HTML)
                return
            query = parse_qs(parsed.query, keep_blank_values=True)
            got_state = query.get("state", [""])[0]
            if not hmac.compare_digest(got_state, session.state):
                self._reply(400, _page("Sign-in rejected", "The security state did not match. Return to FRIDAY and retry.", False))
                return
            error = query.get("error", [""])[0]
            if error:
                detail = "Google access was denied." if error == "access_denied" else f"Google returned: {error}"
                self._reply(400, _page("Google sign-in cancelled", detail, False))
                session.complete({"ok": False, "error": detail})
                return
            code = query.get("code", [""])[0]
            if not code:
                self._reply(400, _ERROR_HTML)
                session.complete({"ok": False, "error": "Google callback did not contain an authorization code."})
                return
            result = session.exchange(code)
            self._reply(200 if result.get("ok") else 400,
                        _SUCCESS_HTML if result.get("ok") else _ERROR_HTML)
            session.complete(result)

    try:
        session.server = http.server.HTTPServer(("127.0.0.1", 0), CallbackHandler)
        session.server.daemon_threads = True
        port = session.server.server_address[1]
        session.redirect_uri = f"http://127.0.0.1:{port}/callback"
        params = {
            "client_id": cid,
            "redirect_uri": session.redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": session.state,
            "code_challenge": session.challenge,
            "code_challenge_method": "S256",
        }
        session.auth_url = AUTH_URI + "?" + urlencode(params)

        def serve():
            try:
                session.server.serve_forever(poll_interval=0.1)
            finally:
                session.server.server_close()
                session.done.set()
                if session.timer:
                    session.timer.cancel()
                with _active_lock:
                    _active_sessions.pop(session.state, None)

        session.server_thread = threading.Thread(target=serve, name="FridayGoogleOAuth", daemon=True)
        session.server_thread.start()
        with _active_lock:
            _active_sessions[session.state] = session
        session.timer = threading.Timer(session.timeout, lambda: session.complete({"ok": False, "error": "timed out waiting for Google authorization."}))
        session.timer.daemon = True
        session.timer.start()
        if open_browser:
            webbrowser.open(session.auth_url)
        return ({"ok": True, "auth_url": session.auth_url,
                 "authorization_url": session.auth_url, "redirect_uri": session.redirect_uri,
                 "state": session.state, "code_verifier": session.verifier,
                 "code_challenge": session.challenge, "code_challenge_method": "S256"}, session)
    except Exception as exc:
        if session.timer:
            session.timer.cancel()
        if session.server:
            if session.server_thread and session.server_thread.is_alive():
                session.complete({"ok": False, "error": f"could not start local OAuth callback: {exc}"})
                session.server_thread.join(timeout=3)
            else:
                session.server.server_close()
        return {"ok": False, "error": f"could not start local OAuth callback: {exc}"}, None


def auth_pkce_start(timeout: int = 300, open_browser: bool = True) -> dict:
    """Start a non-blocking localhost PKCE flow and return a clickable URL."""
    result, _session = _start_pkce_session(timeout, open_browser)
    if result.get("ok"):
        # The verifier is private to the local session; do not expose it to callers.
        result.pop("code_verifier", None)
        result["message"] = "Waiting for Google authorization in the browser."
    return result


def authorize_browser(timeout: int = 180, open_browser: bool = True) -> dict:
    """Run PKCE authorization and block until success, denial, or timeout."""
    started, session = _start_pkce_session(timeout, open_browser)
    if not started.get("ok") or session is None:
        return started
    session.done.wait(max(0.0, float(timeout)) + 1.0)
    result = dict(session.result)
    if result.get("pending"):
        result = {"ok": False, "error": "timed out waiting for Google authorization."}
        session.complete(result)
    if result.get("ok") and result.get("email"):
        _cache_email(result["email"])
    result.pop("pending", None)
    result.pop("code_verifier", None)
    result["auth_url"] = started.get("auth_url")
    return result


def auth_browser(timeout_seconds: int = 240) -> dict:
    """Backward-compatible alias for the installed-app browser authorization."""
    return authorize_browser(timeout=timeout_seconds, open_browser=True)


def auth_browser_nopkce(port: int = 8787, timeout_seconds: int = 300) -> dict:
    """Deprecated compatibility wrapper; browser sign-in now always uses PKCE."""
    return authorize_browser(timeout=timeout_seconds, open_browser=True)


def auth_start() -> dict:
    """Legacy device-flow start API, retained for backward compatibility."""
    cid, _secret = client_ids()
    if not cid:
        return {"ok": False, "error": "google_client_id is not set in OAuth config."}
    try:
        resp = requests.post(DEVICE_URI, data={"client_id": cid, "scope": " ".join(SCOPES)}, timeout=30)
        if resp.status_code != 200:
            return {"ok": False, "error": f"device/code failed: {resp.text[:300]}"}
        data = resp.json()
        data["ok"] = True
        return data
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def auth_poll(device_code: str, interval: int = 5, timeout: int = 300) -> dict:
    """Legacy device-flow polling API, retained for existing callers."""
    cid, secret = client_ids()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = requests.post(TOKEN_URI, data={
                "client_id": cid, "client_secret": secret, "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }, timeout=30)
            data = response.json()
        except Exception as exc:
            return {"ok": False, "error": f"token request failed: {exc}"}
        if response.status_code == 200:
            token = {"client_id": cid, "client_secret": secret,
                     "refresh_token": data.get("refresh_token"), "token": data.get("access_token"),
                     "scopes": data.get("scope", " ".join(SCOPES))}
            _save_token(token)
            email = _fetch_email(data.get("access_token"))
            if email:
                _cache_email(email)
            return {"ok": True, "email": email}
        if data.get("error") not in ("authorization_pending", "slow_down"):
            return {"ok": False, "error": f"token error: {data.get('error', response.text[:200])}"}
        time.sleep(max(1, interval + (5 if data.get("error") == "slow_down" else 0)))
    return {"ok": False, "error": "timed out waiting for authorization."}


def _fetch_email(access_token: str) -> str | None:
    try:
        response = requests.get(USER_URI, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
        if response.status_code == 200:
            return response.json().get("email")
    except Exception:
        pass
    return None


def _cache_email(email: str) -> None:
    if not email:
        return
    token = _load_token()
    if token:
        token["email"] = email
        _save_token(token)


def get_credentials(force_refresh: bool = False):
    """Return working Google credentials, refreshing expired access tokens."""
    global _cached
    with _token_lock:
        token = _load_token()
        refresh = token.get("refresh_token")
        cid = token.get("client_id") or client_ids()[0]
        secret = token.get("client_secret") or client_ids()[1]
        if not refresh or not cid:
            return None
        if _cached is not None and not force_refresh:
            return _cached
        creds = gcreds.Credentials(token=token.get("token"), refresh_token=refresh,
                                   token_uri=TOKEN_URI, client_id=cid,
                                   client_secret=secret or None,
                                   scopes=token.get("scopes", SCOPES))
        if getattr(creds, "expired", False) or force_refresh:
            try:
                creds.refresh(gtr.Request())
                token["token"] = creds.token
                _save_token(token)
            except Exception:
                return None
        _cached = creds
        return creds


def email_address() -> str:
    token = _load_token()
    if token.get("email"):
        return str(token["email"])
    creds = get_credentials()
    if not creds:
        return ""
    email = _fetch_email(creds.token)
    if email:
        _cache_email(email)
        return email
    return ""


def revoke() -> bool:
    """Best-effort remote revocation followed by unconditional local sign-out."""
    global _cached
    token = _load_token()
    access_token = token.get("refresh_token") or token.get("token")
    revoked = False
    if access_token:
        try:
            response = requests.post(REVOKE_URI, data={"token": access_token}, timeout=15)
            revoked = response.status_code in (200, 204)
        except Exception:
            pass
    with _token_lock:
        try:
            TOKEN_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        _cached = None
    return revoked


def sign_out() -> bool:
    return revoke()


def status() -> str:
    cid, _secret = client_ids()
    authorized = has_token()
    email = email_address() if authorized else ""
    lines = ["Google OAuth:", f"  client configured: {bool(cid)}",
             f"  authorized:        {authorized}" + (f" ({email})" if email else "")]
    with _active_lock:
        pending = any(not session.done.is_set() for session in _active_sessions.values())
    if pending:
        lines.append("  browser sign-in:   waiting for callback")
    return "\n".join(lines)
