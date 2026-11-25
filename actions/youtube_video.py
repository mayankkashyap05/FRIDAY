"""Deterministic, production-grade YouTube control for FRIDAY.

Everything YouTube-related lives here: zero-key HTTP search, a persistent
Playwright player controller, playback control, transcripts, summaries,
video info and modern category charts.  Every optional dependency (Playwright,
pygetwindow, pyautogui, youtube-transcript-api, requests, BeautifulSoup) is
imported lazily or guarded, so this module imports cleanly on Windows and
Linux even when none of them are installed.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse, urlunparse

try:
    import requests
    _REQUESTS_OK = True
except ImportError:                                    # pragma: no cover - environment
    requests = None
    _REQUESTS_OK = False

try:
    from bs4 import BeautifulSoup
    _BEAUTIFULSOUP_OK = True
except ImportError:                                    # pragma: no cover - environment
    BeautifulSoup = None
    _BEAUTIFULSOUP_OK = False

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    _TRANSCRIPT_OK = True
except ImportError:                                    # pragma: no cover - environment
    YouTubeTranscriptApi = None
    _TRANSCRIPT_OK = False


# ---------------------------------------------------------------------------
# Legacy-compatible constants / browser discovery
# ---------------------------------------------------------------------------

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# YouTube asks for these cookies on consent-gated sessions.  They are public
# preference cookies, not credentials.
SEARCH_COOKIES = {"CONSENT": "YES+cb", "SOCS": "CAI"}
YOUTUBE_HOME = "https://www.youtube.com"
SEARCH_ENDPOINT = "https://www.youtube.com/results?search_query={query}&hl=en"

CAPTCHA_MESSAGE = (
    "YouTube is asking for a quick Google verification (CAPTCHA) in the browser "
    "window, sir. Please complete it once — FRIDAY uses a persistent browser "
    "profile so it will remember your verification."
)
NO_VIDEO_MESSAGE = "No YouTube video is currently loaded in FRIDAY's player, sir."

# A real, persistent browser profile avoids the ephemeral-context fingerprint
# that sends Playwright sessions to Google's /sorry interstitial.
_LAST_PLAYED_LOCK = threading.RLock()
_LAST_PLAYED_VIDEO: dict[str, str] = {}


def _youtube_profile_dir() -> Path:
    configured = os.environ.get("FRIDAY_YOUTUBE_PROFILE_DIR", "").strip()
    return Path(configured).expanduser() if configured else BASE_DIR / "data" / "youtube_profile"


def _native_mode() -> bool:
    return os.environ.get("FRIDAY_YOUTUBE_MODE", "").strip().lower() == "native"


def _cdp_url() -> str:
    return os.environ.get("FRIDAY_CHROME_CDP_URL", "").strip()


def _remember_played_video(info: dict) -> None:
    with _LAST_PLAYED_LOCK:
        _LAST_PLAYED_VIDEO.clear()
        _LAST_PLAYED_VIDEO.update({
            "video_id": str(info.get("video_id") or ""),
            "title": str(info.get("title") or ""),
            "channel": str(info.get("channel") or ""),
            "url": str(info.get("url") or ""),
        })


def _last_played_video() -> dict:
    with _LAST_PLAYED_LOCK:
        return dict(_LAST_PLAYED_VIDEO)


async def _is_blocked_page(page) -> bool:
    """Detect Google's /sorry CAPTCHA interstitial without false positives."""
    try:
        if "/sorry" in str(getattr(page, "url", "")).lower():
            return True
    except Exception:
        pass
    try:
        title = (await page.title() or "").lower()
        if "unusual traffic" in title or ("captcha" in title and "google" in title):
            return True
    except Exception:
        pass
    for selector in ("#captcha-form", 'iframe[src*="recaptcha"]'):
        try:
            locator = page.locator(selector).first
            if await locator.count():
                return True
        except Exception:
            continue
    try:
        body = await page.evaluate("() => (document.body && document.body.innerText) || ''")
        if body and "unusual traffic" in str(body).lower():
            return True
    except Exception:
        pass
    try:
        body_text = await page.inner_text("body")
        if body_text and "unusual traffic" in str(body_text).lower():
            return True
    except Exception:
        pass
    return False


def _is_youtube_page(url: str) -> bool:
    try:
        host = urlparse(str(url or "")).hostname or ""
    except Exception:
        return False
    host = host.lower()
    return any(host == domain or host.endswith("." + domain)
               for domain in ("youtube.com", "youtu.be"))


LANGUAGE_PRIORITY = [
    "en", "hi", "tr", "de", "fr", "es", "it", "pt",
    "ru", "ja", "ko", "ar", "zh",
]

_AD_RENDERER_KEYS = frozenset({
    "promotedVideoRenderer", "adSlotRenderer", "displayAdRenderer",
    "promotedSparklesWebRenderer", "inlineSurveyRenderer",
})


def _request_headers() -> dict:
    """Desktop headers + consent cookies for public YouTube pages."""
    return dict(HEADERS, Cookie="; ".join(
        f"{key}={value}" for key, value in SEARCH_COOKIES.items()
    ))


def _get_api_key() -> str:
    """Legacy helper — summaries now use api_keys.generate_with_retry directly."""
    from api_keys import next_key
    return next_key()


def _get_default_browser_name() -> str | None:
    """Registry'den varsayılan tarayıcının gerçek yürütülebilir adını döndürür."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        )
        prog_id = winreg.QueryValueEx(key, "ProgId")[0].lower()
        winreg.CloseKey(key)
        mapping = {
            "chrome": "chrome", "firefox": "firefox", "opera": "opera",
            "brave": "brave", "vivaldi": "vivaldi", "msedge": "msedge",
            "edge": "msedge",
        }
        for key_str, exe in mapping.items():
            if key_str in prog_id:
                return exe
    except Exception as exc:
        print(f"[YouTube] Browser detect failed: {exc}")
    return None


def _get_default_browser_display_name() -> str:
    """Windows Search'e yazılacak tarayıcı adını döndürür."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        )
        prog_id = winreg.QueryValueEx(key, "ProgId")[0].lower()
        winreg.CloseKey(key)
        display_names = {
            "chrome": "Google Chrome", "firefox": "Firefox", "opera": "Opera",
            "brave": "Brave", "vivaldi": "Vivaldi", "msedge": "Microsoft Edge",
            "edge": "Microsoft Edge",
        }
        for key_str, name in display_names.items():
            if key_str in prog_id:
                return name
    except Exception:
        pass
    return "Google Chrome"


