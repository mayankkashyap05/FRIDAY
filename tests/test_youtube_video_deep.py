"""100% offline deep tests for the YouTube automation engine.

HTTP, Playwright, Gemini, transcripts, browser windows and the media-control
fallback are all mocked — no network or GUI is ever touched.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from actions import youtube_video as yt


VIDEO_ID = "dQw4w9WgXcQ"


@pytest.fixture(autouse=True)
def _clean_youtube_state(monkeypatch):
    """Keep the module-level search cache and controller singleton isolated."""
    with yt._SEARCH_CACHE_LOCK:
        previous = yt._LAST_SEARCH_RESULTS[:]
        yt._LAST_SEARCH_RESULTS.clear()
    monkeypatch.setattr(yt, "_yt_controller", None)
    yield
    yt._cache_results(previous)


class FakeController:
    """Drop-in controller used by handler-level tests."""

    def __init__(self, active: bool = True, url: str = ""):
        self.active = active
        self._url = url
        self.calls: list[tuple] = []
        self.dom_results: list[dict] = []

    # -- plumbing ------------------------------------------------------------
    def is_active(self) -> bool:
        return self.active

    def current_url(self) -> str:
        return self._url

    def start(self) -> bool:
        return True

    def run(self, awaited):
        """Fakes return plain strings, so 'run' just forwards them."""
        return awaited

    def _call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    # -- operations ----------------------------------------------------------
    def open(self, url=None):
        self._call("open", url)
        return f"Opened {url or 'YouTube'}."

    def play_url(self, url, start_seconds=None):
        self._call("play_url", url, start_seconds)
        return "Playing 'Test Video'."

    def search_dom(self, query):
        self._call("search_dom", query)
        return self.dom_results

    def pause(self):
        self._call("pause")
        return "Paused 'Test Video' at 0:30."

    def resume(self):
        self._call("resume")
        return "Resumed 'Test Video' at 0:30."

    def toggle(self):
        self._call("toggle")
        return "Playing 'Test Video' at 0:30."

    def seek(self, value, relative=False):
        self._call("seek", value, relative)
        return f"Seeked to {int(value)}."

    def volume(self, level=None, delta=None):
        self._call("volume", level, delta)
        return "Volume set to 50%."

    def mute(self, muted=True):
        self._call("mute", muted)
        return "Muted." if muted else "Unmuted."

    def speed(self, rate=1.0):
        self._call("speed", rate)
        return f"Playback speed set to {rate:g}x."

    def next(self):
        self._call("next")
        return "Playing next video: Next."

    def previous(self):
        self._call("previous")
        return "Playing previous video: Prev."

    def feature(self, name, enabled=None):
        self._call("feature", name, enabled)
        return f"{name.capitalize()} on."

    def skip_ad(self, wait_ms=0):
        self._call("skip_ad", wait_ms)
        return "Skipped ad."

    def now_playing(self):
        self._call("now_playing")
        return "Now playing: 'Test Video', playing at 0:30 / 3:20."

    def close(self):
        self._call("close")
        return "YouTube player closed."

    def _state(self):
        return {"ok": True, "speed": 1.5, "paused": False}


@pytest.fixture
def controller(monkeypatch):
    fake = FakeController()
    monkeypatch.setattr(yt, "_get_controller", lambda create=True: fake)
    return fake


def fake_get(html: str):
    return lambda url, **kwargs: SimpleNamespace(text=html, status_code=200)


def video_renderer(video_id, title, *, channel="Channel", duration="3:20",
                   views="1M views", url=None, short=False, live=False):
    target_url = url or f"/watch?v={video_id}"
    badges = [{"metadataBadgeRenderer": {"label": "Shorts"}}] if short else []
    if live:
        badges.append({"metadataBadgeRenderer": {"label": "LIVE"}})
    return {
        "videoRenderer": {
            "videoId": video_id,
            "title": {"runs": [{"text": title}]},
            "ownerText": {"runs": [{"text": channel}]},
            "lengthText": {"simpleText": duration},
            "viewCountText": {"simpleText": views},
            "badges": badges,
            "navigationEndpoint": {
                "commandMetadata": {"webCommandMetadata": {"url": target_url}}
            },
        }
    }


def initial_data_html(items, extra_marker="ytInitialData"):
    return f"var {extra_marker} = {json.dumps({'ytInitialData': {'contents': items}})};\n"


# ---------------------------------------------------------------------------
# Parsing matrix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    f"https://www.youtube.com/watch?v={VIDEO_ID}",
    f"https://youtu.be/{VIDEO_ID}?t=90",
    f"https://www.youtube.com/shorts/{VIDEO_ID}",
    f"https://www.youtube.com/embed/{VIDEO_ID}",
    f"https://www.youtube.com/live/{VIDEO_ID}",
    f"https://m.youtube.com/watch?v={VIDEO_ID}&t=30s",
    f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PL123",
])
def test_extract_video_id_supports_all_url_forms(url):
    assert yt._extract_video_id(url) == VIDEO_ID


@pytest.mark.parametrize("bad", [
    "", None, "not a url", "https://example.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=short",
])
def test_extract_video_id_rejects_invalid_urls(bad):
    assert yt._extract_video_id(bad) is None


def test_is_valid_youtube_url():
    assert yt._is_valid_youtube_url(f"https://youtu.be/{VIDEO_ID}")
    assert not yt._is_valid_youtube_url("https://example.com/video")


@pytest.mark.parametrize("value,expected", [
    ("90", 90.0), ("1:30", 90.0), ("1:02:03", 3723.0), ("+15", 15.0),
    ("-10", -10.0), ("2m30s", 150.0), ("0:05", 5.0), (45, 45.0),
    ("garbage", None), ("", None), (None, None),
])
def test_parse_timestamp_matrix(value, expected):
    assert yt._parse_timestamp(value) == expected


def test_relative_timestamp_detection_and_formatting():
    assert yt._is_relative_timestamp("+15") is True
    assert yt._is_relative_timestamp("-10") is True
    assert yt._is_relative_timestamp("1:30") is False
    assert yt._format_time(90) == "1:30"
    assert yt._format_time(3723) == "1:02:03"


def test_extract_json_blob_is_brace_balanced_and_handles_escaped_braces():
    html = 'var ytInitialData = {"a": "brace } inside", "nested": {"x": [1,2]}}; trailing'
    parsed = yt._extract_json_blob(html, "ytInitialData")
    assert parsed == {"a": "brace } inside", "nested": {"x": [1, 2]}}
    assert yt._extract_json_blob("no marker here", "ytInitialData") is None


def test_number_and_clamp_helpers_reject_bad_input():
    assert yt._number("nope", 7) == 7
    assert yt._number(float("inf"), 3) == 3
    assert yt._clamp(500, 0, 100) == 100
    assert yt._clamp(-5, 0, 100) == 0


def test_append_timestamp_handles_missing_and_existing_query():
    assert yt._append_timestamp(f"https://www.youtube.com/watch?v={VIDEO_ID}", 90) == \
        f"https://www.youtube.com/watch?v={VIDEO_ID}&t=90s"
    assert "t=15s" in yt._append_timestamp(
        f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PL&t=5s", 15
    )
    assert yt._append_timestamp("https://www.youtube.com/watch?v=x", None).endswith("?v=x")


# ---------------------------------------------------------------------------
# Deterministic HTTP search + index resolution
# ---------------------------------------------------------------------------

def test_search_parses_json_filters_ads_and_shorts_and_caches(monkeypatch, controller):
    items = [
        video_renderer("abcdefghijk", "First Song", channel="Artist A"),
        {"promotedVideoRenderer": video_renderer("zzzzzzzzzzz", "Ad")},
        video_renderer("shortshorts", "A Short", url="/shorts/shortshorts", short=True),
        video_renderer("bcdefghijkl", "Second Song", channel="Artist B",
                       duration="4:01", views="2M views"),
    ]
    monkeypatch.setattr(yt.requests, "get", fake_get(initial_data_html(items)))

    results = yt._search_youtube_videos("lo-fi beats")
    assert [item["title"] for item in results] == ["First Song", "Second Song"]
    assert results[1]["channel"] == "Artist B"
    assert results[1]["duration"] == "4:01"
    assert results[1]["url"] == "https://www.youtube.com/watch?v=bcdefghijkl"
    assert yt._get_cached_results() == results

    message = yt.youtube_video({"action": "play", "index": 2})
    assert "Playing" in message
    assert controller.calls[-1][0] == "play_url"
    assert "bcdefghijkl" in controller.calls[-1][1][0]


def test_search_include_shorts_and_search_text_output(monkeypatch, controller):
    items = [
        video_renderer("abcdefghijk", "Regular"),
        video_renderer("shortshorts", "A Short", url="/shorts/shortshorts", short=True),
    ]
    monkeypatch.setattr(yt.requests, "get", fake_get(initial_data_html(items)))
    assert len(yt._search_youtube_videos("x", include_shorts=False)) == 1
    assert len(yt._search_youtube_videos("x", include_shorts=True)) == 2

    result = yt.youtube_video({"action": "search", "query": "lo-fi", "open": True})
    assert "Top YouTube results" in result
    assert "1. Regular" in result
    assert controller.calls[0][0] == "open"


def test_search_http_failure_falls_back_to_live_dom(monkeypatch, controller):
    def fail(*args, **kwargs):
        raise OSError("blocked")

    monkeypatch.setattr(yt.requests, "get", fail)
    controller.dom_results = [yt._renderer_to_result(
        video_renderer("abcdefghijk", "DOM Video")["videoRenderer"]
    )]
    result = yt.youtube_video({"action": "play", "query": "topic"})
    assert "Playing" in result
    assert any(call[0] == "search_dom" for call in controller.calls) or \
        controller.calls[-1][0] == "play_url"
    assert "abcdefghijk" in controller.calls[-1][1][0]


def test_search_requests_use_expected_endpoint_and_consent_cookies(monkeypatch):
    seen = {}

    def capture(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs["headers"]
        return SimpleNamespace(text=initial_data_html([]))

    monkeypatch.setattr(yt.requests, "get", capture)
    yt._search_youtube_videos("lo-fi beats")
    assert "search_query=lo-fi+beats" in seen["url"] and "hl=en" in seen["url"]
    assert "CONSENT=YES+cb" in seen["headers"]["Cookie"]
    assert "SOCS=CAI" in seen["headers"]["Cookie"]


# ---------------------------------------------------------------------------
# All playback control actions through the dispatcher
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action,method,expected", [
    ("pause", "pause", "Paused"),
    ("resume", "resume", "Resumed"),
    ("next", "next", "next video"),
    ("previous", "previous", "previous video"),
    ("mute", "mute", "Muted"),
    ("unmute", "mute", "Unmuted"),
    ("fullscreen", "feature", "on"),
    ("theater", "feature", "on"),
    ("miniplayer", "feature", "on"),
    ("captions", "feature", "on"),
    ("skip_ad", "skip_ad", "Skipped"),
    ("now_playing", "now_playing", "Now playing"),
    ("close", "close", "closed"),
])
def test_control_actions_dispatch(controller, action, method, expected):
    result = yt.youtube_video({"action": action})
    assert expected in result
    assert controller.calls[-1][0] == method


def test_play_by_query_url_index_and_resume(monkeypatch, controller):
    monkeypatch.setattr(yt.requests, "get", fake_get(
        initial_data_html([video_renderer("abcdefghijk", "Lo-fi")])
    ))
    assert "Playing" in yt.youtube_video({"action": "play", "query": "lo-fi"})
    assert controller.calls[-1][0] == "play_url" and "lo-fi" not in controller.calls[-1][1][0]

    assert "Playing" in yt.youtube_video({"action": "play", "url": f"https://youtu.be/{VIDEO_ID}"})
    assert VIDEO_ID in controller.calls[-1][1][0]

    yt._cache_results([{"video_id": VIDEO_ID, "url": f"https://youtu.be/{VIDEO_ID}"}])
    yt.youtube_video({"action": "play", "index": 1})
    assert controller.calls[-1][0] == "play_url"

    assert "Resumed" in yt.youtube_video({"action": "play"})
    assert controller.calls[-1][0] == "resume"


def test_play_with_start_timestamp_appends_t_param(monkeypatch, controller):
    yt.youtube_video({"action": "play", "url": f"https://youtu.be/{VIDEO_ID}",
                      "timestamp": "1:30"})
    assert controller.calls[-1][1] == (
        f"https://www.youtube.com/watch?v={VIDEO_ID}", 90.0
    )


def test_toggle_reports_exact_state(controller):
    assert "Playing" in yt.youtube_video({"action": "toggle_play"})
    assert controller.calls[-1][0] == "toggle"


@pytest.mark.parametrize("action,value,relative", [
    ("seek", 90.0, False),
    ("forward", 10.0, True),
    ("rewind", -10.0, True),
    ("restart", 0, False),
])
def test_seek_family(controller, action, value, relative):
    yt.youtube_video({"action": action, "timestamp": 90} if action == "seek" else
                     {"action": action})
    assert controller.calls[-1][0] == "seek"
    assert controller.calls[-1][1] == (value, relative)


def test_seek_offset_and_invalid_input(controller):
    yt.youtube_video({"action": "seek", "offset": -10})
    assert controller.calls[-1][1] == (-10.0, True)
    assert "timestamp" in yt.youtube_video({"action": "seek", "timestamp": "nope"}).lower()


@pytest.mark.parametrize("level,clamped", [
    (0, 0.0), (50, 50.0), (100, 100.0), (-4, 0.0), (250, 100.0),
])
def test_volume_set_and_clamping(controller, level, clamped):
    yt.youtube_video({"action": "volume", "volume": level})
    assert controller.calls[-1][0] == "volume"
    assert controller.calls[-1][1][0] == clamped


@pytest.mark.parametrize("alias,delta", [
    ("volume_up", 10.0), ("volume_down", -10.0),
])
def test_volume_relative_aliases(controller, alias, delta):
    yt.youtube_video({"action": alias})
    assert controller.calls[-1][1] == (None, delta)


@pytest.mark.parametrize("rate,clamped", [
    (1.25, 1.25), (0.1, 0.25), (9, 4.0), ("bad", None),
])
def test_speed_clamping_and_bad_input(controller, rate, clamped):
    result = yt.youtube_video({"action": "speed", "speed": rate})
    if clamped is None:
        assert "0.25" in result
    else:
        assert controller.calls[-1][1][0] == clamped


def test_feature_aliases_and_explicit_enabled(controller):
    for alias in ("theatre", "cinema", "pip", "subtitles", "cc", "full_screen"):
        yt.youtube_video({"action": alias})
    names = [call[1][0] for call in controller.calls]
    assert names == ["theater", "theater", "miniplayer", "captions", "captions", "fullscreen"]

    yt.youtube_video({"action": "captions", "enabled": "false"})
    assert controller.calls[-1][1][1] is False


# ---------------------------------------------------------------------------
# Edge cases, aliases and fallbacks
# ---------------------------------------------------------------------------

def test_empty_and_unknown_parameters(controller):
    controller.active = False
    assert "what you'd like to watch" in yt.youtube_video(None)
    assert "what you'd like to watch" in yt.youtube_video({})
    assert "Unknown YouTube action" in yt.youtube_video({"action": "teleport"})


@pytest.mark.parametrize("action,canonical", [
    ("launch", "open"), ("find", "search"), ("watch", "play"), ("hold", "pause"),
    ("continue", "resume"), ("playpause", "toggle_play"), ("jump", "seek"),
    ("ff", "forward"), ("back", "rewind"), ("replay", "restart"),
    ("next_video", "next"), ("prev", "previous"), ("set_volume", "volume"),
    ("playback_rate", "speed"), ("cc", "captions"), ("skip_ads", "skip_ad"),
    ("status", "now_playing"), ("tldr", "summarize"), ("info", "get_info"),
    ("charts", "trending"), ("quit", "close"),
])
def test_action_alias_normalization(action, canonical):
    assert yt._normalize_action({"action": action}) == canonical
    assert yt._ACTION_MAP[action] is yt._ACTION_MAP[canonical]


def test_non_numeric_index_volume_speed_seconds_do_not_crash(controller):
    for params in (
        {"action": "play", "index": "abc"},
        {"action": "volume", "volume": "loud"},
        {"action": "speed", "speed": "fast"},
        {"action": "seek", "seconds": "tomorrow"},
    ):
        assert isinstance(yt.youtube_video(params), str)


def test_playwright_unavailable_falls_back_to_webbrowser(monkeypatch):
    opened = []
    monkeypatch.setattr(yt, "_get_controller", lambda create=True: None)
    monkeypatch.setattr(yt.webbrowser, "open", lambda url: opened.append(url))

    def blocked(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(yt.requests, "get", blocked)

    yt.youtube_video({"action": "open"})
    yt.youtube_video({"action": "play", "url": f"https://youtu.be/{VIDEO_ID}",
                      "timestamp": 90})
    yt.youtube_video({"action": "play", "query": "topic"})

    assert opened[0] == "https://www.youtube.com"
    assert f"v={VIDEO_ID}" in opened[1] and "t=90s" in opened[1]
    assert "search_query=topic" in opened[2]


def test_external_browser_fallback_sends_shortcut(monkeypatch):
    monkeypatch.setattr(yt, "_get_controller", lambda create=True: None)
    window = SimpleNamespace(title="Lo-fi - YouTube", activate=lambda: None)
    gw = types.ModuleType("pygetwindow")
    gw.getWindowsWithTitle = lambda title: [window]
    pyautogui = types.ModuleType("pyautogui")
    pressed = []
    pyautogui.press = lambda key: pressed.append(key)
    pyautogui.hotkey = lambda *keys: pressed.append("+".join(keys))
    monkeypatch.setitem(sys.modules, "pygetwindow", gw)
    monkeypatch.setitem(sys.modules, "pyautogui", pyautogui)

    result = yt.youtube_video({"action": "pause"})
    assert "YouTube window" in result and pressed == ["k"]


def test_external_browser_fallback_media_controls_and_honest_message(monkeypatch):
    monkeypatch.setattr(yt, "_get_controller", lambda create=True: None)
    gw = types.ModuleType("pygetwindow")
    gw.getWindowsWithTitle = lambda title: []
    monkeypatch.setitem(sys.modules, "pygetwindow", gw)

    calls = []
    monkeypatch.setattr(
        "actions.media_control.media_control_action",
        lambda params: calls.append(params) or "Pressed play/pause, sir.",
    )
    assert "media controls" in yt.youtube_video({"action": "mute"})
    assert calls == [{"action": "mute"}]

    from actions import media_control
    monkeypatch.setattr(media_control, "media_control_action",
                        lambda params: (_ for _ in ()).throw(RuntimeError("no keyboard")))
    assert "No YouTube window" in yt.youtube_video({"action": "pause"})


# ---------------------------------------------------------------------------
# Controller-level deterministic HTML5 control (fake page, no browser)
# ---------------------------------------------------------------------------

class FakeVideoPage:
    """Minimal Playwright-like page implementing only the JS surface we use."""

    def __init__(self, *, paused=False, duration=200, volume=80, muted=False,
                 speed=1.0, current=30, clickable=()):
        self.paused = paused
        self.duration = duration
        self.volume = volume
        self.muted = muted
        self.speed = speed
        self.current = current
        self.clickable = tuple(clickable)
        self.evaluated = []
        self.clicks = []
        self.waits = []
        self.play_calls = 0
        self.keyboard = SimpleNamespace(press=AsyncMock())

    async def evaluate(self, script, arg=None):
        self.evaluated.append((script, arg))
        if "const titleEl = document.querySelector" in script:
            return self._state()
        if "if (!v.paused) v.pause();" in script:
            self.paused = True
            return {"ok": True, "paused": self.paused, "currentTime": self.current,
                    "title": "Fake Video"}
        if "await v.play();" in script:
            if "if (v.paused)" not in script or self.paused:
                self.play_calls += 1
                self.paused = False
            return {"ok": True, "paused": self.paused, "currentTime": self.current,
                    "title": "Fake Video"}
        if "v.currentTime = target;" in script:
            target = (self.current if arg["relative"] else 0) + arg["value"]
            self.current = max(0, min(self.duration, target))
            return {"ok": True, "currentTime": self.current, "duration": self.duration,
                    "paused": self.paused}
        if "v.volume = level / 100;" in script or "arg.delta" in script and "v.volume" in script:
            if arg.get("set") is not None:
                self.volume = max(0, min(100, int(arg["set"])))
                self.muted = self.volume == 0
            if arg.get("delta") is not None:
                self.volume = max(0, min(100, self.volume + int(arg["delta"])))
                self.muted = self.volume == 0
            return {"ok": True, "volume": self.volume, "muted": self.muted}
        if "v.muted = !!arg.muted;" in script:
            self.muted = bool(arg["muted"])
            return {"ok": True, "muted": self.muted, "volume": self.volume}
        if "v.playbackRate = rate;" in script:
            self.speed = max(0.25, min(4.0, arg["value"]))
            return {"ok": True, "speed": self.speed}
        if "const selectors = {" in script:
            wanted = arg["enabled"]
            self.clicks.append(f"feature:{arg['name']}:{wanted}")
            return {"ok": True, "name": arg["name"], "enabled": True if wanted is None else wanted}
        return {}

    def _state(self):
        return {"ok": True, "title": "Fake Video", "channel": "Fake Channel",
                "currentTime": self.current, "duration": self.duration,
                "paused": self.paused, "volume": self.volume, "muted": self.muted,
                "speed": self.speed, "url": "https://www.youtube.com/watch?v=fake"}

    def locator(self, selector):
        page = self

        class Locator:
            async def count(self):
                return 1 if selector in page.clickable else 0

            async def is_visible(self):
                return True

            async def click(self, timeout=None):
                page.clicks.append(selector)

            @property
            def first(self):
                return self

        return Locator()

    async def wait_for_timeout(self, ms):
        self.waits.append(ms)


class _NoBrowserController(yt._YouTubeController):
    def __init__(self, page):
        super().__init__()
        self._page = page

    async def _get_page(self):
        return self._page


def test_pause_is_deterministic_and_idempotent():
    page = FakeVideoPage(paused=True)
    controller = _NoBrowserController(page)
    assert asyncio.run(controller.pause()) == "Paused 'Fake Video' at 0:30."
    assert page.play_calls == 0


def test_resume_does_not_restart_a_playing_video():
    page = FakeVideoPage(paused=False)
    controller = _NoBrowserController(page)
    assert asyncio.run(controller.resume()) == "Resumed 'Fake Video' at 0:30."
    assert page.play_calls == 0  # guard means play() is skipped when already playing


def test_toggle_reports_resulting_state():
    controller = _NoBrowserController(FakeVideoPage(paused=False))
    assert asyncio.run(controller.toggle()) == "Paused 'Fake Video' at 0:30."


def test_seek_clamps_to_duration_and_supports_relative_offsets():
    page = FakeVideoPage(current=30, duration=200)
    controller = _NoBrowserController(page)
    assert asyncio.run(controller.seek(500, False)) == "Seeked to 3:20."
    assert asyncio.run(controller.seek(-50, True)) == "Seeked to 2:30."
    assert asyncio.run(controller.seek(-500, True)) == "Seeked to 0:00."


def test_controller_volume_zero_mutes_and_speed_clamps():
    page = FakeVideoPage(volume=80)
    controller = _NoBrowserController(page)
    assert asyncio.run(controller.volume(level=0)) == "Volume set to 0% (muted)."
    assert page.muted is True and page.volume == 0
    assert asyncio.run(controller.volume(delta=25)) == "Volume set to 25%."
    assert asyncio.run(controller.speed(99)) == "Playback speed set to 4x."
    assert asyncio.run(controller.speed(-3)) == "Playback speed set to 0.25x."


def test_mute_and_now_playing_report_state():
    controller = _NoBrowserController(FakeVideoPage(volume=40, paused=False))
    assert asyncio.run(controller.mute(True)) == "Muted."
    assert asyncio.run(controller.mute(False)) == "Unmuted."
    status = asyncio.run(controller.now_playing())
    assert "Now playing: 'Fake Video' by Fake Channel" in status
    assert "Playing at 0:30 / 3:20" in status and "40%" in status


def test_feature_toggle_and_skip_ad_click():
    page = FakeVideoPage(clickable=(".ytp-skip-ad-button",))
    controller = _NoBrowserController(page)
    assert asyncio.run(controller.feature("captions", True)) == "Captions on."
    assert asyncio.run(controller.skip_ad(wait_ms=2000)) == "Skipped ad."
    assert page.waits == [2000]
    assert ".ytp-skip-ad-button" in page.clicks


def test_dismiss_popups_and_dom_search_are_failure_tolerant():
    page = FakeVideoPage()
    controller = _NoBrowserController(page)
    # No get_by_role/locator popups available -> must not raise.
    asyncio.run(controller._dismiss_popups(page))
    controller._goto = lambda url: asyncio.sleep(0, result=url)
    assert asyncio.run(controller.search_dom("lo-fi")) == []


# ---------------------------------------------------------------------------
# Transcripts, summaries and Gemini wiring
# ---------------------------------------------------------------------------

class _Snippet:
    def __init__(self, text):
        self.text = text


class _TranscriptV12:
    def __init__(self):
        self.languages = []

    def find_transcript(self, languages):
        self.languages.append(languages[0])
        if languages[0] == "en":
            raise RuntimeError("no English transcript")
        return self

    def fetch(self):
        return [_Snippet("Hello"), _Snippet("world")]

    def __iter__(self):
        return iter([])


class _ApiV12:
    instances = []

    def __init__(self):
        self.transcript = _TranscriptV12()
        self.instances.append(self)

    def list(self, video_id):
        return self.transcript


class _TranscriptV06:
    def find_manually_created_transcript(self, languages):
        raise RuntimeError("none")

    def find_generated_transcript(self, languages):
        return self

    def fetch(self):
        return [{"text": "Legacy"}, {"text": "transcript"}]


class _ApiV06:
    @staticmethod
    def list_transcripts(video_id):
        return _TranscriptV06()


def test_transcript_v12_instance_api(monkeypatch):
    _ApiV12.instances.clear()
    monkeypatch.setattr(yt, "_TRANSCRIPT_OK", True)
    monkeypatch.setattr(yt, "YouTubeTranscriptApi", _ApiV12)
    assert yt._get_transcript(VIDEO_ID) == "Hello world"
    assert _ApiV12.instances[0].transcript.languages == ["en", "hi"]


def test_transcript_v06_static_api(monkeypatch):
    monkeypatch.setattr(yt, "_TRANSCRIPT_OK", True)
    monkeypatch.setattr(yt, "YouTubeTranscriptApi", _ApiV06)
    assert yt._get_transcript(VIDEO_ID) == "Legacy transcript"


def test_summarize_uses_playing_video_url_and_gemini_retry(monkeypatch, controller):
    controller._url = f"https://www.youtube.com/watch?v={VIDEO_ID}"
    monkeypatch.setattr(yt, "_TRANSCRIPT_OK", True)
    _ApiV12.instances.clear()
    monkeypatch.setattr(yt, "YouTubeTranscriptApi", _ApiV12)
    monkeypatch.setattr(yt, "_get_transcript", lambda video_id: "A transcript")

    import api_keys
    from config.models import MODEL_FLASH
    calls = {}

    def fake_generate(model, contents, **kwargs):
        calls.update(model=model, contents=contents)
        return SimpleNamespace(text="  One sentence. Three key points.  ")

    monkeypatch.setattr(api_keys, "generate_with_retry", fake_generate)
    result = yt.youtube_video({"action": "summarize"})
    assert result == "One sentence. Three key points."
    assert calls["model"] == MODEL_FLASH
    assert "A transcript" in calls["contents"]


def test_summarize_query_fallback_and_missing_target(monkeypatch, controller):
    controller.active = False
    assert "URL or a search query" in yt.youtube_video({"action": "summarize"})

    monkeypatch.setattr(yt, "_search_youtube_videos", lambda query, **kw: [
        {"video_id": VIDEO_ID, "url": f"https://youtu.be/{VIDEO_ID}"}
    ])
    monkeypatch.setattr(yt, "_TRANSCRIPT_OK", True)
    monkeypatch.setattr(yt, "_get_transcript", lambda video_id: "text")
    import api_keys
    monkeypatch.setattr(api_keys, "generate_with_retry",
                        lambda model, contents: SimpleNamespace(text="Summary"))
    assert yt.youtube_video({"action": "summarize", "query": "topic"}) == "Summary"


def test_get_info_prefers_url_then_current_then_query(monkeypatch, controller):
    seen = []
    monkeypatch.setattr(yt, "_scrape_video_info", lambda video_id: seen.append(video_id) or
                        {"title": "Info", "channel": "Channel"})
    yt.youtube_video({"action": "get_info", "url": f"https://youtu.be/{VIDEO_ID}"})
    controller._url = f"https://www.youtube.com/watch?v={VIDEO_ID}"
    yt.youtube_video({"action": "get_info"})
    controller.active = False
    monkeypatch.setattr(yt, "_search_youtube_videos", lambda query, **kw: [
        {"video_id": VIDEO_ID, "url": f"https://youtu.be/{VIDEO_ID}"}
    ])
    result = yt.youtube_video({"action": "get_info", "query": "topic"})
    assert seen == [VIDEO_ID, VIDEO_ID, VIDEO_ID]
    assert "Title: Info" in result


# ---------------------------------------------------------------------------
# Video info and modern charts
# ---------------------------------------------------------------------------

def test_scrape_video_info_parses_player_response(monkeypatch):
    payload = {
        "ytInitialPlayerResponse": {
            "videoDetails": {
                "title": "Deep Dive", "author": "Friday", "viewCount": "123456",
                "lengthSeconds": "200", "shortDescription": "Description",
                "isLiveContent": False,
            }
        }
    }
    html = "var ytInitialPlayerResponse = " + json.dumps(payload["ytInitialPlayerResponse"]) + ";"
    monkeypatch.setattr(yt.requests, "get", fake_get(html))
    info = yt._scrape_video_info(VIDEO_ID)
    assert info["title"] == "Deep Dive"
    assert info["channel"] == "Friday"
    assert info["views"] == "123,456"
    assert info["duration"] == "3:20"
    assert info["description"] == "Description"
    assert info["is_live"] is False


def test_scrape_video_info_regex_fallback(monkeypatch):
    html = (
        '"title":{"runs":[{"text":"Regex Title"}]}'
        '"ownerChannelName":"Regex Channel"'
        '"viewCount":"42" "lengthSeconds":"65" "label":"1,234 likes"'
    )
    monkeypatch.setattr(yt.requests, "get", fake_get(html))
    info = yt._scrape_video_info(VIDEO_ID)
    assert info["title"] == "Regex Title"
    assert info["channel"] == "Regex Channel"
    assert info["views"] == "42" and info["duration"] == "1:05"
    assert info["likes"] == "1,234 likes"


@pytest.mark.parametrize("category,keyword", [
    ("general", "trending"), ("music", "music"), ("gaming", "gaming"),
    ("movies", "movies"), ("podcasts", "podcasts"),
])
def test_trending_category_charts_are_ranked_and_cached(monkeypatch, controller, category, keyword):
    seen = {}

    def capture(url, **kwargs):
        seen["url"] = url
        if "feed/trending" in url:
            return SimpleNamespace(text="retired endpoint")
        items = [video_renderer("abcdefghijk", "Chart One"),
                 video_renderer("bcdefghijkl", "Chart Two")]
        return SimpleNamespace(text=initial_data_html(items))

    monkeypatch.setattr(yt.requests, "get", capture)
    result = yt.youtube_video({"action": "trending", "category": category, "region": "US"})
    assert f"Top {category} charts in US" in result
    assert "1. Chart One" in result and "2. Chart Two" in result
    assert keyword in seen["url"] or category == "general"
    cached = yt._get_cached_results()
    assert cached[0]["rank"] == 1 and cached[0]["video_id"] == "abcdefghijk"

    yt.youtube_video({"action": "play", "index": 1})
    assert controller.calls[-1][0] == "play_url"
    assert "abcdefghijk" in controller.calls[-1][1][0]


def test_trending_defaults_region_and_handles_failures(monkeypatch):
    seen = {}

    def capture(url, **kwargs):
        seen.setdefault("urls", []).append(url)
        return SimpleNamespace(text="blocked")

    monkeypatch.setattr(yt.requests, "get", capture)
    monkeypatch.setattr(yt, "_BEAUTIFULSOUP_OK", False)
    assert "Could not fetch" in yt.youtube_video({"action": "trending"})
    assert all("gl=IN" in url or "IN" in url for url in seen["urls"])


def test_module_imports_without_optional_gui_or_network_libraries():
    """Windows/Linux with no Playwright/requests/bs4/transcript libs must still import."""
    import subprocess

    code = """
import importlib.abc
import sys

BLOCKED = {'requests', 'bs4', 'youtube_transcript_api', 'playwright',
           'pyautogui', 'pygetwindow', 'winreg', 'tkinter'}

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in BLOCKED:
            raise ImportError('blocked: ' + fullname)
        return None

sys.meta_path.insert(0, Blocker())
from actions import youtube_video as ytv
assert ytv._extract_video_id('https://youtu.be/dQw4w9WgXcQ') == 'dQw4w9WgXcQ'
assert ytv._parse_timestamp('1:30') == 90.0
assert ytv._REQUESTS_OK is False and ytv._TRANSCRIPT_OK is False
assert ytv.youtube_video({'action': 'play'}) == \\
    "Please tell me what you'd like to watch, sir."
print('IMPORT_OK')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert "IMPORT_OK" in result.stdout
