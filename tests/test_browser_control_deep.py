"""100%-offline tests for persistent, anti-detect browser control."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from actions import browser_control as bc


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeBT(bc._BrowserThread):
    """Runs coroutines synchronously without a browser thread."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def run(self, coro, timeout=30):
        return run_async(coro)


@pytest.fixture
def fake_bt(monkeypatch):
    instance = FakeBT()
    monkeypatch.setattr(bc, "_bt", instance)
    monkeypatch.setattr(bc, "_ensure_started", lambda: None)
    return instance


# ── executable resolution ──────────────────────────────────────────────────

def test_canonical_chrome_prefers_real_install(monkeypatch, tmp_path):
    pf = tmp_path / "ProgramFiles"
    pf86 = tmp_path / "ProgramFiles86"
    real = pf / "Google" / "Chrome" / "Application" / "chrome.exe"
    shadow = pf86 / "Google" / "Chrome" / "Application" / "chrome.exe"
    real.parent.mkdir(parents=True)
    shadow.parent.mkdir(parents=True)
    real.write_bytes(b"")
    shadow.write_bytes(b"")
    monkeypatch.setattr(bc.platform, "system", lambda: "Windows")
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.setenv("ProgramFiles(x86)", str(pf86))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert bc._find_browser_executable("chromehtml") == ("chromium", str(real), None)
    assert "chromedriver" not in str(bc._find_browser_executable(""))


# ── persistent context / anti-fingerprint ──────────────────────────────────

def test_persistent_context_launch_options_and_init_script(monkeypatch, tmp_path):
    monkeypatch.setattr(bc, "BROWSER_PROFILE_DIR", tmp_path / "profile")
    page = MagicMock()
    page.is_closed.return_value = False
    page.context = MagicMock(pages=[page])
    context = MagicMock(pages=[page])
    context.add_init_script = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    engine = MagicMock()
    engine.launch_persistent_context = AsyncMock(return_value=context)
    pw = MagicMock(chromium=engine)
    thread = bc._BrowserThread()
    thread._playwright = pw
    thread._browser = MagicMock()
    thread._engine_name = "chromium"

    result = run_async(thread._get_normal_page())
    assert result is page
    kwargs = engine.launch_persistent_context.await_args.kwargs
    assert kwargs["ignore_default_args"] == ["--enable-automation"]
    assert "--disable-blink-features=AutomationControlled" in kwargs["args"]
    assert kwargs["viewport"] is None
    assert "user_agent" not in kwargs
    assert str(kwargs["user_data_dir"]).endswith("profile")
    assert "navigator" in context.add_init_script.await_args.args[0]


def test_incognito_is_ephemeral_and_cdp_attach(monkeypatch):
    page = MagicMock()
    page.is_closed.return_value = False
    context = MagicMock(pages=[page])
    context.add_init_script = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    browser = MagicMock(new_context=AsyncMock(return_value=context), contexts=[])
    thread = bc._BrowserThread()
    thread._playwright = MagicMock(chromium=MagicMock())
    thread._browser = browser
    thread._engine_name = "chromium"
    assert run_async(thread._get_incognito_page()) is page
    assert "user_agent" not in browser.new_context.await_args.kwargs

    thread2 = bc._BrowserThread()
    thread2._cdp_url = "http://127.0.0.1:9222"
    engine = MagicMock()
    engine.connect_over_cdp = AsyncMock(return_value=browser)
    thread2._playwright = MagicMock(chromium=engine)
    run_async(thread2._launch_browser_if_needed())
    engine.connect_over_cdp.assert_awaited_once_with("http://127.0.0.1:9222")


# ── URL safety ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "file:///etc/passwd", "javascript:alert(1)", "data:text/html,x",
    "chrome://settings", "edge://flags", "about:blank",
    "http://127.0.0.1:8000/x", "http://169.254.169.254/latest/",
])
def test_validate_url_blocks_dangerous_schemes_and_ssrf(monkeypatch, bad):
    with pytest.raises(ValueError):
        bc._validate_url(bad)


def test_validate_url_auto_https_and_allow_local():
    assert bc._validate_url("github.com/mayankkashyap05/FRIDAY") == "https://github.com/mayankkashyap05/FRIDAY"
    assert bc._validate_url("http://localhost:3000", allow_local=True) == "http://localhost:3000"


# ── aria snapshots, semantics, tabs ────────────────────────────────────────

def test_snapshot_prefers_aria_and_does_not_launch_real_browser(fake_bt, monkeypatch):
    page = MagicMock()
    page.locator.return_value.aria_snapshot = AsyncMock(return_value="- button Submit\n" * 500)
    page.title = AsyncMock(return_value="Title")
    fake_bt._get_page = AsyncMock(return_value=page)
    monkeypatch.setattr(bc, "_bt", fake_bt)
    result = bc.browser_control({"action": "snapshot"})
    assert "button Submit" in result
    assert len(result) <= bc.MAX_SNAPSHOT_CHARS + 50