def _get_default_browser_id() -> str:
    """Raw default-browser identifier for the current OS (used by the controller)."""
    system = platform.system()
    try:
        if system == "Windows":
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
            ) as key:
                return str(winreg.QueryValueEx(key, "ProgId")[0]).lower()
        if system == "Darwin":
            result = subprocess.run(
                ["defaults", "read", "com.apple.LaunchServices/com.apple.launchservices.secure",
                 "LSHandlers"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.lower()
        if system == "Linux":
            result = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.lower()
    except Exception:
        pass
    return ""


def _find_browser_executable(prog_id: str) -> tuple[str, str | None, str | None]:
    """Prefer the user's default Chromium browser, then Playwright's Chromium."""
    system = platform.system()
    binaries = {
        "Windows": ["chrome.exe", "msedge.exe", "opera.exe", "brave.exe", "vivaldi.exe"],
        "Darwin": ["google-chrome", "chromium", "microsoft-edge", "brave browser", "opera"],
        "Linux": ["google-chrome", "google-chrome-stable", "chromium-browser",
                  "chromium", "microsoft-edge", "brave-browser", "opera-stable"],
    }.get(system, [])

    names = ["chrome", "msedge", "opera", "brave", "vivaldi"]
    ordered = [n for n in names if n in (prog_id or "").lower()] + names
    seen = set()
    for name in ordered:
        if name in seen:
            continue
        seen.add(name)
        for binary in binaries:
            if name in binary.lower() or (name == "chrome" and "chrome" in binary):
                path = shutil.which(binary)
                if path:
                    return "chromium", path, None
    if "chrome" in (prog_id or "").lower() or not prog_id:
        return "chromium", None, "chrome"
    return "chromium", None, None


def _browser_launch_candidates() -> list[dict]:
    """Launch order: detected real browser, Chrome, Edge, bundled Chromium."""
    detected = (_get_default_browser_name() or "").strip().lower()
    if detected in ("chrome", "msedge"):
        preferred = {"channel": detected}
    elif detected in ("opera", "brave", "vivaldi"):
        exe = (shutil.which(detected) or shutil.which(detected + ".exe") or
               shutil.which("brave-browser") or shutil.which("opera-stable"))
        preferred = {"executable_path": exe} if exe else {}
    else:
        preferred = {}

    candidates = [preferred] if preferred else []
    for channel in ("chrome", "msedge", None):
        candidate = {"channel": channel} if channel else {}
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def open_browser() -> None:
    """Legacy helper: open the user's default browser (kept for compatibility)."""
    import pyautogui

    browser_exe = _get_default_browser_name()
    if browser_exe:
        exe_path = shutil.which(browser_exe) or shutil.which(browser_exe + ".exe")
        if exe_path:
            try:
                subprocess.Popen([exe_path])
                time.sleep(1.0)
                print(f"[YouTube] Opened browser via exe: {exe_path}")
                return
            except Exception as exc:
                print(f"[YouTube] Direct exe failed: {exc}")

    display_name = _get_default_browser_display_name()
    print(f"[YouTube] Opening via Windows Search: '{display_name}'")
    pyautogui.press("win")
    time.sleep(0.5)
    pyautogui.write(display_name, interval=0.04)
    time.sleep(0.7)
    pyautogui.press("enter")


def find_video_thumbnails() -> list[tuple[int, int]]:
    """Deprecated screenshot heuristic, retained for imports from older code.

    Search now uses deterministic ``ytInitialData`` JSON; this function is no
    longer called by the automation engine.
    """
    try:
        import numpy as np
        import cv2
        from PIL import ImageGrab

        screenshot = ImageGrab.grab()
        img = np.array(screenshot)
        screen_h, screen_w = img.shape[:2]
        roi = img[int(screen_h * 0.10):int(screen_h * 0.75),
                  int(screen_w * 0.20):int(screen_w * 0.80)]
        gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
        edges = cv2.dilate(cv2.Canny(gray, 30, 100),
                           np.ones((3, 3), np.uint8), iterations=2)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            ratio = w / h if h > 0 else 0
            if w * h < 15000 or not (1.4 < ratio < 2.2):
                continue
            candidates.append((x + w // 2 + int(screen_w * 0.20),
                               y + h // 2 + int(screen_h * 0.10), w * h))
        filtered: list[tuple[int, int]] = []
        for cx, cy, _area in sorted(candidates, key=lambda c: c[1]):
            if not any(abs(cx - fx) < 80 and abs(cy - fy) < 80 for fx, fy in filtered):
                filtered.append((cx, cy))
        return filtered
    except Exception as exc:
        print(f"[YouTube] Thumbnail detection failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Pure parsing helpers
# ---------------------------------------------------------------------------

def _extract_video_id(url: str | None) -> str | None:
    """Extract an 11-character YouTube video id from any common URL form."""
    if not url:
        return None
    text = str(url).strip()
    try:
        parsed = urlparse(text)
        if parsed.scheme in ("http", "https"):
            host = parsed.netloc.lower().split(":")[0]
            if not any(domain in host for domain in
                       ("youtube.com", "youtube-nocookie.com", "youtu.be")):
                return None
    except Exception:
        return None

    patterns = (
        r"[?&](?:v|vi)=([\w-]{11})(?=[\?&#/]|$)",
        r"youtu\.be/([\w-]{11})(?=[\?&#/]|$)",
        r"/(?:v|embed|shorts|live)/([\w-]{11})(?=[\?&#/]|$)",
        r"^\s*([\w-]{11})\s*$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _is_valid_youtube_url(url: str | None) -> bool:
    return bool(_extract_video_id(url)) or bool(
        re.search(r"(youtube\.com|youtu\.be)", str(url or ""), re.IGNORECASE)
    )


def _ask_for_url(prompt_text: str = "YouTube video URL:") -> str | None:
    """Legacy blocking dialog.  Never called from the command dispatcher."""
    try:
        import tkinter as tk
        from tkinter import simpledialog

        root = tk._default_root
        if root is None:
            root = tk.Tk()
            root.withdraw()
        url = simpledialog.askstring("Friday", prompt_text, parent=root)
        return url.strip() if url else None
    except Exception as exc:
        print(f"[YouTube] URL dialog failed: {exc}")
        return None


def _parse_timestamp(value) -> float | None:
    """Parse seconds, ``mm:ss``, ``hh:mm:ss``, ``2m30s`` and signed offsets."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower().replace(" ", "")
    if not text:
        return None
    sign = 1.0
    if text[0] in "+-":
        if text[0] == "-":
            sign = -1.0
        text = text[1:]
    if not text:
        return None

    seconds: float | None = None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        seconds = float(text)
    elif re.fullmatch(r"\d+(?::\d+(?:\.\d+)?)+", text):
        parts = [float(part) for part in text.split(":")]
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + part
    else:
        match = re.fullmatch(
            r"(?:(\d+(?:\.\d+)?)\s*h)?(?:(\d+(?:\.\d+)?)\s*m)?(?:(\d+(?:\.\d+)?)\s*s)?",
            text,
        )
        if match and any(match.groups()):
            hours, minutes, secs = match.groups()
            seconds = (float(hours or 0) * 3600 +
                       float(minutes or 0) * 60 + float(secs or 0))
    return None if seconds is None else sign * seconds


def _is_relative_timestamp(value) -> bool:
    return str(value or "").strip().startswith(("+", "-"))


def _format_time(seconds) -> str:
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _number(value, default: float | None = None) -> float | None:
    """Return a finite float or ``default`` for bad/non-numeric input."""
    if value is None or isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result and abs(result) != float("inf") else default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _canonical_watch_url(url: str) -> str:
    """Normalise youtu.be/shorts/embed links to a canonical watch URL.

    Existing query parameters (notably ``t=90s`` and playlists) are preserved.
    """
    video_id = _extract_video_id(url)
    if not video_id:
        return url
    try:
        parts = list(urlparse(url))
        query = dict(parse_qs(parts[4], keep_blank_values=True))
        query["v"] = [video_id]
        return urlunparse((
            "https", "www.youtube.com", "/watch", "",
            urlencode(query, doseq=True), "",
        ))
    except Exception:
        return f"https://www.youtube.com/watch?v={video_id}"


def _append_timestamp(url: str, seconds) -> str:
    """Add ``&t=N s`` to a watch URL without duplicating existing params."""
    seconds_num = _number(seconds)
    if seconds_num is None:
        return url
    try:
        parts = list(urlparse(url))
        query = dict(parse_qs(parts[4], keep_blank_values=True))
        query["t"] = [f"{int(abs(seconds_num))}s"]
        parts[4] = urlencode(query, doseq=True)
        parts[5] = ""
        return urlunparse(parts)
    except Exception:
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}t={int(abs(seconds_num))}s"


def _extract_json_blob(html: str, marker: str) -> dict | None:
    """Brace-balanced ``ytInitialData``/``ytInitialPlayerResponse`` extraction."""
    if not html:
        return None
    start = html.find(marker)
    if start < 0:
        return None
    brace = html.find("{", start)
    if brace < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(brace, len(html)):
        char = html[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(html[brace:index + 1])
                    return parsed if isinstance(parsed, dict) else None
                except (ValueError, TypeError):
                    break

    # Regex fallback for minified pages with unexpected trailing characters.
    match = re.search(re.escape(marker) + r"\s*=\s*(\{.*?\})\s*;", html, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1))
            return parsed if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            pass
    return None


def _extract_yt_initial_data(html: str) -> dict | None:
    return _extract_json_blob(html, "ytInitialData")


def _text_from_runs(node) -> str:
    if isinstance(node, str):
        return node.strip()
    if not isinstance(node, dict):
        return ""
    if node.get("simpleText"):
        return str(node["simpleText"]).strip()
    runs = node.get("runs")
    if isinstance(runs, list):
        return "".join(str(run.get("text", "")) for run in runs if isinstance(run, dict)).strip()
    return ""


def _walk_video_renderers(node):
    """Yield every ``videoRenderer`` while pruning ad renderer subtrees."""
    if isinstance(node, list):
        for item in node:
            yield from _walk_video_renderers(item)
        return
    if not isinstance(node, dict):
        return
    if any(key in node for key in _AD_RENDERER_KEYS):
        return
    renderer = node.get("videoRenderer")
    if isinstance(renderer, dict):
        yield renderer
    for value in node.values():
        yield from _walk_video_renderers(value)


def _renderer_to_result(renderer: dict, include_shorts: bool = False) -> dict | None:
    video_id = renderer.get("videoId")
    if not isinstance(video_id, str) or not re.fullmatch(r"[\w-]{11}", video_id):
        return None

    endpoint = renderer.get("navigationEndpoint") or {}
    metadata = endpoint.get("commandMetadata") or {}
    web = metadata.get("webCommandMetadata") or {}
    hint_url = str(web.get("url") or "")
    badges = " ".join(
        _text_from_runs(badge)
        for badge in (renderer.get("badges") or [])
        if isinstance(badge, dict)
    )
    is_short = "/shorts/" in hint_url or "shorts" in badges.lower()
    if is_short and not include_shorts:
        return None

    title = (_text_from_runs(renderer.get("title")) or
             _text_from_runs(renderer.get("headline")) or "Untitled")
    channel = (_text_from_runs(renderer.get("ownerText")) or
               _text_from_runs(renderer.get("longBylineText")) or
               _text_from_runs(renderer.get("shortBylineText")) or
               _text_from_runs(renderer.get("owner")) or "Unknown")
    duration = (_text_from_runs(renderer.get("lengthText")) or
                _text_from_runs(renderer.get("lengthSeconds")) or "")
    views = (_text_from_runs(renderer.get("viewCountText")) or
             _text_from_runs(renderer.get("shortViewCountText")) or "")
    is_live = ("LIVE" in badges.upper() or "watching now" in views.lower() or
               bool(renderer.get("isLive")))

    return {
        "video_id": video_id,
        "title": title,
        "channel": channel,
        "duration": duration,
        "views": views,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "is_live": is_live,
        "is_short": is_short,
    }


def _parse_search_html(html: str, include_shorts: bool = False) -> list[dict]:
    """BeautifulSoup fallback used when the embedded JSON is unavailable."""
    if not html or not _BEAUTIFULSOUP_OK:
        return []
    results: list[dict] = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select("a#video-title"):
            href = str(anchor.get("href") or "")
            video_id = _extract_video_id(href)
            if not video_id or ("/shorts/" in href and not include_shorts):
                continue
            container = anchor.find_parent("ytd-video-renderer") or anchor.parent
            channel_node = container.select_one("#channel-name, ytd-channel-name") if container else None
            results.append({
                "video_id": video_id,
                "title": anchor.get_text(" ", strip=True) or "Untitled",
                "channel": channel_node.get_text(" ", strip=True) if channel_node else "Unknown",
                "duration": "",
                "views": "",
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "is_live": False,
                "is_short": "/shorts/" in href,
            })
    except Exception as exc:
        print(f"[YouTube] HTML search fallback failed: {exc}")
    return results


# ---------------------------------------------------------------------------
# Search cache (so "play the second one" resolves instantly)
# ---------------------------------------------------------------------------

_SEARCH_CACHE_LOCK = threading.RLock()
_LAST_SEARCH_RESULTS: list[dict] = []
_LAST_SEARCH_QUERY = ""


def _cache_results(results: list[dict], query: str | None = None) -> None:
    with _SEARCH_CACHE_LOCK:
        _LAST_SEARCH_RESULTS[:] = [dict(item) for item in results]
        if query is not None:
            global _LAST_SEARCH_QUERY
            _LAST_SEARCH_QUERY = query


def _get_cached_results() -> list[dict]:
    with _SEARCH_CACHE_LOCK:
        return [dict(item) for item in _LAST_SEARCH_RESULTS]


def _search_youtube_videos(
    query: str,
    include_shorts: bool = False,
    max_results: int = 20,
) -> list[dict]:
    """Zero-API-key search using YouTube's embedded ``ytInitialData`` JSON."""
    query = str(query or "").strip()
    if not query or not _REQUESTS_OK:
        return []
    url = SEARCH_ENDPOINT.format(query=quote_plus(query))
    try:
        response = requests.get(url, headers=_request_headers(), timeout=15)
        try:
            html = getattr(response, "text", "") or ""
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
        data = _extract_yt_initial_data(html)
        results: list[dict] = []
        if data:
            seen: set[str] = set()
            for renderer in _walk_video_renderers(data):
                item = _renderer_to_result(renderer, include_shorts=include_shorts)
                if item and item["video_id"] not in seen:
                    seen.add(item["video_id"])
                    results.append(item)
        if not results:
            results = _parse_search_html(html, include_shorts=include_shorts)
        if not results:
            return []
        results = results[:max(1, int(max_results))]
        _cache_results(results, query)
        return results
    except Exception as exc:
        print(f"[YouTube] Search failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Transcript / Gemini / video info / charts
# ---------------------------------------------------------------------------

def _snippet_text(snippet) -> str:
    if isinstance(snippet, dict):
        return str(snippet.get("text", ""))
    return str(getattr(snippet, "text", "") or "")


def _get_transcript(video_id: str) -> str | None:
    """Fetch a transcript with both youtube-transcript-api v1.x and v0.6.x."""
    if not _TRANSCRIPT_OK or YouTubeTranscriptApi is None:
        return None

    try:
        api = YouTubeTranscriptApi
        transcript = None

        # v1.2+ instance API: YouTubeTranscriptApi().list(video_id).fetch()
        if hasattr(api, "list"):
            try:
                listing = api().list(video_id)
                for language in LANGUAGE_PRIORITY:
                    try:
                        candidate = listing.find_transcript([language])
                        if candidate is not None:
                            transcript = candidate
                            break
                    except Exception:
                        continue
                if transcript is None:
                    try:
                        for candidate in listing:
                            transcript = candidate
                            break
                    except Exception:
                        pass
                if transcript is not None:
                    fetched = transcript.fetch()
                    text = " ".join(filter(None, (_snippet_text(item) for item in fetched))).strip()
                    if text:
                        return text
            except Exception as exc:
                print(f"[YouTube] Transcript v1.x attempt failed: {exc}")

        # v0.6.x static API: YouTubeTranscriptApi.list_transcripts(video_id)
        if hasattr(api, "list_transcripts"):
            try:
                listing = api.list_transcripts(video_id)
                for finder in ("find_manually_created_transcript", "find_generated_transcript"):
                    try:
                        transcript = getattr(listing, finder)(LANGUAGE_PRIORITY)
                        if transcript is not None:
                            break
                    except Exception:
                        transcript = None
                if transcript is None:
                    for candidate in listing:
                        transcript = candidate
                        break
                if transcript is not None:
                    fetched = transcript.fetch()
                    text = " ".join(filter(None, (_snippet_text(item) for item in fetched))).strip()
                    if text:
                        return text
            except Exception as exc:
                print(f"[YouTube] Transcript v0.6 attempt failed: {exc}")

        # Some v1.x builds expose only fetch(video_id, languages=[...]).
        if hasattr(api, "fetch"):
            try:
                fetched = api().fetch(video_id, languages=LANGUAGE_PRIORITY)
                text = " ".join(filter(None, (_snippet_text(item) for item in fetched))).strip()
                if text:
                    return text
            except Exception as exc:
                print(f"[YouTube] Transcript fetch attempt failed: {exc}")
        return None
    except Exception as exc:
        print(f"[YouTube] Transcript fetch failed: {exc}")
        return None


def _summarize_with_gemini(transcript: str, video_url: str) -> str:
    """Summarise via key-rotating ``api_keys.generate_with_retry``."""
    from api_keys import generate_with_retry
    from config.models import MODEL_FLASH

    max_chars = 80000
    truncated = transcript[:max_chars] + ("..." if len(transcript) > max_chars else "")
    prompt = (
        "You are Friday, a sharp and efficient AI assistant. Summarize this "
        "YouTube video transcript clearly and concisely. Structure: one sentence "
        "overview, then 3-5 key points. Be direct. Address the user as 'sir'. "
        "Match the language of the transcript.\n\n"
        f"Video: {video_url}\n\nTranscript:\n{truncated}"
    )
    response = generate_with_retry(model=MODEL_FLASH, contents=prompt)
    text = getattr(response, "text", None)
    if text is None:
        candidates = getattr(response, "candidates", None) or []
        text = "".join(
            getattr(getattr(candidate, "content", None), "text", "") or ""
            for candidate in candidates
        )
    return str(text or response).strip()


def _save_to_notepad(content: str, video_url: str) -> str:
    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    desktop = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    filepath = desktop / f"youtube_summary_{timestamp}.txt"
    header = (
        f"Friday — YouTube Summary\n{'─' * 50}\n"
        f"URL    : {video_url}\n"
        f"Date   : {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{'─' * 50}\n\n"
    )
    filepath.write_text(header + content, encoding="utf-8")

    system = platform.system()
    opener = {
        "Windows": lambda path: subprocess.Popen(["notepad.exe", str(path)]),
        "Darwin": lambda path: subprocess.Popen(["open", "-t", str(path)]),
        "Linux": lambda path: subprocess.Popen(["xdg-open", str(path)]),
    }.get(system)
    if opener:
        try:
            opener(filepath)
        except Exception as exc:
            print(f"[YouTube] Could not open summary file: {exc}")
    return str(filepath)


def _scrape_video_info(video_id: str) -> dict:
    """Parse ``ytInitialPlayerResponse.videoDetails`` with regex fallbacks."""
    if not _REQUESTS_OK or not video_id:
        return {}
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        response = requests.get(url, headers=_request_headers(), timeout=12)
        try:
            html = getattr(response, "text", "") or ""
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
        info: dict = {}
        player_response = _extract_json_blob(html, "ytInitialPlayerResponse") or {}
        details = player_response.get("videoDetails") if isinstance(player_response, dict) else None
        if isinstance(details, dict):
            if details.get("title"):
                info["title"] = str(details["title"])
            if details.get("author"):
                info["channel"] = str(details["author"])
            views = details.get("viewCount")
            if views is not None:
                try:
                    info["views"] = f"{int(views):,}"
                except (TypeError, ValueError):
                    info["views"] = str(views)
            length = details.get("lengthSeconds")
            if length is not None:
                try:
                    info["duration"] = _format_time(int(length))
                except (TypeError, ValueError):
                    pass
            if details.get("shortDescription"):
                info["description"] = str(details["shortDescription"])
            info["is_live"] = bool(details.get("isLiveContent", False))
            info["video_id"] = video_id
            info["url"] = url

        title_match = re.search(r'"title":\{"runs":\[\{"text":"([^"]+)"', html)
        if title_match and not info.get("title"):
            info["title"] = title_match.group(1)
        channel_match = re.search(r'"ownerChannelName":"([^"]+)"', html)
        if channel_match and not info.get("channel"):
            info["channel"] = channel_match.group(1)
        views_match = re.search(r'"viewCount":"(\d+)"', html)
        if views_match and not info.get("views"):
            info["views"] = f"{int(views_match.group(1)):,}"
        duration_match = re.search(r'"lengthSeconds":"(\d+)"', html)
        if duration_match and not info.get("duration"):
            info["duration"] = _format_time(int(duration_match.group(1)))
        likes_match = re.search(r'"label":"([0-9,]+ likes)"', html)
        if likes_match:
            info["likes"] = likes_match.group(1)
        if not info:
            info["video_id"] = video_id
            info["url"] = url
        return info
    except Exception as exc:
        print(f"[YouTube] Video info failed: {exc}")
        return {}


_CHART_QUERIES = {
    "general": "trending popular videos",
    "music": "trending music videos",
    "gaming": "trending gaming videos",
    "movies": "trending movies trailers",
    "podcasts": "trending podcasts",
}


def _scrape_trending(region: str | None = None, max_results: int = 8,
                     category: str = "general") -> list[dict]:
    """Ranked category charts / popular videos (YouTube retired /feed/trending)."""
    if not _REQUESTS_OK:
        return []
    region = (str(region or "IN").strip().upper() or "IN")[:2]
    category = str(category or "general").strip().lower().replace(" ", "_")
    query = _CHART_QUERIES.get(category, _CHART_QUERIES["general"])

    endpoints = []
    if category == "general":
        endpoints.append(f"https://www.youtube.com/feed/trending?gl={region}&hl=en")
    endpoints.append(
        f"https://www.youtube.com/results?search_query={quote_plus(f'{query} {region}')}&hl=en"
    )

    items: list[dict] = []
    for endpoint in endpoints:
        try:
            response = requests.get(endpoint, headers=_request_headers(), timeout=15)
            try:
                html = getattr(response, "text", "") or ""
            finally:
                close = getattr(response, "close", None)
                if close:
                    close()
            data = _extract_yt_initial_data(html)
            raw: list[dict] = []
            if data:
                seen: set[str] = set()
                for renderer in _walk_video_renderers(data):
                    item = _renderer_to_result(renderer, include_shorts=False)
                    if item and item["video_id"] not in seen:
                        seen.add(item["video_id"])
                        raw.append(item)
            if not raw:
                raw = _parse_search_html(html)
            if raw:
                items = [{
                    "rank": index + 1,
                    "title": item["title"],
                    "channel": item["channel"],
                    "video_id": item["video_id"],
                    "url": item["url"],
                } for index, item in enumerate(raw[:max(1, int(max_results))])]
                break
        except Exception as exc:
            print(f"[YouTube] Trending scrape failed for {endpoint}: {exc}")

    if items:
        _cache_results(items, f"{category} charts {region}")
    return items


# ---------------------------------------------------------------------------
# Persistent Playwright YouTube controller
# ---------------------------------------------------------------------------

_STATE_JS = """() => {
  const v = document.querySelector('video.html5-main-video') || document.querySelector('video');
  if (!v) return {ok: false};
  const titleEl = document.querySelector('.ytp-title-link') || document.querySelector('h1.title');
  const channelEl = document.querySelector('.ytp-title-channel') ||
                    document.querySelector('.ytd-channel-name');
  return {
    ok: true,
    title: ((titleEl && titleEl.textContent) || document.title || '').trim(),
    channel: ((channelEl && channelEl.textContent) || '').trim(),
    currentTime: Number(v.currentTime) || 0,
    duration: Number.isFinite(v.duration) ? v.duration : 0,
    paused: !!v.paused,
    volume: Math.round((Number(v.volume) || 0) * 100),
    muted: !!v.muted,
    speed: Number(v.playbackRate) || 1,
    url: location.href
  };
}"""

_VIDEO_PREFIX = """const v = document.querySelector('video.html5-main-video') ||
            document.querySelector('video');
  if (!v) return {ok: false};"""

_ENV_JS = """() => {
  const v = document.querySelector('video.html5-main-video') || document.querySelector('video');
  const host = (location.hostname || '').toLowerCase();
  const youtube = host === 'youtube.com' || host.endsWith('.youtube.com') ||
                  host === 'youtu.be' || host.endsWith('.youtu.be');
  return {ok: true, hasVideo: !!v, youtube: youtube, url: location.href};
}"""

_TITLE_JS = """() => {
  const selectors = [
    'h1.ytd-watch-metadata yt-formatted-string',
    'h1.style-scope.ytd-watch-metadata',
    '.ytp-title-link', 'h1.title'
  ];
  let title = '';
  for (const selector of selectors) {
    const node = document.querySelector(selector);
    if (node && node.textContent && node.textContent.trim()) {
      title = node.textContent.trim();
      break;
    }
  }
  if (!title) title = (document.title || '').replace(/\\s*-\\s*YouTube\\s*$/i, '').trim();
  return {ok: true, title: title, href: location.href};
}"""

_FEATURE_STATE_JS = """(arg) => {
  const v = document.querySelector('video.html5-main-video') || document.querySelector('video');
  if (!v) return {ok: false};
  let active = false;
  if (arg.name === 'fullscreen') active = !!document.fullscreenElement;
  else if (arg.name === 'theater') active = !!document.querySelector(
      'ytd-watch-flexy[theater], body.ytp-theater-mode');
  else if (arg.name === 'miniplayer') active = !!document.querySelector(
      'ytd-miniplayer, body.ytp-miniplayer-mode');
  else if (arg.name === 'captions') {
    const tracks = v.textTracks || [];
    for (const track of tracks) { if (track.mode === 'showing') active = true; }
  }
  return {ok: true, name: arg.name, enabled: active};
}"""

_PAUSE_JS = """() => {""" + _VIDEO_PREFIX + """
  if (!v.paused) v.pause();
  return {ok: true, paused: !!v.paused, currentTime: Number(v.currentTime) || 0,
          title: (document.querySelector('.ytp-title-link')?.textContent || document.title || '').trim()};
}"""

_RESUME_JS = """async () => {""" + _VIDEO_PREFIX + """
  if (v.paused) {
    try { await v.play(); } catch (e) { return {ok: false, error: String(e)}; }
  }
  return {ok: true, paused: !!v.paused, currentTime: Number(v.currentTime) || 0,
          title: (document.querySelector('.ytp-title-link')?.textContent || document.title || '').trim()};
}"""

_SEEK_JS = """(arg) => {""" + _VIDEO_PREFIX + """
  const duration = Number.isFinite(v.duration) ? v.duration : 0;
  const base = arg.relative ? (Number(v.currentTime) || 0) : 0;
  let target = base + (Number(arg.value) || 0);
  if (target < 0) target = 0;
  if (duration > 0 && target > duration) target = duration;
  v.currentTime = target;
  return {ok: true, currentTime: Number(v.currentTime) || 0, duration: duration,
          paused: !!v.paused};
}"""

_VOLUME_JS = """(arg) => {""" + _VIDEO_PREFIX + """
  if (arg.set !== null && arg.set !== undefined) {
    const level = Math.min(100, Math.max(0, Number(arg.set) || 0));
    v.volume = level / 100;
    v.muted = level === 0;
  }
  if (arg.delta !== null && arg.delta !== undefined) {
    let level = Math.round((Number(v.volume) || 0) * 100) + (Number(arg.delta) || 0);
    level = Math.min(100, Math.max(0, level));
    v.volume = level / 100;
    v.muted = level === 0;
  }
  return {ok: true, volume: Math.round((Number(v.volume) || 0) * 100), muted: !!v.muted};
}"""

_MUTE_JS = """(arg) => {""" + _VIDEO_PREFIX + """
  v.muted = !!arg.muted;
  return {ok: true, muted: !!v.muted, volume: Math.round((Number(v.volume) || 0) * 100)};
}"""

_SPEED_JS = """(arg) => {""" + _VIDEO_PREFIX + """
  const rate = Math.min(4.0, Math.max(0.25, Number(arg.value) || 1));
  v.playbackRate = rate;
  return {ok: true, speed: Number(v.playbackRate) || 1};
}"""

_FEATURE_JS = """(arg) => {""" + _VIDEO_PREFIX + """
  const selectors = {
    fullscreen: '.ytp-fullscreen-button',
    theater: '.ytp-size-button',
    miniplayer: '.ytp-miniplayer-button',
    captions: '.ytp-subtitles-button'
  };
  const button = document.querySelector(selectors[arg.name]);
  if (!button) return {ok: false, name: arg.name};
  const explicit = arg.enabled !== null && arg.enabled !== undefined;
  if (explicit) {
    // Explicit on/off: click only when the current state differs.
    let active = false;
    if (arg.name === 'fullscreen') active = !!document.fullscreenElement;
    else if (arg.name === 'theater') active = !!document.querySelector(
        'ytd-watch-flexy[theater], body.ytp-theater-mode');
    else if (arg.name === 'miniplayer') active = !!document.querySelector(
        'ytd-miniplayer, body.ytp-miniplayer-mode');
    else if (arg.name === 'captions') {
      const tracks = v.textTracks || [];
      for (const track of tracks) { if (track.mode === 'showing') active = true; }
    }
    if (active === !!arg.enabled) return {ok: true, name: arg.name, clicked: false};
  }
  button.click();
  return {ok: true, name: arg.name, clicked: true};
}"""

_SKIP_AD_SELECTORS = (
    ".ytp-skip-ad-button", ".ytp-ad-skip-button-modern", ".ytp-ad-skip-button",
)

_SEARCH_RESULTS_JS = """els => els.map(a => {
  const container = a.closest('ytd-video-renderer') || a.parentElement;
  const channel = container && container.querySelector(
      '#channel-name, ytd-channel-name, .ytd-channel-name');
  return {
    href: a.href || '',
    title: (a.textContent || '').trim(),
    channel: channel ? channel.textContent.trim() : 'Unknown'
  };
})"""


class _YouTubeController:
    """One lazy-initialized asyncio/Playwright thread owning one YouTube tab."""

    def __init__(self):
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
        self._lock = threading.RLock()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._engine = "chromium"
        self._exe_path = None
        self._channel = None
        self._last_url = ""
        self._cdp = False

    # -- thread plumbing -----------------------------------------------------

    def start(self, timeout: float = 15) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        with self._lock:
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._run_loop, daemon=True, name="YouTubeController"
            )
            self._thread.start()
            return bool(self._ready.wait(timeout=timeout))

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._init())
        except Exception as exc:
            print(f"[YouTube] Playwright unavailable: {exc}")
        self._ready.set()
        self._loop.run_forever()

    async def _init(self) -> None:
        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()

    def run(self, coroutine, timeout: float = 35):
        if not self._loop or not self._thread or not self._thread.is_alive():
            raise RuntimeError("YouTube controller thread is not running.")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result(timeout=timeout)

    def is_active(self) -> bool:
        page = self._page
        if page is None:
            return False
        try:
            if page.is_closed():
                return False
        except Exception:
            pass
        return bool(self._loop and self._thread and self._thread.is_alive())

    def current_url(self) -> str:
        return self._last_url if self._last_url else ""

    # -- browser lifecycle ---------------------------------------------------

    async def _launch_browser_if_needed(self):
        """Attach over CDP or launch a persistent, real-browser profile."""
        if self._cdp and self._browser and self._browser.is_connected():
            return self._browser
        if self._context is not None and not self._cdp:
            return self._context
        if self._playwright is None:
            await self._init()

        cdp = _cdp_url()
        if cdp:
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(cdp)
                self._cdp = True
                contexts = getattr(self._browser, "contexts", []) or []
                self._context = contexts[0] if contexts else await self._browser.new_context()
                return self._browser
            except Exception as exc:
                print(f"[YouTube] CDP attach failed ({exc}); using persistent profile")

        profile = _youtube_profile_dir()
        try:
            profile.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            print(f"[YouTube] Could not create browser profile directory: {exc}")

        last_error = None
        for candidate in _browser_launch_candidates():
            launch_kwargs = {
                "user_data_dir": str(profile),
                "headless": False,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    "--autoplay-policy=no-user-gesture-required",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                "ignore_default_args": ["--enable-automation"],
                "viewport": None,
            }
            launch_kwargs.update(candidate)
            try:
                # No user_agent override: Client Hints must match the real browser.
                self._context = await self._playwright.chromium.launch_persistent_context(
                    **launch_kwargs
                )
                await self._context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                self._browser = getattr(self._context, "browser", None)
                self._engine = "chromium"
                self._channel = candidate.get("channel")
                self._exe_path = candidate.get("executable_path")
                return self._browser or self._context
            except Exception as exc:
                last_error = exc
                print(f"[YouTube] Persistent browser launch failed ({exc}); trying next")

        raise RuntimeError(
            "Could not launch a persistent Chromium profile "
            f"(profile may be locked by another process): {last_error}"
        )

    async def _get_page(self):
        await self._launch_browser_if_needed()
        if self._page is None or self._page.is_closed():
            pages = []
            if self._context is not None:
                try:
                    pages = list(getattr(self._context, "pages", []) or [])
                except Exception:
                    pages = []
            for candidate in pages:
                try:
                    if not candidate.is_closed() and "youtube" in str(candidate.url).lower():
                        self._page = candidate
                        break
                except Exception:
                    continue
            if self._page is None or self._page.is_closed():
                if pages and not pages[0].is_closed():
                    self._page = pages[0]
                elif self._context is not None:
                    self._page = await self._context.new_page()
                else:
                    self._page = await self._browser.new_page()
        return self._page

    async def _guard(self, page):
        """Return (page, None) only for a real YouTube page with a <video>."""
        if await _is_blocked_page(page):
            return None, CAPTCHA_MESSAGE
        try:
            env = await page.evaluate(_ENV_JS) or {}
        except Exception:
            env = {}
        if not env.get("hasVideo") or not env.get("youtube"):
            return None, NO_VIDEO_MESSAGE
        return page, None

    async def safe_current_url(self) -> str:
        """Current watch URL only when it is YouTube and not a /sorry page."""
        page = self._page
        if page is None:
            return ""
        try:
            if page.is_closed():
                return ""
        except Exception:
            pass
        if await _is_blocked_page(page):
            return ""
        url = str(getattr(page, "url", "") or self._last_url or "")
        return url if _is_youtube_page(url) and _extract_video_id(url) else ""

    async def _dismiss_popups(self, page) -> None:
        """Dismiss consent/cookie and sign-in nags; never fail if absent."""
        for label in ("Accept all", "Reject all", "I agree", "Accept the use of cookies"):
            try:
                button = page.get_by_role("button", name=label).first
                if await button.count() and await button.is_visible():
                    await button.click(timeout=2000)
                    break
            except Exception:
                continue
        for selector in (
            "ytd-consent-bump-v2-lightbox button[aria-label='Close']",
            "button[aria-label='Close']",
            "ytd-signin-promoShelfRenderer button",
        ):
            try:
                button = page.locator(selector).first
                if await button.count() and await button.is_visible():
                    await button.click(timeout=1500)
                    break
            except Exception:
                continue

    async def _skip_ad_if_available(self, page, wait_ms: int = 0) -> bool:
        try:
            if wait_ms:
                await page.wait_for_timeout(wait_ms)
        except Exception:
            pass
        for selector in _SKIP_AD_SELECTORS:
            try:
                button = page.locator(selector).first
                if await button.count() and await button.is_visible():
                    await button.click(timeout=2000)
                    return True
            except Exception:
                continue
        return False

    async def _goto(self, url: str) -> str:
        page = await self._get_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        self._last_url = url
        await self._dismiss_popups(page)
        try:
            await page.bring_to_front()
        except Exception:
            pass
        return url

    async def _state(self, page=None):
        page = page or await self._get_page()
        try:
            return await page.evaluate(_STATE_JS) or {}
        except Exception as exc:
            print(f"[YouTube] Player state failed: {exc}")
            return {}

    # -- user-facing controller operations ----------------------------------

    async def open(self, url: str | None = None) -> str:
        target = url if url and _is_valid_youtube_url(url) else YOUTUBE_HOME
        await self._goto(target)
        page = await self._get_page()
        if await _is_blocked_page(page):
            return CAPTCHA_MESSAGE
        return "Opened YouTube." if target == YOUTUBE_HOME else f"Opened {target}."

    async def play_url(self, url: str, start_seconds=None, title=None, channel=None) -> str:
        """Play a resolved URL; ``title`` comes from the search result if known."""
        return await self._play_url(
            url, title=title, channel=channel, start_seconds=start_seconds
        )

    async def _play_url(self, url: str, title=None, channel=None, start_seconds=None) -> str:
        target = _append_timestamp(url, start_seconds)
        await self._goto(target)
        page = await self._get_page()
        if await _is_blocked_page(page):
            return CAPTCHA_MESSAGE

        try:
            await page.wait_for_selector(
                "video.html5-main-video, video", timeout=5000, state="attached"
            )
        except Exception:
            if await _is_blocked_page(page):
                return CAPTCHA_MESSAGE
            return NO_VIDEO_MESSAGE

        page, error = await self._guard(page)
        if error:
            return error

        try:
            await page.evaluate(_RESUME_JS)  # ensure playback actually starts
        except Exception:
            pass
        await self._skip_ad_if_available(page, wait_ms=2000)

        try:
            title_info = await page.evaluate(_TITLE_JS) or {}
        except Exception:
            title_info = {}
        state = await self._state(page)
        live_title = str(title_info.get("title") or "").strip()
        resolved_title = str(title or "").strip()
        final_title = live_title or resolved_title or str(state.get("title") or "").strip()
        if not final_title:
            return NO_VIDEO_MESSAGE

        _remember_played_video({
            "video_id": _extract_video_id(target) or "",
            "title": final_title,
            "channel": str(channel or state.get("channel") or ""),
            "url": target,
        })
        return f"Playing '{final_title}'."

    async def search_dom(self, query: str, max_results: int = 20) -> list[dict]:
        """Live-search fallback for blocked HTTP requests."""
        await self._goto(SEARCH_ENDPOINT.format(query=quote_plus(query)))
        page = await self._get_page()
        if await _is_blocked_page(page):
            print("[YouTube] Search page is blocked by a Google verification")
            return []
        try:
            await page.wait_for_selector("a#video-title", timeout=10000)
        except Exception:
            pass
        try:
            entries = await page.eval_on_selector_all("a#video-title", _SEARCH_RESULTS_JS) or []
        except Exception:
            entries = []
        results: list[dict] = []
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            href = str(entry.get("href") or "")
            video_id = _extract_video_id(href)
            if not video_id or video_id in seen or "/shorts/" in href:
                continue
            seen.add(video_id)
            results.append({
                "video_id": video_id,
                "title": str(entry.get("title") or "Untitled"),
                "channel": str(entry.get("channel") or "Unknown"),
                "duration": "",
                "views": "",
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "is_live": False,
                "is_short": False,
            })
        results = results[:max_results]
        if results:
            _cache_results(results, query)
        return results

    @staticmethod
    def _state_line(state: dict, verb: str) -> str:
        title = state.get("title") or _last_played_video().get("title") or "the video"
        return f"{verb} '{title}' at {_format_time(state.get('currentTime', 0))}."

    async def pause(self) -> str:
        page = await self._get_page()
        page, error = await self._guard(page)
        if error:
            return error
        try:
            result = await page.evaluate(_PAUSE_JS) or {}
        except Exception:
            result = {}
        if not result.get("ok"):
            try:
                await page.keyboard.press("k")
            except Exception:
                pass
            result = await self._state(page)
            if not result.get("ok"):
                return NO_VIDEO_MESSAGE
        after = await self._state(page)
        return self._state_line(after or result, "Paused")

    async def resume(self) -> str:
        page = await self._get_page()
        page, error = await self._guard(page)
        if error:
            return error
        try:
            result = await page.evaluate(_RESUME_JS) or {}
        except Exception:
            result = {}
        if not result.get("ok"):
            try:
                await page.keyboard.press("k")
            except Exception:
                pass
            result = await self._state(page)
            if not result.get("ok"):
                return NO_VIDEO_MESSAGE
        after = await self._state(page)
        return self._state_line(after or result, "Resumed")

    async def toggle(self) -> str:
        page = await self._get_page()
        page, error = await self._guard(page)
        if error:
            return error
        state = await self._state(page)
        if not state.get("ok"):
            return NO_VIDEO_MESSAGE
        was_paused = bool(state.get("paused", True))
        result = await (self.resume() if was_paused else self.pause())
        if result in (CAPTCHA_MESSAGE, NO_VIDEO_MESSAGE):
            return result
        after = await self._state(page)
        if not after.get("ok"):
            return NO_VIDEO_MESSAGE
        return self._state_line(after, "Paused" if after.get("paused") else "Playing")

    async def seek(self, value: float = 0, relative: bool = False) -> str:
        page = await self._get_page()
        page, error = await self._guard(page)
        if error:
            return error
        try:
            result = await page.evaluate(
                _SEEK_JS, {"value": float(value), "relative": bool(relative)}
            ) or {}
        except Exception:
            result = {}
        if not result.get("ok"):
            key = "l" if relative and value > 0 else "j" if relative and value < 0 else None
            if key:
                try:
                    for _ in range(max(1, min(20, int(abs(value) // 10)))):
                        await page.keyboard.press(key)
                except Exception:
                    pass
        after = await self._state(page)
        if not after.get("ok") and not result.get("ok"):
            return NO_VIDEO_MESSAGE
        actual = after.get("currentTime", result.get("currentTime", 0))
        return f"Seeked to {_format_time(actual)}."

    async def volume(self, level=None, delta=None) -> str:
        page = await self._get_page()
        page, error = await self._guard(page)
        if error:
            return error
        payload = {"set": None, "delta": None}
        if level is not None:
            payload["set"] = _number(level, 0)
        if delta is not None:
            payload["delta"] = _number(delta, 0)
        try:
            result = await page.evaluate(_VOLUME_JS, payload) or {}
        except Exception:
            result = {}
        if not result.get("ok"):
            if delta is not None and delta > 0:
                try:
                    await page.keyboard.press("volumeup")
                except Exception:
                    pass
            elif delta is not None and delta < 0:
                try:
                    await page.keyboard.press("volumedown")
                except Exception:
                    pass
        after = await self._state(page)
        if not after.get("ok") and not result.get("ok"):
            return NO_VIDEO_MESSAGE
        percent = int(after.get("volume", result.get("volume", 0)))
        muted = bool(after.get("muted", result.get("muted", False)))
        return f"Volume set to {percent}%{' (muted)' if muted else ''}."

    async def mute(self, muted: bool = True) -> str:
        page = await self._get_page()
        page, error = await self._guard(page)
        if error:
            return error
        try:
            result = await page.evaluate(_MUTE_JS, {"muted": bool(muted)}) or {}
        except Exception:
            result = {}
        if not result.get("ok"):
            try:
                await page.keyboard.press("m")
            except Exception:
                pass
        after = await self._state(page)
        if not after.get("ok") and not result.get("ok"):
            return NO_VIDEO_MESSAGE
        return "Muted." if after.get("muted", result.get("muted", muted)) else "Unmuted."

    async def speed(self, rate: float = 1.0) -> str:
        page = await self._get_page()
        page, error = await self._guard(page)
        if error:
            return error
        rate = _clamp(_number(rate, 1.0), 0.25, 4.0)
        try:
            result = await page.evaluate(_SPEED_JS, {"value": rate}) or {}
        except Exception:
            result = {}
        after = await self._state(page)
        if not after.get("ok") and not result.get("ok"):
            return NO_VIDEO_MESSAGE
        actual = _number(after.get("speed", result.get("speed")), rate)
        return f"Playback speed set to {actual:g}x."

    async def _click_player_button(self, page, selectors, shortcuts):
        for selector in selectors:
            try:
                button = page.locator(selector).first
                if await button.count() and await button.is_visible():
                    await button.click(timeout=3000)
                    return True
            except Exception:
                continue
        for key in shortcuts:
            try:
                await page.keyboard.press(key)
                return True
            except Exception:
                continue
        return False

    async def _navigate_playlist(self, direction: str) -> str:
        page = await self._get_page()
        page, error = await self._guard(page)
        if error:
            return error
        before = await self._state(page)
        if direction == "next":
            acted = await self._click_player_button(page, (".ytp-next-button",), ("Shift+N",))
            verb = "next"
        else:
            acted = await self._click_player_button(page, (".ytp-prev-button",), ("Shift+P",))
            verb = "previous"
        if not acted:
            return f"No {verb} video is available, sir."
        for _ in range(2):
            try:
                await page.wait_for_timeout(600)
            except Exception:
                break
            after = await self._state(page)
            if after.get("url") != before.get("url") or after.get("title") != before.get("title"):
                return f"Playing {verb} video: {after.get('title') or 'unknown title'}."
        return f"No {verb} video is available, sir."

    async def next(self) -> str:
        return await self._navigate_playlist("next")

    async def previous(self) -> str:
        return await self._navigate_playlist("previous")

    async def feature(self, name: str, enabled=None) -> str:
        page = await self._get_page()
        page, error = await self._guard(page)
        if error:
            return error
        key = {"fullscreen": "f", "theater": "t", "miniplayer": "i",
               "captions": "c"}.get(name, "f")
        try:
            clicked = await page.evaluate(
                _FEATURE_JS, {"name": name, "enabled": enabled}
            ) or {}
        except Exception:
            clicked = {}
        if not clicked.get("ok"):
            try:
                await page.keyboard.press(key)
            except Exception:
                pass
        try:
            await page.wait_for_timeout(250)
        except Exception:
            pass
        try:
            state = await page.evaluate(_FEATURE_STATE_JS, {"name": name}) or {}
        except Exception:
            state = {}
        if not state.get("ok"):
            return f"Could not verify {name} state in the player, sir."
        return f"{name.capitalize()} {'on' if state.get('enabled') else 'off'}."

    async def skip_ad(self, wait_ms: int = 0) -> str:
        page = await self._get_page()
        page, error = await self._guard(page)
        if error:
            return error
        clicked = await self._skip_ad_if_available(page, wait_ms=wait_ms)
        return "Skipped ad." if clicked else "No skippable ad found."

    async def now_playing(self) -> str:
        page = await self._get_page()
        page, error = await self._guard(page)
        if error:
            return error
        state = await self._state(page)
        if not state.get("ok"):
            return NO_VIDEO_MESSAGE
        remembered = _last_played_video()
        title = state.get("title") or remembered.get("title") or "unknown title"
        channel = state.get("channel") or remembered.get("channel")
        line = f"Now playing: '{title}'"
        if channel:
            line += f" by {channel}"
        state_word = "paused" if state.get("paused") else "playing"
        line += (
            f". {state_word.capitalize()} at "
            f"{_format_time(state.get('currentTime', 0))} / "
            f"{_format_time(state.get('duration', 0))}. "
            f"Volume {int(state.get('volume', 0))}%"
        )
        if state.get("muted"):
            line += " (muted)"
        line += f". Speed {_number(state.get('speed'), 1.0):g}x."
        if state.get("url"):
            line += f" {state['url']}"
        return line

    async def close(self) -> str:
        # A persistent context owns the browser; close it instead of a missing
        # context.browser handle. For CDP this only detaches from the user's browser.
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        elif self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        self._browser = None
        self._context = None
        self._page = None
        self._cdp = False
        self._last_url = ""
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._playwright = None
        return "YouTube player closed."


# ---------------------------------------------------------------------------
# Controller singleton and external-browser fallback
# ---------------------------------------------------------------------------

_CONTROLLER_LOCK = threading.Lock()
_yt_controller: _YouTubeController | None = None


def _get_controller(create: bool = True):
    """Return the shared controller, starting its thread only when requested.

    In native mode Playwright is bypassed completely so FRIDAY always uses the
    user's real, signed-in default browser.
    """
    global _yt_controller
    if _native_mode():
        return None
    with _CONTROLLER_LOCK:
        if _yt_controller is None and create:
            _yt_controller = _YouTubeController()
        controller = _yt_controller
    if controller is not None and create:
        try:
            controller.start()
        except Exception as exc:
            print(f"[YouTube] Could not start controller: {exc}")
            return None
    return controller


def _external_browser_fallback(action: str, params: dict) -> str:
    """Drive an existing external YouTube window with standard shortcuts."""
    shortcuts = {
        "pause": "k", "resume": "k", "toggle_play": "k", "mute": "m",
        "unmute": "m", "forward": "l", "rewind": "j", "next": "shift+n",
        "previous": "shift+p", "fullscreen": "f", "captions": "c",
    }
    key = shortcuts.get(action)
    window = None
    try:
        import pygetwindow as gw
        candidates = [
            win for win in gw.getWindowsWithTitle("YouTube")
            if "youtube" in str(getattr(win, "title", "")).lower()
        ]
        if candidates:
            window = candidates[0]
            window.activate()
    except Exception as exc:
        print(f"[YouTube] No accessible YouTube window: {exc}")

    if window is not None and key:
        try:
            import pyautogui
            if "+" in key:
                pyautogui.hotkey(*key.split("+"))
            else:
                pyautogui.press(key)
            return f"Sent '{key}' to the YouTube window."
        except Exception as exc:
            return f"Found a YouTube window but could not send '{key}': {exc}"

    media_actions = {
        "pause": "toggle", "resume": "toggle", "toggle_play": "toggle",
        "mute": "mute", "unmute": "unmute", "next": "next", "previous": "prev",
    }
    media_action = media_actions.get(action)
    if media_action:
        try:
            from actions import media_control
            result = media_control.media_control_action({"action": media_action})
            return f"No FRIDAY YouTube player detected; used media controls. {result}"
        except Exception:
            pass
    return "No YouTube window is open and FRIDAY's YouTube player is not active, sir."


def _active_controller(action: str, params: dict):
    """Return (controller, error_message) for deterministic control actions."""
    controller = _get_controller(create=False)
    if controller is not None and controller.is_active():
        return controller, None
    return None, _external_browser_fallback(action, params)


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _handle_open(params: dict, player=None, speak=None) -> str:
    url = str(params.get("url") or "").strip()
    controller = _get_controller()
    if controller is None:
        target = url if _is_valid_youtube_url(url) else YOUTUBE_HOME
        webbrowser.open(target)
        return f"Opened YouTube in your default browser: {target}"
    try:
        return controller.run(controller.open(url))
    except Exception as exc:
        print(f"[YouTube] Playwright open failed: {exc}")
        target = url if _is_valid_youtube_url(url) else YOUTUBE_HOME
        webbrowser.open(target)
        return f"Opened YouTube in your default browser: {target}"


def _format_search_results(results: list[dict], heading: str) -> str:
    lines = [heading]
    for position, item in enumerate(results[:5], start=1):
        detail = " • ".join(
            part for part in (item.get("duration"), item.get("channel"),
                              item.get("views")) if part
        )
        suffix = f" ({detail})" if detail else ""
        lines.append(f"{item.get('rank', position)}. {item.get('title', 'Untitled')}{suffix}")
    return "\n".join(lines)


def _handle_search(params: dict, player=None, speak=None) -> str:
    query = str(params.get("query") or "").strip()
    if not query:
        return "Tell me what you'd like to search for on YouTube, sir."
    include_shorts = bool(params.get("include_shorts", False))
    results = _search_youtube_videos(query, include_shorts=include_shorts)
    browser_failed = False
    if not results:
        controller = _get_controller(create=False)
        if controller is not None:
            try:
                controller.start()
                results = controller.run(controller.search_dom(query))
            except Exception as exc:
                browser_failed = True
                print(f"[YouTube] DOM search fallback failed: {exc}")
    if not results:
        if (_native_mode() or browser_failed or params.get("open")
                or params.get("open_results")):
            webbrowser.open(SEARCH_ENDPOINT.format(query=quote_plus(query)))
            return f"Opened YouTube search for '{query}' in your browser."
        return f"No YouTube results found for '{query}', sir."
    if _native_mode():
        # Native mode never starts Playwright: open the resolved result directly.
        webbrowser.open(results[0]["url"])
    if params.get("open") or params.get("open_results"):
        controller = _get_controller()
        if controller is not None:
            try:
                controller.run(controller.open(
                    SEARCH_ENDPOINT.format(query=quote_plus(query))
                ))
            except Exception:
                webbrowser.open(SEARCH_ENDPOINT.format(query=quote_plus(query)))
    result = _format_search_results(results, f"Top YouTube results for '{query}':")
    if speak:
        speak(f"Found {len(results)} YouTube results. The first is {results[0]['title']}, sir.")
    return result


def _handle_play(params: dict, player=None, speak=None) -> str:
    query = str(params.get("query") or "").strip()
    url = str(params.get("url") or "").strip()
    index_num = _number(params.get("index"), 1)
    index = max(1, int(index_num)) if index_num is not None else 1
    include_shorts = bool(params.get("include_shorts", False))
    seconds = _parse_timestamp(params.get("timestamp"))
    if seconds is None:
        seconds = _parse_timestamp(params.get("seconds"))

    chosen_url = ""
    chosen_title = ""
    chosen_channel = ""
    if url:
        if not _is_valid_youtube_url(url):
            return "That doesn't appear to be a valid YouTube URL, sir."
        chosen_url = url
    elif query:
        results = _search_youtube_videos(query, include_shorts=include_shorts,
                                         max_results=max(index, 20))
        if not results:
            controller = _get_controller(create=False)
            if controller is not None:
                try:
                    controller.start()
                    results = controller.run(controller.search_dom(query))
                except Exception as exc:
                    print(f"[YouTube] DOM search fallback failed: {exc}")
        if not results:
            webbrowser.open(SEARCH_ENDPOINT.format(query=quote_plus(query)))
            return f"I couldn't resolve '{query}', so I opened the YouTube search page."
        if index > len(results):
            return f"I only found {len(results)} results for '{query}', sir."
        chosen_url = results[index - 1]["url"]
        chosen_title = str(results[index - 1].get("title") or "")
        chosen_channel = str(results[index - 1].get("channel") or "")
    elif params.get("index") is not None or params.get("position") is not None:
        cached = _get_cached_results()
        if not cached:
            return "No recent YouTube results to choose from, sir. Search first."
        if index > len(cached):
            return f"I only have {len(cached)} results from the last search, sir."
        chosen_url = cached[index - 1].get("url") or cached[index - 1]["video_id"]
        chosen_title = str(cached[index - 1].get("title") or "")
        chosen_channel = str(cached[index - 1].get("channel") or "")
    else:
        controller = _get_controller(create=False)
        if controller is not None and controller.is_active():
            try:
                return controller.run(controller.resume())
            except Exception as exc:
                return f"Could not resume the current video: {exc}"
        return "Please tell me what you'd like to watch, sir."

    chosen_url = _canonical_watch_url(chosen_url)
    target = _append_timestamp(chosen_url, seconds)
    controller = _get_controller()
    if controller is not None:
        try:
            return controller.run(controller.play_url(
                chosen_url, seconds, title=chosen_title, channel=chosen_channel
            ))
        except Exception as exc:
            print(f"[YouTube] Managed playback failed: {exc}")
    webbrowser.open(target)
    return f"Opened YouTube video in your browser: {target}"


def _control(name: str, params: dict, method: str, *args, **kwargs) -> str:
    controller, error = _active_controller(name, params)
    if controller is None:
        return error
    try:
        return controller.run(getattr(controller, method)(*args, **kwargs))
    except Exception as exc:
        print(f"[YouTube] {name} failed: {exc}")
        return f"Could not {name.replace('_', ' ')} the video, sir: {exc}"


def _handle_pause(params: dict, player=None, speak=None) -> str:
    return _control("pause", params, "pause")


def _handle_resume(params: dict, player=None, speak=None) -> str:
    return _control("resume", params, "resume")


def _handle_toggle_play(params: dict, player=None, speak=None) -> str:
    return _control("toggle_play", params, "toggle")


def _handle_seek(params: dict, player=None, speak=None) -> str:
    controller, error = _active_controller("seek", params)
    if controller is None:
        return error
    raw = params.get("offset")
    relative = raw is not None
    if raw is None:
        raw = params.get("timestamp")
        if raw is None:
            raw = params.get("seconds")
        relative = _is_relative_timestamp(raw)
    value = _parse_timestamp(raw)
    if value is None:
        return "Please give me a timestamp like 1:30 or a number of seconds, sir."
    return controller.run(controller.seek(value, relative))


def _handle_relative_seek(direction: int, params: dict, player=None, speak=None) -> str:
    if params.get("offset") is not None or params.get("seconds") is not None:
        raw = params.get("offset") if params.get("offset") is not None else params.get("seconds")
        value = _parse_timestamp(raw)
        if value is None:
            return "That offset isn't a valid number of seconds, sir."
        value = abs(value) * direction
    elif params.get("timestamp") is not None:
        value = _parse_timestamp(params["timestamp"])
        if value is None:
            return "That timestamp isn't valid, sir."
        value = abs(value) * direction
    else:
        value = 10.0 * direction
    return _control("forward" if direction > 0 else "rewind", params,
                    "seek", value, True)


def _handle_forward(params: dict, player=None, speak=None) -> str:
    return _handle_relative_seek(1, params, player, speak)


def _handle_rewind(params: dict, player=None, speak=None) -> str:
    return _handle_relative_seek(-1, params, player, speak)


def _handle_restart(params: dict, player=None, speak=None) -> str:
    return _control("restart", params, "seek", 0, False)


def _handle_next(params: dict, player=None, speak=None) -> str:
    return _control("next", params, "next")


def _handle_previous(params: dict, player=None, speak=None) -> str:
    return _control("previous", params, "previous")


def _handle_volume(params: dict, player=None, speak=None) -> str:
    controller, error = _active_controller("volume", params)
    if controller is None:
        return error
    delta = params.get("_volume_delta")
    if delta is None:
        raw = params.get("volume", params.get("level"))
        if raw is None:
            return controller.run(controller.volume())
        level = _number(raw)
        if level is None:
            return "Please give a volume from 0 to 100, sir."
        return controller.run(controller.volume(level=_clamp(level, 0, 100)))
    return controller.run(controller.volume(delta=float(delta)))


def _handle_mute(params: dict, player=None, speak=None) -> str:
    return _control("mute", params, "mute", True)


def _handle_unmute(params: dict, player=None, speak=None) -> str:
    return _control("unmute", params, "mute", False)


def _handle_speed(params: dict, player=None, speak=None) -> str:
    controller, error = _active_controller("speed", params)
    if controller is None:
        return error
    raw = params.get("speed", params.get("rate"))
    if raw is None:
        state = controller.run(controller._state())
        return f"Playback speed is {_number(state.get('speed'), 1.0):g}x."
    rate = _number(raw)
    if rate is None:
        return "Please give a playback speed between 0.25 and 4, sir."
    return controller.run(controller.speed(_clamp(rate, 0.25, 4.0)))


def _handle_feature(name: str, params: dict) -> str:
    controller, error = _active_controller(name, params)
    if controller is None:
        return error
    enabled = params.get("enabled", params.get("on"))
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in ("true", "yes", "on", "1")
    elif enabled is not None:
        enabled = bool(enabled)
    return controller.run(controller.feature(name, enabled))


def _handle_fullscreen(params: dict, player=None, speak=None) -> str:
    return _handle_feature("fullscreen", params)


def _handle_theater(params: dict, player=None, speak=None) -> str:
    return _handle_feature("theater", params)


def _handle_miniplayer(params: dict, player=None, speak=None) -> str:
    return _handle_feature("miniplayer", params)


def _handle_captions(params: dict, player=None, speak=None) -> str:
    return _handle_feature("captions", params)


def _handle_skip_ad(params: dict, player=None, speak=None) -> str:
    controller, error = _active_controller("skip_ad", params)
    if controller is None:
        return error if "window" in error.lower() else \
            "No YouTube player is active, so there is no ad to skip, sir."
    return controller.run(controller.skip_ad(wait_ms=2000))


def _handle_now_playing(params: dict, player=None, speak=None) -> str:
    controller = _get_controller(create=False)
    if controller is None or not controller.is_active():
        return "No YouTube video is currently playing in FRIDAY's player, sir."
    try:
        result = controller.run(controller.now_playing())
    except Exception as exc:
        return f"Could not read player status: {exc}"
    if speak:
        speak(result)
    return result


def _resolve_info_url(params: dict) -> str | None:
    url = str(params.get("url") or "").strip()
    if url:
        if "/sorry" in url.lower() or not _is_valid_youtube_url(url):
            return None
        return url
    query = str(params.get("query") or "").strip()
    if query:
        results = _search_youtube_videos(query, max_results=1)
        if results:
            return results[0]["url"]
    controller = _get_controller(create=False)
    if controller is not None and controller.is_active():
        try:
            # safe_current_url() rejects /sorry interstitials and other hosts.
            current = controller.run(controller.safe_current_url())
            if current and _is_valid_youtube_url(current):
                return current
        except Exception as exc:
            print(f"[YouTube] Could not read active tab URL: {exc}")
    remembered = _last_played_video().get("url", "")
    return remembered if remembered and _is_valid_youtube_url(remembered) else None


def _handle_summarize(params: dict, player=None, speak=None) -> str:
    url = _resolve_info_url(params)
    if not url:
        return "Give me a YouTube URL or a search query and I'll summarise it, sir."
    if not _is_valid_youtube_url(url) or not _extract_video_id(url):
        return "That doesn't appear to be a valid YouTube video URL, sir."
    if not _TRANSCRIPT_OK:
        return "youtube-transcript-api is not installed. Run: pip install youtube-transcript-api"

    video_id = _extract_video_id(url)
    if player:
        player.write_log(f"[YouTube] Summarizing: {url}")
    if speak:
        speak("Fetching the transcript now, sir. One moment.")

    transcript = _get_transcript(video_id)
    if not transcript:
        return "I couldn't retrieve a transcript for that video, sir."
    if speak:
        speak("Transcript retrieved. Generating summary now.")
    try:
        summary = _summarize_with_gemini(transcript, url)
    except Exception as exc:
        return f"Summary generation failed, sir: {exc}"
    if speak:
        speak(summary)
    if params.get("save"):
        return f"Summary complete and saved to Desktop: {_save_to_notepad(summary, url)}"
    return summary


def _handle_get_info(params: dict, player=None, speak=None) -> str:
    url = _resolve_info_url(params)
    if not url:
        return "Give me a YouTube URL or a search query and I'll look it up, sir."
    video_id = _extract_video_id(url)
    if not video_id:
        return "Could not extract a video ID from that URL, sir."
    if player:
        player.write_log(f"[YouTube] Getting info: {url}")
    info = _scrape_video_info(video_id)
    if not info or not any(
        key in info for key in ("title", "channel", "views", "duration", "likes")
    ):
        return "Could not retrieve video information, sir."
    lines = [
        f"{key.capitalize()}: {info[key]}"
        for key in ("title", "channel", "views", "duration", "likes")
        if key in info
    ]
    result = "\n".join(lines)
    if speak:
        speak(f"Here's the video info, sir. {result.replace(chr(10), '. ')}")
    return result


def _handle_trending(params: dict, player=None, speak=None) -> str:
    region = params.get("region") or "IN"
    category = str(params.get("category") or "general").strip().lower()
    if player:
        player.write_log(f"[YouTube] Charts: {category} / {region}")
    items = _scrape_trending(region=region, max_results=8, category=category)
    if not items:
        return f"Could not fetch {category} charts for {region}, sir."
    heading = f"Top {category} charts in {str(region).upper()}:"
    result = "\n".join(
        f"{item['rank']}. {item['title']} — {item['channel']}" for item in items
    )
    if speak:
        speak(f"Here are the top {category} charts, sir. " + ". ".join(
            f"Number {item['rank']}: {item['title']} by {item['channel']}"
            for item in items[:3]
        ))
    return f"{heading}\n{result}"


def _handle_close(params: dict, player=None, speak=None) -> str:
    global _yt_controller
    controller = _get_controller(create=False)
    if controller is None:
        return "YouTube player is already closed, sir."
    try:
        result = controller.run(controller.close())
    except Exception as exc:
        result = f"Could not fully close the YouTube player: {exc}"
    with _CONTROLLER_LOCK:
        if _yt_controller is controller:
            _yt_controller = None
    return result


# ---------------------------------------------------------------------------
# Action aliases and dispatcher
# ---------------------------------------------------------------------------

_ACTION_ALIASES = {
    "open": "open", "launch": "open", "home": "open",
    "search": "search", "find": "search", "lookup": "search",
    "play": "play", "watch": "play", "start": "play",
    "pause": "pause", "hold": "pause",
    "resume": "resume", "unpause": "resume", "continue": "resume",
    "toggle": "toggle_play", "toggle_play": "toggle_play",
    "play_pause": "toggle_play", "playpause": "toggle_play",
    "seek": "seek", "jump": "seek", "goto": "seek", "timestamp": "seek",
    "forward": "forward", "skip_forward": "forward",
    "fast_forward": "forward", "ff": "forward",
    "rewind": "rewind", "backward": "rewind", "skip_back": "rewind",
    "back": "rewind", "restart": "restart", "replay": "restart",
    "from_start": "restart",
    "next": "next", "next_video": "next", "skip": "next",
    "previous": "previous", "prev": "previous", "prev_video": "previous",
    "volume": "volume", "set_volume": "volume",
    "volume_up": "volume", "vol_up": "volume",
    "volume_down": "volume", "vol_down": "volume",
    "mute": "mute", "unmute": "unmute",
    "speed": "speed", "playback_speed": "speed",
    "playback_rate": "speed", "rate": "speed",
    "fullscreen": "fullscreen", "full_screen": "fullscreen",
    "theater": "theater", "theatre": "theater", "cinema": "theater",
    "miniplayer": "miniplayer", "mini_player": "miniplayer", "pip": "miniplayer",
    "captions": "captions", "subtitles": "captions", "cc": "captions",
    "skip_ad": "skip_ad", "skip_ads": "skip_ad", "ad": "skip_ad",
    "status": "now_playing", "now_playing": "now_playing",
    "current": "now_playing", "what_is_playing": "now_playing",
    "summarize": "summarize", "summary": "summarize", "tldr": "summarize",
    "get_info": "get_info", "info": "get_info", "details": "get_info",
    "trending": "trending", "charts": "trending", "popular": "trending",
    "close": "close", "stop": "close", "exit": "close", "quit": "close",
}

_VOLUME_ALIASES = {
    "volume_up": 10, "vol_up": 10,
    "volume_down": -10, "vol_down": -10,
}


def _normalize_action(params: dict) -> str:
    """Canonicalise voice/LLM synonyms and attach relative deltas."""
    raw = str(params.get("action") or "play").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in _VOLUME_ALIASES:
        params["_volume_delta"] = _VOLUME_ALIASES[raw]
    if raw in ("skip_forward", "fast_forward", "ff"):
        params["_seek_delta"] = 10
    if raw in ("backward", "skip_back", "back"):
        params["_seek_delta"] = -10
    return _ACTION_ALIASES.get(raw, raw)


_ACTION_MAP = {
    "open": _handle_open,
    "search": _handle_search,
    "play": _handle_play,
    "pause": _handle_pause,
    "resume": _handle_resume,
    "toggle_play": _handle_toggle_play,
    "seek": _handle_seek,
    "forward": _handle_forward,
    "rewind": _handle_rewind,
    "restart": _handle_restart,
    "next": _handle_next,
    "previous": _handle_previous,
    "volume": _handle_volume,
    "mute": _handle_mute,
    "unmute": _handle_unmute,
    "speed": _handle_speed,
    "fullscreen": _handle_fullscreen,
    "theater": _handle_theater,
    "miniplayer": _handle_miniplayer,
    "captions": _handle_captions,
    "skip_ad": _handle_skip_ad,
    "now_playing": _handle_now_playing,
    "summarize": _handle_summarize,
    "get_info": _handle_get_info,
    "trending": _handle_trending,
    "close": _handle_close,
}
# Legacy callers may look aliases up directly in _ACTION_MAP.
for _alias, _canonical in _ACTION_ALIASES.items():
    _ACTION_MAP.setdefault(_alias, _ACTION_MAP[_canonical])


def youtube_video(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    """Unified YouTube command entry point used by the FRIDAY tool registry."""
    params = dict(parameters or {})
    action = _normalize_action(params)

    if player:
        try:
            player.write_log(f"[YouTube] Action: {action}")
        except Exception:
            pass
    print(f"[YouTube] Action: {action}  Params: {params}")

    handler = _ACTION_MAP.get(action)
    if handler is None:
        available = ", ".join(sorted(set(_ACTION_ALIASES.values())))
        return f"Unknown YouTube action: '{action}'. Available: {available}."

    try:
        return handler(params, player, speak) or "Done."
    except concurrent.futures.TimeoutError:
        return f"YouTube {action} timed out, sir."
    except Exception as exc:
        print(f"[YouTube] Error in {action}: {exc}")
        return f"YouTube {action} failed, sir: {exc}"
