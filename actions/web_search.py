# actions/web_search.py
# Friday — grounded, multi-tier web search.
#
# Tier 1: Gemini google_search grounding via api_keys.generate_with_retry
#         (rotates Gemini keys on 429 and falls back to MODEL_FLASH).
# Tier 2: Modern ddgs (or legacy duckduckgo_search) text/news search.
# Tier 3: DuckDuckGo Instant Answer + Wikipedia REST summary (HTTP only).
#
# All heavy imports are lazy so this module imports cleanly on Windows/Linux
# without google-genai, ddgs or requests installed.

from __future__ import annotations

import sys
import threading
import time
import warnings
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote_plus

from config.models import MODEL_FLASH, MODEL_FLASH_LITE

_CACHE_TTL_S = 300.0
_CACHE_MAX = 64
_CACHE_LOCK = threading.RLock()
_CACHE: OrderedDict[tuple, tuple[float, str]] = OrderedDict()

_TIME_LIMITS = {"day": "d", "week": "w", "month": "m", "year": "y"}


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_api_key() -> str:
    """Legacy helper kept for compatibility; key rotation now lives in api_keys."""
    from api_keys import next_key
    return next_key()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_key(params: dict) -> tuple:
    return (
        str(params.get("mode") or "search").strip().lower(),
        str(params.get("query") or "").strip().lower(),
        tuple(_normalize_items(params.get("items"))),
        str(params.get("aspect") or "").strip().lower(),
        str(params.get("time_range") or "").strip().lower(),
        str(params.get("region") or "").strip().lower(),
    )


def _cache_get(key: tuple) -> str | None:
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if not entry:
            return None
        created, value = entry
        if time.monotonic() - created > _CACHE_TTL_S:
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return value


def _cache_put(key: tuple, value: str) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), value)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


# ---------------------------------------------------------------------------
# Prompt construction and response extraction
# ---------------------------------------------------------------------------

def _normalize_items(items) -> list[str]:
    if items is None:
        return []
    if isinstance(items, str):
        return [part.strip() for part in items.split(",") if part.strip()]
    if isinstance(items, (list, tuple, set)):
        return [str(part).strip() for part in items if str(part).strip()]
    return [str(items).strip()] if str(items).strip() else []


def _build_prompt(query: str, mode: str, items: list[str], aspect: str,
                  time_range: str, region: str) -> str:
    mode = (mode or "search").strip().lower()
    context = []
    if time_range:
        context.append(f"Limit results to the last {time_range}.")
    if region:
        context.append(f"Use the {region} regional perspective where relevant.")
    suffix = " ".join(context)

    if mode == "compare" and items:
        return (
            f"Compare {', '.join(items)} side by side in terms of {aspect}. "
            "Use specific, verifiable facts and data. Structure the answer with "
            f"one clearly labelled section per item. {suffix}"
        ).strip()
    if mode == "news":
        return (
            f"Give the latest verified news about: {query}. Include dates and "
            f"name the source for each key development. {suffix}"
        ).strip()
    if mode in ("quick", "answer"):
        return (
            f"Answer in at most 2 short sentences, voice-ready, no bullet points: "
            f"{query}. {suffix}"
        ).strip()
    return (
        f"Give a concise, factual and up-to-date answer to: {query}. "
        f"Prefer current, authoritative information. {suffix}"
    ).strip()


def _config_for_grounding(prompt: str):
    """Build a GenerateContentConfig, with a plain dict fallback."""
    try:
        from google.genai import types
        tool = types.Tool(google_search=types.GoogleSearch())
        return types.GenerateContentConfig(
            tools=[tool], system_instruction=prompt[:4000]
        )
    except Exception:
        return {"tools": [{"google_search": {}}], "system_instruction": prompt[:4000]}


