# actions/browser_control.py
# Friday — persistent, human-like browser automation.
#
# Real Chromium installs are preferred (never chromedriver), sessions survive
# restarts via a persistent profile, and pages are inspected through compact
# ARIA snapshots rather than raw HTML dumps.

from __future__ import annotations

import asyncio
import inspect
import concurrent.futures
import os
import platform
import re
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import quote_plus, urlparse

try:
    from playwright.async_api import async_playwright
    from playwright.async_api import TimeoutError as PlaywrightTimeout
except ImportError:                                        # optional dependency
    async_playwright = None

    class PlaywrightTimeout(Exception):
        pass


def get_base_dir() -> Path:
    if getattr(os.sys, "frozen", False):
        return Path(os.sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
_profile_env = os.environ.get("FRIDAY_BROWSER_PROFILE_DIR", "").strip()
BROWSER_PROFILE_DIR = Path(_profile_env).expanduser() if _profile_env else (BASE_DIR / "data" / "browser_profile")
SCREENSHOT_DIR = BASE_DIR / "data" / "screenshots"
MAX_SNAPSHOT_CHARS = 6000
MAX_TEXT_CHARS = 12000

_ANTI_BOT_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--start-maximized",
    "--no-first-run",
    "--no-default-browser-check",
]
_ANTI_BOT_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")
    return bool(value) if value is not None else default


def _get_default_browser_id() -> str:
    """Raw default-browser identifier for the current OS."""
    system = platform.system()
    try:
        if system == "Windows":
            import winreg
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
                ) as key:
                    return str(winreg.QueryValueEx(key, "ProgId")[0]).lower()
            except Exception:
                pass
        elif system == "Darwin":
            result = subprocess.run(
                ["defaults", "read",
                 "com.apple.LaunchServices/com.apple.launchservices.secure", "LSHandlers"],
                capture_output=True, text=True, timeout=5)
            return result.stdout.lower()
        elif system == "Linux":
            result = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5)
            return result.stdout.lower()
    except Exception:
        pass
    return ""


_BROWSER_BINARIES = {
    "Windows": {
        "opera": ["opera.exe"], "brave": ["brave.exe"], "vivaldi": ["vivaldi.exe"],
        "chrome": ["chrome.exe"], "firefox": ["firefox.exe"], "edge": ["msedge.exe"],
    },
    "Darwin": {
        "opera": ["opera"], "brave": ["brave browser", "brave"], "vivaldi": ["vivaldi"],
        "chrome": ["google chrome", "google-chrome"], "firefox": ["firefox"],
    },
    "Linux": {
        "opera": ["opera", "opera-stable"], "brave": ["brave-browser", "brave"],
        "vivaldi": ["vivaldi-stable", "vivaldi"],
        "chrome": ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"],
        "firefox": ["firefox"],
    },
}

# Canonical Windows installs — checked BEFORE shutil.which("chrome") so a
# chromedriver binary on PATH can never shadow the real browser.
_WINDOWS_BROWSER_PATHS = {
    "chrome": [
        r"{PF}\Google\Chrome\Application\chrome.exe",
        r"{PF86}\Google\Chrome\Application\chrome.exe",
        r"{LOCAL}\Google\Chrome\Application\chrome.exe",
    ],
    "edge": [
        r"{PF86}\Microsoft\Edge\Application\msedge.exe",
        r"{PF}\Microsoft\Edge\Application\msedge.exe",
    ],
    "brave": [
        r"{PF}\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"{LOCAL}\BraveSoftware\Brave-Browser\Application\brave.exe",
    ],
    "opera": [
        r"{PF}\Opera\opera.exe",
        r"{LOCAL}\Programs\Opera\opera.exe",
    ],
    "vivaldi": [
        r"{PF}\Vivaldi\Application\vivaldi.exe",
        r"{LOCAL}\Vivaldi\Application\vivaldi.exe",
    ],
}

_WIN_APP_PATH_KEYS = {
    "chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "brave": ["brave.exe"],
    "opera": ["opera.exe", "launcher.exe"],
    "vivaldi": ["vivaldi.exe"],
}


def _is_chromedriver_path(path: str) -> bool:
    lowered = (path or "").replace("\\", "/").lower()
    return "chromedriver" in lowered or "chrome-for-testing" in lowered