def test_semantic_locator_order_and_click(fake_bt, monkeypatch):
    role_locator = MagicMock()
    role_locator.count = AsyncMock(return_value=1)
    role_locator.first = MagicMock(click=AsyncMock())
    page = MagicMock()
    page.get_by_role.return_value = role_locator
    fake_bt._get_page = AsyncMock(return_value=page)
    monkeypatch.setattr(bc, "_bt", fake_bt)

    locator, how = run_async(fake_bt._resolve_locator(page, "Submit", "", "button"))
    assert how == "role button" and locator is role_locator
    result = bc.browser_control({"action": "click", "description": "Submit"})
    assert "Clicked" in result
    role_locator.first.click.assert_awaited()


def test_type_press_enter_and_smart_actions(fake_bt, monkeypatch):
    element = MagicMock(clear=AsyncMock(), fill=AsyncMock(), press=AsyncMock())
    locator = MagicMock(first=element, count=AsyncMock(return_value=1))
    page = MagicMock()
    page.get_by_role.return_value = locator
    fake_bt._get_page = AsyncMock(return_value=page)
    monkeypatch.setattr(bc, "_bt", fake_bt)
    assert "typed" in bc.browser_control(
        {"action": "type", "description": "Email", "text": "a@b.com", "press_enter": "true"})
    element.fill.assert_awaited_with("a@b.com")
    element.press.assert_awaited_with("Enter")
    assert isinstance(bc.browser_control({"action": "smart_type", "description": "Email", "text": "x"}), str)


def test_tab_lifecycle_and_navigation(fake_bt, monkeypatch):
    pages = [MagicMock(url="https://one.example"), MagicMock(url="https://two.example")]
    for index, page in enumerate(pages, 1):
        page.title = AsyncMock(return_value=f"Page {index}")
        page.context = MagicMock(pages=pages)
        page.bring_to_front = AsyncMock()
        page.close = AsyncMock()
    active = pages[0]
    active.context.new_page = AsyncMock(return_value=pages[1])
    fake_bt._get_page = AsyncMock(return_value=active)
    monkeypatch.setattr(bc, "_bt", fake_bt)

    listing = bc.browser_control({"action": "list_tabs"})
    assert "1." in listing and "2." in listing and "*" in listing
    assert "Switched" in bc.browser_control({"action": "switch_tab", "index": 2})
    assert "New tab" in bc.browser_control({"action": "new_tab"})
    assert "closed" in bc.browser_control({"action": "close_tab", "index": 2}).lower()


def test_wait_extract_links_select_hover_history_actions(fake_bt, monkeypatch):
    locator = MagicMock(count=AsyncMock(return_value=1), wait_for=AsyncMock(),
                        select_option=AsyncMock(), hover=AsyncMock())
    locator.first = locator
    page = MagicMock()
    page.get_by_role.return_value = locator
    page.wait_for_selector = AsyncMock()
    page.locator.return_value.evaluate_all = AsyncMock(
        return_value=[["Docs", "https://docs.example"]])
    page.inner_text = AsyncMock(return_value="Body text")
    page.goto = AsyncMock()
    page.go_back = AsyncMock()
    page.go_forward = AsyncMock()
    page.reload = AsyncMock()
    page.title = AsyncMock(return_value="Title")
    page.url = "https://example.com"
    page.context = MagicMock(pages=[page])
    fake_bt._get_page = AsyncMock(return_value=page)
    monkeypatch.setattr(bc, "_bt", fake_bt)

    assert "Found selector" in bc.browser_control({"action": "wait_for", "selector": "#x", "timeout": 99})
    assert page.wait_for_selector.await_args.kwargs["timeout"] == 30000
    assert "[Docs](https://docs.example)" in bc.browser_control({"action": "extract_links"})
    assert "Selected" in bc.browser_control({"action": "select", "description": "Country", "value": "IN"})
    assert "Hovered" in bc.browser_control({"action": "hover", "description": "Country"})
    assert "Title" in bc.browser_control({"action": "page_info"})
    assert "back" in bc.browser_control({"action": "back"}).lower()
    assert "forward" in bc.browser_control({"action": "forward"}).lower()
    assert "Reloaded" in bc.browser_control({"action": "reload"})


def test_captcha_page_is_reported_honestly(fake_bt, monkeypatch):
    page = MagicMock(url="https://www.google.com/sorry/index")
    page.title = AsyncMock(return_value="Google")
    page.inner_text = AsyncMock(return_value="garbled")
    fake_bt._get_page = AsyncMock(return_value=page)
    monkeypatch.setattr(bc, "_bt", fake_bt)
    assert "CAPTCHA" in bc.browser_control({"action": "get_text"})
    assert "CAPTCHA" in bc.browser_control({"action": "snapshot"})


def test_browser_control_handles_none_params_and_close(fake_bt, monkeypatch):
    monkeypatch.setattr(bc, "_bt", fake_bt)
    assert isinstance(bc.browser_control(None), str)
    fake_bt._close_browser = AsyncMock(return_value="Browser closed.")
    assert bc.browser_control({"action": "close"}) == "Browser closed."