def _field(obj, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _extract_response(response) -> tuple[str, list[dict]]:
    """Safely pull text and deduplicated web citations out of a GenAI response."""
    text_parts: list[str] = []
    citations: list[dict] = []
    seen: set[str] = set()

    for candidate in _field(response, "candidates", None) or []:
        content = _field(candidate, "content", None)
        for part in _field(content, "parts", None) or []:
            snippet = _field(part, "text", None)
            if snippet:
                text_parts.append(str(snippet))

        metadata = _field(candidate, "grounding_metadata", None)
        chunks = _field(metadata, "grounding_chunks", None) or []
        if isinstance(chunks, dict):
            chunks = list(chunks.values())
        for chunk in chunks:
            web = _field(chunk, "web", None) or _field(
                chunk, "retrieved_context", None
            )
            title = str(_field(web, "title", None) or "").strip()
            uri = str(_field(web, "uri", None) or "").strip()
            if not (title or uri) or uri in seen:
                continue
            seen.add(uri or title)
            citations.append({"title": title or uri, "uri": uri})

    return "\n".join(text_parts).strip(), citations


def _append_sources(text: str, citations: list[dict], include_sources: bool) -> str:
    if not include_sources or not citations:
        return text
    lines = ["", "Sources:"]
    for index, citation in enumerate(citations[:5], start=1):
        uri = citation.get("uri", "")
        title = citation.get("title", uri)
        lines.append(f"{index}. {title}" + (f" — {uri}" if uri else ""))
    return text.rstrip() + "\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Tier 1 — Gemini grounding with multi-key failover
# ---------------------------------------------------------------------------

def _gemini_search(
    query: str,
    *,
    mode: str = "search",
    items=None,
    aspect: str = "general",
    time_range: str = "",
    region: str = "",
    include_sources: bool = True,
) -> str:
    from api_keys import generate_with_retry

    item_list = _normalize_items(items)
    prompt = _build_prompt(query, mode, item_list, aspect, time_range, region)
    config = _config_for_grounding(prompt)

    response = None
    last_error = None
    for model in (MODEL_FLASH_LITE, MODEL_FLASH):
        try:
            response = generate_with_retry(model=model, contents=prompt, config=config)
            break
        except Exception as exc:
            last_error = exc
    if response is None:
        raise last_error or RuntimeError("Gemini search unavailable")

    text, citations = _extract_response(response)
    if not text.strip():
        raise ValueError("Empty response")
    return _append_sources(text, citations, include_sources)


# ---------------------------------------------------------------------------
# Tier 2 — ddgs / duckduckgo_search
# ---------------------------------------------------------------------------

def _ddgs_import():
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from duckduckgo_search import DDGS as LegacyDDGS
        return LegacyDDGS


def _ddg_search(
    query: str,
    max_results: int = 6,
    *,
    mode: str = "search",
    time_range: str = "",
    region: str = "",
) -> list[dict]:
    DDGS = _ddgs_import()
    max_results = max(1, min(10, int(max_results or 5)))
    kwargs: dict = {"max_results": max_results}
    if region:
        kwargs["region"] = region
    timelimit = _TIME_LIMITS.get(str(time_range or "").strip().lower())
    if timelimit:
        kwargs["timelimit"] = timelimit

    results: list[dict] = []
    with DDGS() as ddg:
        method_name = "news" if str(mode).strip().lower() == "news" else "text"
        method = getattr(ddg, method_name)

        def invoke(options: dict):
            return list(method(query, **options))

        try:
            raw = invoke(kwargs)
        except TypeError:
            # Older/newer ddgs releases expose different keyword sets.
            raw = []
            for key in list(kwargs):
                reduced = dict(kwargs)
                reduced.pop(key)
                try:
                    raw = invoke(reduced)
                    break
                except TypeError:
                    continue
            if not raw:
                raw = invoke({})

        for item in raw:
            if not isinstance(item, dict):
                continue
            results.append({
                "title": str(item.get("title") or "").strip(),
                "snippet": str(item.get("body") or item.get("snippet") or "").strip(),
                "url": str(item.get("href") or item.get("url") or "").strip(),
                "date": str(item.get("date") or "").strip(),
            })
    return results


def _format_ddg(query: str, results: list) -> str:
    if not results:
        return f"No results found for: {query}"
    lines = [f"Search results for: {query}", ""]
    for index, item in enumerate(results, 1):
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or item.get("body") or "").strip()
        url = str(item.get("url") or item.get("href") or "").strip()
        date = str(item.get("date") or "").strip()
        heading = f"{index}. {title}" if title else f"{index}. Result"
        if date:
            heading += f" ({date})"
        lines.append(heading)
        if snippet:
            lines.append(f"   {snippet}")
        if url:
            lines.append(f"   {url}")
        lines.append("")
    return "\n".join(lines).strip()