def _expand_win_path(template: str) -> Path:
    replacements = {
        "{PF}": os.environ.get("ProgramFiles", r"C:\Program Files"),
        "{PF86}": os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        "{LOCAL}": os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    if os.name != "nt":
        template = template.replace("\\", os.sep)
    return Path(template)


def _windows_registry_exe(browser: str):
    try:
        import winreg
        for exe_name in _WIN_APP_PATH_KEYS.get(browser, []):
            key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        raw = str(winreg.QueryValue(key, None)).strip().strip('"')
                    raw = raw.split('"')[0].split(" --")[0].strip()
                    if raw and Path(raw).exists() and not _is_chromedriver_path(raw):
                        return raw
                except Exception:
                    continue
    except Exception:
        pass
    return None


def _canonical_windows_exe(browser: str) -> str | None:
    for template in _WINDOWS_BROWSER_PATHS.get(browser, []):
        candidate = _expand_win_path(template)
        if candidate.is_file() and not _is_chromedriver_path(str(candidate)):
            return str(candidate)
    return _windows_registry_exe(browser) or None


def _get_opera_executable() -> str | None:
    if platform.system() != "Windows":
        return None
    exe = _canonical_windows_exe("opera")
    if exe:
        print(f"[Browser] 🔍 Opera found via canonical path: {exe}")
        return exe
    try:
        import winreg
        keys = [
            r"SOFTWARE\Clients\StartMenuInternet\OperaStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\OperaGXStable\shell\open\command",
        ]
        for key_path in keys:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        raw = str(winreg.QueryValue(key, None))
                    exe = raw.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists() and not _is_chromedriver_path(exe):
                        return exe
                except Exception:
                    continue
    except Exception:
        pass
    return None


def _find_browser_executable(prog_id: str) -> tuple:
    """Return ``(engine, executable_path, channel)`` for the default browser."""
    system = platform.system()
    os_bins = _BROWSER_BINARIES.get(system, {})
    pid = (prog_id or "").lower()

    if any(token in pid for token in ("firefox", "mozilla")):
        return "firefox", None, None
    if "safari" in pid:
        return "webkit", None, None

    if system == "Windows":
        preferred = []
        if "edge" in pid:
            preferred = ["edge"]
        elif "opera" in pid:
            preferred = ["opera"]
        elif "brave" in pid:
            preferred = ["brave"]
        elif "vivaldi" in pid:
            preferred = ["vivaldi"]
        elif any(token in pid for token in ("chrome", "chromium", "google")) or not pid:
            preferred = ["chrome", "edge", "brave", "vivaldi"]
        for browser in preferred:
            exe = _canonical_windows_exe(browser)
            if exe:
                print(f"[Browser] 🔍 Found {browser} at: {exe}")
                return "chromium", exe, None
        if "edge" in pid:
            return "chromium", None, "msedge"

    if "opera" in pid:
        exe = _get_opera_executable()
        if exe:
            return "chromium", exe, None
        for binary in os_bins.get("opera", []):
            path = shutil.which(binary)
            if path and not _is_chromedriver_path(path):
                return "chromium", path, None

    browser_patterns = {
        "brave": ["brave"], "vivaldi": ["vivaldi"],
        "chrome": ["chrome", "chromium", "google-chrome"],
        "edge": ["msedge"],
    }
    for browser_name, patterns in browser_patterns.items():
        if pid and not any(p in pid for p in patterns):
            continue
        for binary in os_bins.get(browser_name, []):
            path = shutil.which(binary)
            if path and not _is_chromedriver_path(path):
                print(f"[Browser] 🔍 Found {browser_name} at: {path}")
                return "chromium", path, None

    if "chrome" in pid or not pid:
        return "chromium", None, "chrome"
    return "chromium", None, None


def _validate_url(raw: str, allow_local: bool = False) -> str:
    """Normalise and SSRF-check a user-supplied URL."""
    url = str(raw or "").strip().strip('"').strip("'")
    if not url:
        raise ValueError("No URL provided.")
    if "://" not in url and re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:[/?#]|$)", url):
        url = "https://" + url
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme '{scheme or 'none'}'. Use http:// or https://.")
    if not allow_local:
        try:
            from actions.document_qa import _is_private_target
            if _is_private_target(url):
                raise ValueError("That URL points to a private/internal network address.")
        except ImportError:
            pass
    return url


def _captcha_detected(page) -> bool:
    try:
        url = str(getattr(page, "url", "") or "")
        if "/sorry" in urlparse(url).path:
            return True
        title_fn = getattr(page, "title", None)
        title = title_fn() if callable(title_fn) else ""
        if inspect.isawaitable(title):       # sync Playwright gives str; async mocks don't
            title.close()
            title = ""
        if not isinstance(title, str):
            title = ""
        lowered = title.lower()
        return "unusual traffic" in lowered or "before you continue" in lowered and "google" in lowered
    except Exception:
        return False


def _compact_snapshot(raw: str, limit: int = MAX_SNAPSHOT_CHARS) -> str:
    """Trim an ARIA snapshot while keeping it readable."""
    lines = [line.rstrip() for line in str(raw or "").splitlines() if line.strip()]
    kept, used = [], 0
    for line in lines:
        if used + len(line) + 1 > limit:
            break
        kept.append(line)
        used += len(line) + 1
    if not kept:
        return "No accessible elements found."
    suffix = "\n… (snapshot truncated)" if used < sum(len(x) + 1 for x in lines) else ""
    return "\n".join(kept) + suffix


class _BrowserThread:
    """Single asyncio thread shared by every browser action."""

    def __init__(self):
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
        self._playwright = None
        self._browser = None
        self._context = None
        self._incog_context = None
        self._page = None
        self._incog_page = None
        self._engine_name = "chromium"
        self._exe_path = None
        self._channel = None
        self._cdp_url = os.environ.get("FRIDAY_CHROME_CDP_URL", "").strip()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="BrowserThread")
        self._thread.start()
        self._ready.wait(timeout=15)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._init())
        self._ready.set()
        self._loop.run_forever()

    async def _init(self):
        if async_playwright is None:
            raise RuntimeError("playwright is not installed. Run: pip install playwright")
        self._playwright = await async_playwright().start()

    def run(self, coro, timeout: int = 30):
        if not self._loop:
            raise RuntimeError("Browser thread not started.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ── Launch ──────────────────────────────────────────────────────────────

    async def _launch_browser_if_needed(self):
        if self._browser and getattr(self._browser, "is_connected", lambda: True)():
            return
        prog_id = _get_default_browser_id()
        self._engine_name, self._exe_path, self._channel = _find_browser_executable(prog_id)
        engine = getattr(self._playwright, self._engine_name, self._playwright.chromium)

        if self._cdp_url:
            self._browser = await self._playwright.chromium.connect_over_cdp(self._cdp_url)
            print("[Browser] ✅ Attached over CDP.")
            return

        launch_kwargs = {"headless": False}
        if self._engine_name == "chromium":
            launch_kwargs.update(args=_ANTI_BOT_ARGS, ignore_default_args=["--enable-automation"])
        if self._exe_path:
            launch_kwargs["executable_path"] = self._exe_path
        elif self._channel:
            launch_kwargs["channel"] = self._channel
        try:
            self._browser = await engine.launch(**launch_kwargs)
            print(f"[Browser] ✅ Launched ({self._engine_name})")
        except Exception as exc:
            print(f"[Browser] ⚠️ Launch failed ({exc}), falling back to bundled Chromium")
            self._browser = await self._playwright.chromium.launch(
                headless=False, args=_ANTI_BOT_ARGS,
                ignore_default_args=["--enable-automation"])

    async def _configure_context(self, context, persistent: bool = False):
        try:
            await context.add_init_script(_ANTI_BOT_SCRIPT)
        except Exception:
            pass
        return context

    async def _get_page(self, incognito: bool = False):
        await self._launch_browser_if_needed()
        return await (self._get_incognito_page() if incognito else self._get_normal_page())

    async def _get_normal_page(self):
        if self._page is not None and not self._page.is_closed():
            return self._page
        if self._context is None:
            if self._cdp_url:
                contexts = getattr(self._browser, "contexts", [])
                self._context = contexts[0] if contexts else await self._browser.new_context(viewport=None)
            else:
                kwargs = dict(
                    headless=False, args=_ANTI_BOT_ARGS,
                    ignore_default_args=["--enable-automation"], viewport=None,
                )
                if self._engine_name == "chromium":
                    kwargs["user_data_dir"] = str(BROWSER_PROFILE_DIR)
                    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                    engine = getattr(self._playwright, self._engine_name)
                    self._context = await engine.launch_persistent_context(**kwargs)
                else:
                    if self._exe_path:
                        kwargs["executable_path"] = self._exe_path
                    elif self._channel:
                        kwargs["channel"] = self._channel
                    self._context = await self._browser.new_context(**kwargs)
            await self._configure_context(self._context, persistent=True)
        pages = list(getattr(self._context, "pages", []) or [])
        self._page = pages[0] if pages else await self._context.new_page()
        return self._page

    async def _get_incognito_page(self):
        """A clean, isolated context — no profile, no cookies."""
        if self._incog_page and not self._incog_page.is_closed():
            return self._incog_page
        if self._incog_context:
            try:
                await self._incog_context.close()
            except Exception:
                pass
        print("[Browser] 🕵️  Opening private/incognito context...")
        kwargs = {"viewport": None}
        if self._engine_name == "firefox" and not self._cdp_url:
            kwargs["firefox_user_prefs"] = {"browser.privatebrowsing.autostart": True}
        self._incog_context = await self._browser.new_context(**kwargs)
        await self._configure_context(self._incog_context)
        self._incog_page = await self._incog_context.new_page()
        return self._incog_page

    # ── Locators ────────────────────────────────────────────────────────────

    async def _resolve_locator(self, page, target: str = "", selector: str = "",
                               role: str = ""):
        """CSS → semantic roles → label/placeholder/text, then visible fallback."""
        target = str(target or "").strip()
        selector = str(selector or "").strip()
        if selector:
            return page.locator(selector), f"selector {selector}"
        if target.startswith(("#", ".", "[", "//")):
            return page.locator(target), f"selector {target}"

        roles = [role] if role else ["button", "link", "textbox", "combobox", "searchbox"]
        for candidate in roles:
            try:
                locator = page.get_by_role(candidate, name=target, exact=False)
                if await locator.count():
                    return locator, f"role {candidate}"
            except Exception:
                continue
        for label, factory in (
            ("label", lambda: page.get_by_label(target, exact=False)),
            ("placeholder", lambda: page.get_by_placeholder(target, exact=False)),
            ("text", lambda: page.get_by_text(target, exact=False)),
        ):
            if not target:
                break
            try:
                locator = factory()
                if await locator.count():
                    return locator, label
            except Exception:
                continue
        if target:
            try:
                locator = page.locator(f"text={target}")
                if await locator.count():
                    return locator, "visible text"
            except Exception:
                pass
        return None, "no match"

    # ── Actions ─────────────────────────────────────────────────────────────

    async def _go_to(self, url: str, incognito: bool = False, allow_local: bool = False) -> str:
        url = _validate_url(url, allow_local=allow_local)
        page = await self._get_page(incognito=incognito)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            if _captcha_detected(page):
                return "Google CAPTCHA / '/sorry' page detected — please complete it in the browser."
            mode = " [private]" if incognito else ""
            return f"Opened{mode}: {page.url}"
        except PlaywrightTimeout:
            return f"Timeout loading: {url}"
        except ValueError as exc:
            return f"Blocked: {exc}"
        except Exception as exc:
            return f"Navigation error: {exc}"

    async def _search(self, query: str, engine: str = "google", incognito: bool = False,
                      allow_local: bool = False) -> str:
        q = quote_plus(str(query or ""))
        engines = {
            "google": f"https://www.google.com/search?q={q}",
            "bing": f"https://www.bing.com/search?q={q}",
            "duckduckgo": f"https://duckduckgo.com/?q={q}",
        }
        return await self._go_to(engines.get((engine or "google").lower(), engines["google"]),
                                 incognito=incognito, allow_local=allow_local)

    async def _click(self, selector=None, text=None, description=None,
                     incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        if _captcha_detected(page):
            return "CAPTCHA page detected — I won't click through it automatically."
        locator, how = await self._resolve_locator(page, text or description or "", selector or "")
        if locator is None:
            return f"Element not found: '{text or description or selector}'"
        try:
            await locator.first.click(timeout=8000)
            return f"Clicked ({how}): '{text or description or selector}'"
        except Exception as exc:
            return f"Click failed: {exc}"

    async def _type(self, selector=None, text: str = "", clear_first: bool = True,
                    press_enter: bool = False, description: str = "",
                    incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        locator, how = await self._resolve_locator(page, description or "", selector or "", "textbox")
        if locator is None:
            try:
                locator = page.locator(":focus").first
                how = "focused element"
            except Exception:
                return f"Could not find input: '{description or selector}'"
        try:
            element = locator.first
            if clear_first:
                await element.clear()
            await element.fill(str(text or ""))
            if press_enter:
                await element.press("Enter")
            return f"Text typed ({how})."
        except Exception as exc:
            return f"Type error: {exc}"

    async def _scroll(self, direction: str = "down", amount: int = 500,
                      incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        try:
            pixels = int(amount)
            pixels = max(-10000, min(10000, pixels))
            y = pixels if (direction or "down").lower() != "up" else -pixels
            await page.mouse.wheel(0, y)
            return f"Scrolled {direction}."
        except Exception as exc:
            return f"Scroll error: {exc}"

    async def _press(self, key: str, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        try:
            await page.keyboard.press(key)
            return f"Pressed: {key}"
        except Exception as exc:
            return f"Key error: {exc}"

    async def _get_text(self, incognito: bool = False, mode: str = "body") -> str:
        page = await self._get_page(incognito=incognito)
        if _captcha_detected(page):
            return "Google CAPTCHA / '/sorry' page detected — text cannot be read reliably."
        try:
            selector = "article, main, body" if mode == "main" else "body"
            text = await page.inner_text(selector)
            return str(text)[:MAX_TEXT_CHARS]
        except Exception as exc:
            return f"Could not get page text: {exc}"

    async def _snapshot(self, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        if _captcha_detected(page):
            return "CAPTCHA page detected — automation is paused."
        try:
            body = page.locator("body")
            if hasattr(body, "aria_snapshot"):
                return _compact_snapshot(await body.aria_snapshot())
        except Exception:
            pass
        # DOM fallback for older Playwright builds.
        try:
            html = await page.inner_html("body")
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            keep = []
            for tag in soup.find_all(["h1", "h2", "h3", "a", "button", "input", "textarea", "select"]):
                role = {
                    "a": "link", "button": "button", "input": "textbox",
                    "textarea": "textbox", "select": "combobox",
                }.get(tag.name, "heading")
                name = tag.get("aria-label") or tag.get("placeholder") or tag.get_text(" ", strip=True)
                if name:
                    keep.append(f"- {role} {name[:100]}")
            return _compact_snapshot("\n".join(keep))
        except Exception as exc:
            return f"Snapshot failed: {exc}"

    async def _fill_form(self, fields: dict, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        results = []
        for selector, value in (fields or {}).items():
            try:
                element = page.locator(selector).first
                await element.clear()
                await element.fill(str(value))
                results.append(f"✓ {selector}")
            except Exception as exc:
                results.append(f"✗ {selector}: {exc}")
        return "Form filled: " + ", ".join(results)

    async def _smart_click(self, description: str, incognito: bool = False) -> str:
        return await self._click(description=description, incognito=incognito)

    async def _smart_type(self, description: str, text: str, incognito: bool = False,
                          press_enter: bool = False) -> str:
        return await self._type(description=description, text=text, incognito=incognito,
                                press_enter=press_enter)

    async def _hover(self, selector=None, description=None, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        locator, how = await self._resolve_locator(page, description or "", selector or "")
        if locator is None:
            return f"Element not found: '{description or selector}'"
        try:
            await locator.first.hover(timeout=5000)
            return f"Hovered ({how})."
        except Exception as exc:
            return f"Hover failed: {exc}"

    async def _select(self, selector=None, description=None, value: str = "",
                      incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        locator, how = await self._resolve_locator(page, description or "", selector or "", "combobox")
        if locator is None:
            return f"Dropdown not found: '{description or selector}'"
        try:
            await locator.first.select_option(value=str(value))
            return f"Selected '{value}' ({how})."
        except Exception as exc:
            return f"Select failed: {exc}"

    async def _wait_for(self, selector: str = "", text: str = "", timeout: int = 10,
                        incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        seconds = max(1, min(30, int(timeout)))
        try:
            if selector:
                await page.wait_for_selector(selector, timeout=seconds * 1000)
                return f"Found selector: {selector}"
            if text:
                await page.get_by_text(text, exact=False).first.wait_for(timeout=seconds * 1000)
                return f"Found text: {text}"
            return "No selector or text provided."
        except Exception:
            return f"Timed out waiting for: {selector or text}"

    async def _extract_links(self, incognito: bool = False, limit: int = 40) -> str:
        page = await self._get_page(incognito=incognito)
        try:
            links = await page.locator("a[href]").evaluate_all(
                "(els) => els.map(e => [e.innerText.trim(), e.href]).filter(x => x[0] || x[1])")
            lines = [f"[{text or url}]({url})" for text, url in links[:max(1, min(int(limit), 200))]]
            return "\n".join(lines) if lines else "No links found."
        except Exception as exc:
            return f"Could not extract links: {exc}"

    async def _screenshot(self, save_path: str = "", incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        target = Path(save_path).expanduser() if save_path else SCREENSHOT_DIR / f"browser_{int(asyncio.get_event_loop().time())}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            await page.screenshot(path=str(target), full_page=False)
            return f"Screenshot saved: {target}"
        except Exception as exc:
            return f"Screenshot failed: {exc}"

    async def _back(self, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        try:
            await page.go_back(wait_until="domcontentloaded", timeout=10000)
            return f"Navigated back: {page.url}"
        except Exception as exc:
            return f"Back failed: {exc}"

    async def _forward(self, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        try:
            await page.go_forward(wait_until="domcontentloaded", timeout=10000)
            return f"Navigated forward: {page.url}"
        except Exception as exc:
            return f"Forward failed: {exc}"

    async def _reload(self, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        try:
            await page.reload(wait_until="domcontentloaded", timeout=10000)
            return f"Reloaded: {page.url}"
        except Exception as exc:
            return f"Reload failed: {exc}"

    async def _page_info(self, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        try:
            title = await page.title()
        except Exception:
            title = ""
        context = page.context
        tabs = list(getattr(context, "pages", []) or [])
        return f"URL: {page.url}\nTitle: {title}\nTabs: {len(tabs)}"

    async def _new_tab(self, url: str = "", incognito: bool = False,
                       allow_local: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        context = page.context
        new_page = await context.new_page()
        if incognito:
            self._incog_page = new_page
        else:
            self._page = new_page
        if url:
            normalized = _validate_url(url, allow_local=allow_local)
            await new_page.goto(normalized, wait_until="domcontentloaded", timeout=15000)
        return f"New tab opened: {new_page.url}"

    async def _list_tabs(self, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        lines = []
        for index, tab in enumerate(list(getattr(page.context, "pages", []) or []), 1):
            try:
                title = await tab.title()
            except Exception:
                title = ""
            marker = " *" if tab is page else ""
            lines.append(f"{index}. {title or '(untitled)'}{marker} — {tab.url}")
        return "\n".join(lines) if lines else "No tabs open."

    async def _switch_tab(self, index: int = 0, match: str = "", incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        tabs = list(getattr(page.context, "pages", []) or [])
        if not tabs:
            return "No tabs open."
        target = None
        if index:
            try:
                position = int(index) - 1
                target = tabs[position] if 0 <= position < len(tabs) else None
            except (TypeError, ValueError):
                target = None
        if target is None and match:
            needle = str(match).lower()
            for tab in tabs:
                try:
                    title = (await tab.title()).lower()
                except Exception:
                    title = ""
                if needle in title or needle in str(tab.url).lower():
                    target = tab
                    break
        if target is None:
            return "Tab not found."
        await target.bring_to_front()
        if incognito:
            self._incog_page = target
        else:
            self._page = target
        return f"Switched to tab: {target.url}"

    async def _close_tab(self, index: int = 0, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        tabs = list(getattr(page.context, "pages", []) or [])
        target = page
        if index:
            try:
                position = int(index) - 1
                target = tabs[position] if 0 <= position < len(tabs) else page
            except (TypeError, ValueError):
                pass
        await target.close()
        remaining = list(getattr(page.context, "pages", []) or [])
        if incognito:
            self._incog_page = remaining[0] if remaining else None
        else:
            self._page = remaining[0] if remaining else None
        return f"Tab closed. {len(remaining)} tab(s) remain."

    async def _close_browser(self) -> str:
        for context in (self._incog_context, self._context):
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
        self._incog_context = self._context = None
        self._incog_page = self._page = None
        if self._browser and not self._cdp_url:
            try:
                await self._browser.close()
            except Exception:
                pass
        self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        return "Browser closed."


# ── Singleton thread ────────────────────────────────────────────────────────

_bt = _BrowserThread()
_bt_started = False
_bt_lock = threading.Lock()


def _ensure_started():
    global _bt_started
    with _bt_lock:
        if not _bt_started:
            _bt.start()
            _bt_started = True


def browser_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None
) -> str:
    """Control the browser through a persistent, anti-detect Chromium profile."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action", "") or "").lower().strip().replace("-", "_")
    incognito = _as_bool(params.get("incognito"), False)
    allow_local = _as_bool(params.get("allow_local"), False)

    try:
        if action == "close":
            return _bt.run(_bt._close_browser())
        _ensure_started()
    except Exception as exc:
        return f"Browser error: {exc}"

    result = "Unknown action."
    try:
        if action in ("go_to", "goto", "open", "navigate"):
            result = _bt.run(_bt._go_to(params.get("url", ""), incognito, allow_local))
        elif action == "search":
            result = _bt.run(_bt._search(params.get("query", ""), params.get("engine", "google"),
                                         incognito, allow_local))
        elif action == "click":
            result = _bt.run(_bt._click(params.get("selector"), params.get("text"),
                                        params.get("description"), incognito))
        elif action in ("type", "fill"):
            result = _bt.run(_bt._type(
                params.get("selector"), params.get("text", ""),
                _as_bool(params.get("clear_first"), True),
                _as_bool(params.get("press_enter"), False),
                params.get("description", ""), incognito))
        elif action in ("snapshot", "aria_snapshot", "inspect"):
            result = _bt.run(_bt._snapshot(incognito))
        elif action == "scroll":
            result = _bt.run(_bt._scroll(params.get("direction", "down"),
                                         int(params.get("amount", 500) or 500), incognito))
        elif action == "fill_form":
            fields = params.get("fields", {})
            result = _bt.run(_bt._fill_form(fields if isinstance(fields, dict) else {}, incognito))
        elif action == "smart_click":
            result = _bt.run(_bt._smart_click(params.get("description", ""), incognito))
        elif action == "smart_type":
            result = _bt.run(_bt._smart_type(params.get("description", ""), params.get("text", ""),
                                             incognito, _as_bool(params.get("press_enter"), False)))
        elif action == "hover":
            result = _bt.run(_bt._hover(params.get("selector"), params.get("description"), incognito))
        elif action == "select":
            result = _bt.run(_bt._select(params.get("selector"), params.get("description"),
                                         params.get("value", ""), incognito))
        elif action == "wait_for":
            result = _bt.run(_bt._wait_for(params.get("selector", ""), params.get("text", ""),
                                           int(params.get("timeout", 10) or 10), incognito))
        elif action == "extract_links":
            result = _bt.run(_bt._extract_links(incognito, int(params.get("limit", 40) or 40)))
        elif action == "extract_text":
            result = _bt.run(_bt._get_text(incognito, "main"))
        elif action == "get_text":
            result = _bt.run(_bt._get_text(incognito))
        elif action == "screenshot":
            result = _bt.run(_bt._screenshot(params.get("save_path", ""), incognito))
        elif action == "back":
            result = _bt.run(_bt._back(incognito))
        elif action == "forward":
            result = _bt.run(_bt._forward(incognito))
        elif action == "reload":
            result = _bt.run(_bt._reload(incognito))
        elif action == "page_info":
            result = _bt.run(_bt._page_info(incognito))
        elif action == "new_tab":
            result = _bt.run(_bt._new_tab(params.get("url", ""), incognito, allow_local))
        elif action in ("list_tabs", "tabs"):
            result = _bt.run(_bt._list_tabs(incognito))
        elif action == "switch_tab":
            result = _bt.run(_bt._switch_tab(params.get("index", 0), params.get("match", ""), incognito))
        elif action == "close_tab":
            result = _bt.run(_bt._close_tab(params.get("index", 0), incognito))
        elif action == "press":
            result = _bt.run(_bt._press(params.get("key", "Enter"), incognito))
        else:
            result = f"Unknown action: {action}"
    except (concurrent.futures.TimeoutError, PlaywrightTimeout):
        result = "Browser action timed out."
    except Exception as exc:
        result = f"Browser error: {exc}"

    print(f"[Browser] {result[:80]}")
    if player:
        try:
            player.write_log(f"[browser] {result[:60]}")
        except Exception:
            pass
    return result
