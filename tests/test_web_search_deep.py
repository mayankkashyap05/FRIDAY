"""Fully offline combinatorial tests for actions.web_search."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from actions import web_search as ws
from config.models import MODEL_FLASH, MODEL_FLASH_LITE


@pytest.fixture(autouse=True)
def _clear_cache():
    with ws._CACHE_LOCK:
        ws._CACHE.clear()
    yield
    with ws._CACHE_LOCK:
        ws._CACHE.clear()


def _response(text="Grounded answer", chunks=(), candidates=None):
    if candidates is None:
        part = SimpleNamespace(text=text)
        content = SimpleNamespace(parts=[part])
        metadata = SimpleNamespace(grounding_chunks=list(chunks))
        candidates = [SimpleNamespace(content=content, grounding_metadata=metadata)]
    return SimpleNamespace(candidates=candidates)


def _chunk(title, uri):
    return SimpleNamespace(web=SimpleNamespace(title=title, uri=uri))


class _Memory:
    def __init__(self):
        self.calls = []

    def set_last_search(self, **kwargs):
        self.calls.append(kwargs)


# ---------------------------------------------------------------------------
# Tier 1: Gemini grounding
# ---------------------------------------------------------------------------

def test_grounded_search_uses_lite_model_and_deduplicates_citations(monkeypatch):
    import api_keys

    calls = []
    response = _response(chunks=[
        _chunk("NASA", "https://nasa.example/a"),
        _chunk("NASA duplicate", "https://nasa.example/a"),
        _chunk("ESA", "https://esa.example/b"),
    ])

    def generate(model, contents, config=None):
        calls.append(model)
        return response

    monkeypatch.setattr(api_keys, "generate_with_retry", generate)
    result = ws.web_search({"query": "space news", "fresh": True})

    assert "Grounded answer" in result
    assert "Sources:" in result
    assert result.count("https://nasa.example/a") == 1
    assert "https://esa.example/b" in result
    assert calls == [MODEL_FLASH_LITE]


def test_gemini_falls_back_to_flash_model_on_error(monkeypatch):
    import api_keys

    calls = []

    def generate(model, contents, config=None):
        calls.append(model)
        if model == MODEL_FLASH_LITE:
            raise RuntimeError("model retired")
        return _response("Fallback answer")

    monkeypatch.setattr(api_keys, "generate_with_retry", generate)
    assert ws._gemini_search("hello") == "Fallback answer"
    assert calls == [MODEL_FLASH_LITE, MODEL_FLASH]


def test_empty_candidates_fall_back_to_ddgs(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda *a, **k: (_ for _ in ()).throw(
        ValueError("Empty response")
    ))
    monkeypatch.setattr(ws, "_ddg_search", lambda *a, **k: [
        {"title": "Result", "snippet": "Snippet", "url": "https://example.com"}
    ])
    result = ws.web_search({"query": "python", "fresh": True})
    assert "Search results for: python" in result
    assert "Result" in result


def test_none_content_and_sources_toggle(monkeypatch):
    import api_keys

    response = SimpleNamespace(candidates=[SimpleNamespace(content=None)])
    monkeypatch.setattr(api_keys, "generate_with_retry", lambda **kw: response)
    with pytest.raises(ValueError):
        ws._gemini_search("nothing")

    response = _response(chunks=[_chunk("Source", "https://src.example")])
    monkeypatch.setattr(api_keys, "generate_with_retry", lambda **kw: response)
    with_sources = ws._gemini_search("q", include_sources=True)
    without = ws._gemini_search("q", include_sources=False)
    assert "Sources:" in with_sources and "Sources:" not in without


@pytest.mark.parametrize("mode", ["search", "news", "quick", "answer"])
def test_prompt_tailoring_by_mode(monkeypatch, mode):
    import api_keys

    prompts = []

    def generate(model, contents, config=None):
        prompts.append(contents)
        return _response()

    monkeypatch.setattr(api_keys, "generate_with_retry", generate)
    ws.web_search({"query": "topic", "mode": mode, "time_range": "week",
                   "region": "in-en", "fresh": True})
    prompt = prompts[0]
    if mode == "news":
        assert "latest verified news" in prompt and "week" in prompt and "in-en" in prompt
    elif mode in ("quick", "answer"):
        assert "at most 2 short sentences" in prompt
    else:
        assert "concise, factual" in prompt


# ---------------------------------------------------------------------------
# Tier 2: ddgs text / news
# ---------------------------------------------------------------------------

class _FakeDDGS:
    calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def text(self, query, **kwargs):
        self.calls.append(("text", query, kwargs))
        return [{"title": "Text Result", "body": "A snippet", "href": "https://text.example"}]

    def news(self, query, **kwargs):
        self.calls.append(("news", query, kwargs))
        return [{"title": "News Result", "body": "Breaking", "href": "https://news.example",
                 "date": "2026-11-24"}]


def test_ddgs_text_and_news_routing(monkeypatch):
    _FakeDDGS.calls = []
    monkeypatch.setattr(ws, "_ddgs_import", lambda: _FakeDDGS)

    text = ws._ddg_search("cats", max_results=3, time_range="week", region="in-en")
    news = ws._ddg_search("cats", mode="news", time_range="day")

    assert text[0]["url"] == "https://text.example"
    assert news[0]["date"] == "2026-11-24"
    assert _FakeDDGS.calls[0][0] == "text"
    assert _FakeDDGS.calls[0][2]["timelimit"] == "w"
    assert _FakeDDGS.calls[0][2]["region"] == "in-en"
    assert _FakeDDGS.calls[1][0] == "news"
    assert _FakeDDGS.calls[1][2]["timelimit"] == "d"


def test_ddgs_max_results_is_clamped(monkeypatch):
    _FakeDDGS.calls = []
    monkeypatch.setattr(ws, "_ddgs_import", lambda: _FakeDDGS)
    ws._ddg_search("q", max_results=999)
    assert _FakeDDGS.calls[0][2]["max_results"] == 10


def test_ddgs_typeerror_retries_without_unsupported_kwargs(monkeypatch):
    class PickyDDGS(_FakeDDGS):
        def text(self, query, **kwargs):
            if "timelimit" in kwargs:
                raise TypeError("unexpected keyword timelimit")
            return [{"title": "OK", "href": "https://ok.example"}]

    monkeypatch.setattr(ws, "_ddgs_import", lambda: PickyDDGS)
    results = ws._ddg_search("q", time_range="month")
    assert results[0]["title"] == "OK"


def test_format_ddg_includes_dates_and_urls():
    formatted = ws._format_ddg("q", [
        {"title": "T", "snippet": "S", "url": "https://u", "date": "2026-11-24"}
    ])
    assert "T (2026-11-24)" in formatted and "S" in formatted and "https://u" in formatted
    assert "No results found" in ws._format_ddg("q", [])


# ---------------------------------------------------------------------------
# Tier 3: HTTP fallbacks
# ---------------------------------------------------------------------------

def test_ratelimit_falls_back_to_instant_answer(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("RatelimitException")
    ))
    monkeypatch.setattr(ws, "_ddg_search", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("RatelimitException")
    ))
    monkeypatch.setattr(ws, "_instant_answer", lambda q: "Instant answer")
    result = ws.web_search({"query": "fallback", "fresh": True})
    assert result == "Instant answer"


def test_instant_answer_uses_ddg_then_wikipedia(monkeypatch):
    import requests

    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "duckduckgo" in url:
            return SimpleNamespace(json=lambda: {"AbstractText": "DDG abstract"})
        return SimpleNamespace(json=lambda: {"extract": "Wikipedia summary"})

    monkeypatch.setattr(requests, "get", fake_get)
    assert ws._instant_answer("topic") == "DDG abstract"
    assert len(calls) == 1

    def empty_ddg(url, **kwargs):
        calls.append(url)
        if "duckduckgo" in url:
            return SimpleNamespace(json=lambda: {"RelatedTopics": []})
        return SimpleNamespace(json=lambda: {"extract": "Wikipedia summary"})

    monkeypatch.setattr(requests, "get", empty_ddg)
    assert ws._instant_answer("topic") == "Wikipedia summary"
    assert "wikipedia.org" in calls[-1]


# ---------------------------------------------------------------------------
# Compare, cache and dispatcher edge cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("items", [["Python", "Rust"], "Python, Rust"])
def test_compare_accepts_list_and_comma_string(monkeypatch, items):
    calls = []
    monkeypatch.setattr(ws, "_gemini_search", lambda query, **kw: calls.append(kw) or
                        "compared")
    assert ws._compare(items, "speed") == "compared"
    assert calls[0]["items"] == ["Python", "Rust"]
    assert calls[0]["mode"] == "compare"


def test_compare_falls_back_to_ddg(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("quota")
    ))
    monkeypatch.setattr(ws, "_ddg_search", lambda *a, **k: [
        {"title": "T", "snippet": "Comparison snippet"}
    ])
    result = ws._compare(["A", "B"], "price")
    assert "Comparison" in result and "Comparison snippet" in result


def test_cache_key_matches_documented_six_fields():
    key = ws._cache_key({
        "query": " Cats ", "mode": "news", "items": "A, B",
        "aspect": "price", "time_range": "week", "region": "in-en",
        "include_sources": False, "max_results": 9,
    })
    assert key == ("news", "cats", ("A", "B"), "price", "week", "in-en")


def test_ddg_no_results_falls_through_to_tier_three(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("offline")
    ))
    monkeypatch.setattr(ws, "_ddg_search", lambda *a, **k: [])
    monkeypatch.setattr(ws, "_instant_answer", lambda q: "Wikipedia says hello")
    assert ws.web_search({"query": "hello", "fresh": True}) == "Wikipedia says hello"


def test_ttl_cache_hit_and_fresh_bypass(monkeypatch):
    calls = []

    def fake(query, **kwargs):
        calls.append(query)
        return f"result-{len(calls)}"

    monkeypatch.setattr(ws, "_gemini_search", fake)
    params = {"query": "cached query"}
    assert ws.web_search(params) == "result-1"
    assert ws.web_search(params) == "result-1"
    assert len(calls) == 1
    assert ws.web_search({"query": "cached query", "fresh": True}) == "result-2"
    assert len(calls) == 2


def test_no_cache_and_session_memory(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda *a, **k: "Answer")
    memory = _Memory()
    ws.web_search({"query": "one", "no_cache": True}, session_memory=memory)
    ws.web_search({"query": "two", "fresh": True}, session_memory=memory)
    assert memory.calls == [
        {"query": "one", "response": "Answer"},
        {"query": "two", "response": "Answer"},
    ]


@pytest.mark.parametrize("params", [None, {}, {"query": "   "}])
def test_empty_requests_return_prompt(params):
    assert "provide a search query" in ws.web_search(params)


def test_non_list_items_and_bad_max_results_are_safe(monkeypatch):
    seen = []
    monkeypatch.setattr(ws, "_gemini_search", lambda query, **kw: seen.append(kw) or "OK")
    assert ws.web_search({"items": "A, B", "aspect": "price", "fresh": True}) == "OK"
    assert seen[0]["items"] == ["A", "B"]
    def fail_gemini(query, **kw):
        raise RuntimeError("offline")

    monkeypatch.setattr(ws, "_gemini_search", fail_gemini)
    monkeypatch.setattr(ws, "_ddg_search",
                        lambda query, max_results=6, **kw: seen.append(max_results) or [])
    ws.web_search({"query": "q", "max_results": "bogus", "fresh": True})
    assert seen[-1] == 5