def _compare(items, aspect: str) -> str:
    item_list = _normalize_items(items)
    if not item_list:
        return "Please tell me what to compare, sir."
    query = f"Compare {', '.join(item_list)} in terms of {aspect}. Give specific facts and data."
    try:
        return _gemini_search(
            query, mode="compare", items=item_list, aspect=aspect
        )
    except Exception as exc:
        print(f"[WebSearch] Gemini compare failed: {exc}")
        all_results = {}
        for item in item_list:
            try:
                all_results[item] = _ddg_search(f"{item} {aspect}", max_results=3)
            except Exception:
                all_results[item] = []
        lines = [f"Comparison — {aspect.upper()}", "─" * 40]
        for item in item_list:
            lines.extend(["", f"▸ {item}"])
            for result in all_results.get(item, [])[:2]:
                if result.get("snippet"):
                    lines.append(f"  • {result['snippet']}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tier 3 — zero-dependency HTTP fallbacks
# ---------------------------------------------------------------------------

def _instant_answer(query: str) -> str:
    try:
        import requests
    except ImportError:
        return ""

    try:
        response = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            headers={"User-Agent": "Mozilla/5.0 (compatible; Friday/1.0)"},
            timeout=6,
        )
        data = response.json() if hasattr(response, "json") else {}
        if not isinstance(data, dict):
            data = {}
        abstract = str(data.get("AbstractText") or "").strip()
        answer = str(data.get("Answer") or "").strip()
        if abstract or answer:
            text = abstract or answer
            source = str(data.get("AbstractURL") or "").strip()
            return text + (f"\nSource: {source}" if source else "")
        topics = data.get("RelatedTopics") or []
        for topic in topics[:3]:
            if isinstance(topic, dict):
                snippet = str(topic.get("Text") or "").strip()
                if snippet:
                    return snippet
    except Exception as exc:
        print(f"[WebSearch] Instant answer unavailable: {exc}")

    try:
        response = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(query)}",
            headers={"User-Agent": "Mozilla/5.0 (compatible; Friday/1.0)"},
            timeout=6,
        )
        data = response.json() if hasattr(response, "json") else {}
        if isinstance(data, dict):
            summary = str(data.get("extract") or "").strip()
            if summary:
                return summary
    except Exception as exc:
        print(f"[WebSearch] Wikipedia fallback unavailable: {exc}")
    return ""


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

def web_search(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    if not isinstance(params, dict):
        return "Please provide a valid search request, sir."

    query = str(params.get("query") or "").strip()
    items = _normalize_items(params.get("items"))
    mode = str(params.get("mode") or "search").strip().lower()
    aspect = str(params.get("aspect") or "general").strip()
    time_range = str(params.get("time_range") or "").strip().lower()
    region = str(params.get("region") or "").strip()
    include_sources = params.get("include_sources", True)
    include_sources = True if include_sources is None else bool(include_sources)

    try:
        max_results = int(params.get("max_results") or 5)
    except (TypeError, ValueError):
        max_results = 5
    max_results = max(1, min(10, max_results))

    if not query and not items:
        return "Please provide a search query, sir."
    if items and mode not in ("compare", "news"):
        mode = "compare"

    fresh = bool(params.get("fresh") or params.get("no_cache"))
    cache_params = dict(params, mode=mode, items=items, max_results=max_results,
                        time_range=time_range, region=region)
    key = _cache_key(cache_params)
    if not fresh:
        cached = _cache_get(key)
        if cached:
            if session_memory:
                try:
                    session_memory.set_last_search(query=query or ", ".join(items),
                                                   response=cached)
                except Exception:
                    pass
            return cached

    if player:
        try:
            player.write_log(f"[Search] {query or ', '.join(items)}")
        except Exception:
            pass
    print(f"[WebSearch] Query: {query!r} Mode: {mode}")

    result = ""
    kwargs = dict(mode=mode, items=items, aspect=aspect, time_range=time_range,
                  region=region, include_sources=include_sources)
    try:
        if mode == "compare" and items:
            result = _compare(items, aspect)
        else:
            result = _gemini_search(query, **kwargs)
    except Exception as exc:
        print(f"[WebSearch] Gemini unavailable ({exc}); trying DuckDuckGo")
        try:
            hits = _ddg_search(query or ", ".join(items), max_results=max_results,
                               mode=mode, time_range=time_range, region=region)
            result = _format_ddg(query, hits)
            if result.startswith("No results found"):
                result = _instant_answer(query) if query else ""
        except Exception as ddg_exc:
            print(f"[WebSearch] ddgs unavailable ({ddg_exc}); trying instant answers")
            result = _instant_answer(query) if query else ""

    result = str(result or "").strip()
    if not result:
        return f"Search failed, sir. No results for: {query or ', '.join(items)}"

    _cache_put(key, result)
    if session_memory:
        try:
            session_memory.set_last_search(
                query=query or ", ".join(items), response=result
            )
        except Exception:
            pass
    return result
