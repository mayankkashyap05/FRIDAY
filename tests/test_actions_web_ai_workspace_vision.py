"""Deep combinatorial tests for web, AI, workspace, gaming, smart home, and vision actions."""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# actions/document_qa.py & actions/pdf_reader.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/secret",
    "http://localhost/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "file:///etc/passwd",
    "ftp://example.com/a.txt",
])
def test_document_qa_ssrf_blocks_private_and_non_http(url):
    from actions.document_qa import _is_private_target

    assert _is_private_target(url) is True


def test_document_qa_ssrf_blocks_multi_ip_dns_with_private_addr(monkeypatch):
    from actions import document_qa

    # First IP is public, second IP is private -> must still be blocked!
    fake_infos = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: fake_infos)
    assert document_qa._is_private_target("https://dual.example.com/doc") is True


def test_pdf_reader_ask_gemini_non_quota_error_no_redundant_fallback(monkeypatch):
    from actions import pdf_reader

    calls = {"n": 0}

    def fake_ask(*_a, **_kw):
        calls["n"] += 1
        raise ValueError("Invalid argument")

    with patch("actions.pdf_reader._ask_gemini", side_effect=fake_ask):
        with pytest.raises(ValueError):
            pdf_reader._ask_gemini("q", "ctx")
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# actions/flight_finder.py
# ---------------------------------------------------------------------------

def test_flight_finder_url_encodes_and_compares_prices_numerically():
    from actions.flight_finder import _build_google_flights_url

    url = _build_google_flights_url("New York", "Los Angeles", "2026-12-01")
    assert "New+York" in url and "Los+Angeles" in url


# ---------------------------------------------------------------------------
# actions/drive.py
# ---------------------------------------------------------------------------

def _stub_google_auth(monkeypatch):
    import sys, types
    if "google.auth.transport.requests" not in sys.modules:
        monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
        monkeypatch.setitem(sys.modules, "google.auth", types.ModuleType("google.auth"))
        monkeypatch.setitem(sys.modules, "google.auth.transport", types.ModuleType("google.auth.transport"))
        monkeypatch.setitem(sys.modules, "google.auth.transport.requests", types.ModuleType("google.auth.transport.requests"))
        monkeypatch.setitem(sys.modules, "google.oauth2", types.ModuleType("google.oauth2"))
        goc = types.ModuleType("google.oauth2.credentials")
        goc.Credentials = MagicMock
        monkeypatch.setitem(sys.modules, "google.oauth2.credentials", goc)


def test_drive_escape_query_and_pdf_not_text(monkeypatch):
    import sys, types
    _stub_google_auth(monkeypatch)
    if "googleapiclient.discovery" not in sys.modules:
        gapi = types.ModuleType("googleapiclient")
        gdisc = types.ModuleType("googleapiclient.discovery")
        gdisc.build = MagicMock()
        ghttp = types.ModuleType("googleapiclient.http")
        ghttp.MediaFileUpload = MagicMock()
        ghttp.MediaIoBaseDownload = MagicMock()
        monkeypatch.setitem(sys.modules, "googleapiclient", gapi)
        monkeypatch.setitem(sys.modules, "googleapiclient.discovery", gdisc)
        monkeypatch.setitem(sys.modules, "googleapiclient.http", ghttp)
    from actions.drive import TEXT_MIME, _escape_drive_q

    assert _escape_drive_q("Bob's \\ File") == "Bob\\'s \\\\ File"
    assert "application/pdf" not in TEXT_MIME


# ---------------------------------------------------------------------------
# actions/gmail.py
# ---------------------------------------------------------------------------

def test_gmail_oauth_unknown_action_does_not_fall_through(monkeypatch):
    _stub_google_auth(monkeypatch)
    from actions import gmail
    import google_oauth

    monkeypatch.setattr(google_oauth, "has_token", lambda: True)
    res = gmail.gmail_action({"action": "bogus_action"})
    assert "Unknown gmail action" in res


# ---------------------------------------------------------------------------
# actions/smart_home.py
# ---------------------------------------------------------------------------

def test_smart_home_simulated_lifecycle(tmp_path, monkeypatch):
    from actions import smart_home

    cfg_file = tmp_path / "smart_home.json"
    cfg_file.write_text(
        '{"enabled": true, "mode": "simulated", "devices": [{"id": "lamp", "name": "Desk Lamp", "state": "off"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(smart_home, "CONFIG_PATH", cfg_file, raising=False)
    res = smart_home.smart_home_control(action="turn_on", device="lamp")
    assert isinstance(res, str)


# ---------------------------------------------------------------------------
# actions/screen_processor.py
# ---------------------------------------------------------------------------

def test_screen_processor_uses_model_flash(monkeypatch):
    import sys, types
    if "cv2" not in sys.modules:
        monkeypatch.setitem(sys.modules, "cv2", types.ModuleType("cv2"))
    if "mss" not in sys.modules:
        mss_mod = types.ModuleType("mss")
        mss_mod.tools = types.ModuleType("mss.tools")
        monkeypatch.setitem(sys.modules, "mss", mss_mod)
        monkeypatch.setitem(sys.modules, "mss.tools", mss_mod.tools)
    if "google.genai" not in sys.modules:
        g_mod = types.ModuleType("google")
        g_mod.genai = types.ModuleType("google.genai")
        monkeypatch.setitem(sys.modules, "google", g_mod)
        monkeypatch.setitem(sys.modules, "google.genai", g_mod.genai)
    from actions import screen_processor
    from config.models import MODEL_FLASH

    assert screen_processor.VISION_MODEL == MODEL_FLASH


# ---------------------------------------------------------------------------
# actions/vision_click.py
# ---------------------------------------------------------------------------

def test_vision_click_scales_coordinates_back_to_monitor(monkeypatch):
    from actions import vision_click

    # Simulate 2560x1440 monitor downscaled to 1280x720
    monkeypatch.setattr(vision_click, "_screenshot_jpeg", lambda: (b"jpg", 2560, 1440, 1280, 720))
    monkeypatch.setattr(vision_click, "_find_coordinates", lambda _img, _t: (640, 360))
    res = vision_click.vision_click_action({"target": "Send", "only_coords": "true"})
    assert "(1280, 720)" in res


# ---------------------------------------------------------------------------
# actions/game_updater.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hour,minute", [(-1, 0), (24, 0), (12, 60), ("bad", 0)])
def test_game_updater_schedule_rejects_invalid_time(hour, minute):
    from actions.game_updater import _schedule_daily_update

    res = _schedule_daily_update(hour=hour, minute=minute)
    assert "Invalid schedule time" in res
