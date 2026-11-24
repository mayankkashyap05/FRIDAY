# actions/translate.py
# Friday — fast, context-aware translation via Gemini with multi-key rotation.
#
# Modes: translate | detect | transliterate | explain | clipboard | batch
# All heavy imports are lazy so this module imports cleanly without google-genai,
# pyperclip or the desktop clipboard backend.

from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

from config.models import MODEL_FLASH, MODEL_FLASH_LITE

_CACHE_TTL_S = 600.0
_CACHE_MAX = 128
_CACHE_LOCK = threading.RLock()
_CACHE: OrderedDict[tuple, tuple[float, str]] = OrderedDict()
CHUNK_LIMIT = 4000

_LANGUAGES = {
    "en": "English", "english": "English",
    "hi": "Hindi", "hindi": "Hindi",
    "as": "Assamese", "assamese": "Assamese",
    "bn": "Bengali", "bangla": "Bengali", "bengali": "Bengali",
    "ta": "Tamil", "tamil": "Tamil",
    "te": "Telugu", "telugu": "Telugu",
    "mr": "Marathi", "marathi": "Marathi",
    "gu": "Gujarati", "gujarati": "Gujarati",
    "ur": "Urdu", "urdu": "Urdu",
    "uz": "Uzbek", "uzbek": "Uzbek",
    "tr": "Turkish", "turkish": "Turkish",
    "es": "Spanish", "spanish": "Spanish",
    "fr": "French", "french": "French",
    "de": "German", "german": "German",
    "ja": "Japanese", "japanese": "Japanese",
    "ko": "Korean", "korean": "Korean",
    "zh": "Chinese", "cn": "Chinese", "chinese": "Chinese",
    "ar": "Arabic", "arabic": "Arabic",
    "ru": "Russian", "russian": "Russian",
    "pt": "Portuguese", "portuguese": "Portuguese",
    "it": "Italian", "italian": "Italian",
    "nl": "Dutch", "dutch": "Dutch",
    "pl": "Polish", "polish": "Polish",
    "fa": "Persian", "persian": "Persian",
    "th": "Thai", "thai": "Thai",
    "vi": "Vietnamese", "vietnamese": "Vietnamese",
    "id": "Indonesian", "indonesian": "Indonesian",
    "sw": "Swahili", "swahili": "Swahili",
    "auto": "auto-detect",
}

_TONES = {"natural", "formal", "casual", "literal", "business"}


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = get_base_dir() / "config" / "api_keys.json"


def _get_api_key() -> str:
    """Legacy helper kept for compatibility."""
    from api_keys import next_key
    return next_key()


def _language(value, default: str = "English") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    return _LANGUAGES.get(text.lower(), text)


def _cache_key(action: str, text: str, source: str, target: str,
               tone: str) -> tuple:
    return (action, text, source, target, tone)


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


def _config(json_mode: bool = False):
    try:
        from google.genai import types
        kwargs = {"temperature": 0.2}
        if json_mode:
            kwargs["response_mime_type"] = "application/json"
        return types.GenerateContentConfig(**kwargs)
    except Exception:
        config = {"temperature": 0.2}
        if json_mode:
            config["response_mime_type"] = "application/json"
        return config


def _extract_text(response) -> str:
    text = getattr(response, "text", None)
    if text:
        return str(text).strip()
    parts = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            snippet = getattr(part, "text", None)
            if snippet:
                parts.append(str(snippet))
    return "\n".join(parts).strip()


def _parse_json(text: str) -> dict | list | None:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    else:
        start, end = text.find("["), text.rfind("]")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        return json.loads(text)
    except Exception:
        return None


def _generate(prompt: str, *, prefer_lite: bool = True,
              json_mode: bool = False) -> str:
    """Generate with multi-key rotation; lite first with flash failover."""
    from api_keys import generate_with_retry

    models = (MODEL_FLASH_LITE, MODEL_FLASH) if prefer_lite else (MODEL_FLASH,)
    last_error = None
    for model in models:
        try:
            response = generate_with_retry(
                model=model, contents=prompt, config=_config(json_mode)
            )
            text = _extract_text(response)
            if not text:
                raise ValueError("Empty model response")
            return text
        except Exception as exc:
            last_error = exc
    raise last_error or RuntimeError("Translation service unavailable")


def _chunk_text(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Split on paragraph/sentence boundaries, never truncating content."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    paragraphs = re.split(r"(\n\s*\n)", text)
    for paragraph in paragraphs:
        if not paragraph:
            continue
        if len(paragraph) <= limit:
            chunks.append(paragraph)
            continue
        # Keeping the captured whitespace preserves paragraph/sentence
        # boundaries when the translated chunks are concatenated again.
        parts = re.split(r"(?<=[.!?।។])(\s+)", paragraph)
        current = ""
        for piece in parts:
            if not piece:
                continue
            if len(piece) > limit:
                if current:
                    chunks.append(current)
                    current = ""
                for offset in range(0, len(piece), limit):
                    chunks.append(piece[offset:offset + limit])
                continue
            if len(current) + len(piece) <= limit:
                current += piece
            else:
                if current:
                    chunks.append(current)
                current = piece
        if current:
            chunks.append(current)
    return chunks


def _clipboard_read() -> str:
    try:
        from actions.clipboard import _clipboard_get
        return str(_clipboard_get() or "").strip()
    except Exception:
        pass
    try:
        import pyperclip
        return str(pyperclip.paste() or "").strip()
    except Exception:
        return ""


def _clipboard_write(text: str) -> bool:
    try:
        from actions.clipboard import _clipboard_set
        if _clipboard_set(text):
            return True
    except Exception:
        pass
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        return False


def _translate_chunk(text: str, source: str, target: str, tone: str) -> str:
    instruction = (
        f"Translate the following text into {target}"
        + (f" from {source}" if source and source != "auto-detect" else
           " (detect the source language yourself)")
        + f". Use a {tone} register. "
        "Preserve markdown, URLs, inline code and {placeholders} exactly. "
        "Output ONLY the translation, with no commentary.\n\nTEXT:\n"
    )
    return _generate(instruction + text).strip()


def _do_translate(text: str, source: str, target: str, tone: str) -> str:
    chunks = _chunk_text(text)
    pieces = [
        chunk if not chunk.strip() else _translate_chunk(chunk, source, target, tone)
        for chunk in chunks
    ]
    return "".join(pieces)


def _do_detect(text: str) -> str:
    prompt = (
        "Identify the language of the text below. Return ONLY JSON: "
        '{"language": "Hindi", "code": "hi", "script": "Devanagari", '
        '"summary": "one short sentence about what the text says"}.\n\nTEXT:\n'
        + text
    )
    data = _parse_json(_generate(prompt)) or {}
    language = _language(data.get("language"), "Unknown")
    code = str(data.get("code") or "").strip()
    script = str(data.get("script") or "Unknown").strip()
    summary = str(data.get("summary") or "").strip()
    return (
        f"Detected language: {language}" + (f" ({code})" if code else "") +
        f". Script: {script}." + (f" Summary: {summary}" if summary else "")
    )


def _do_transliterate(text: str, target: str) -> str:
    prompt = (
        f"Transliterate the following text into {target} using a {target} "
        "script/phonetics. Preserve word boundaries and punctuation. "
        "Output ONLY the transliteration.\n\nTEXT:\n" + text
    )
    return _generate(prompt).strip()


def _do_explain(text: str, source: str, target: str) -> str:
    prompt = (
        f"Translate the text below into {target}"
        + (f" from {source}" if source else "")
        + ". Return ONLY JSON: "
        '{"translation": "...", "pronunciation": "...", '
        '"notes": ["note 1", "note 2", "note 3"]}. '
        " pronunciation may be empty for Latin-script text. "
        "Notes must be concise grammar or nuance points (2-4 bullets).\n\nTEXT:\n"
        + text
    )
    data = _parse_json(_generate(prompt, prefer_lite=False, json_mode=True)) or {}
    translation = str(data.get("translation") or "").strip()
    pronunciation = str(data.get("pronunciation") or "").strip()
    notes = data.get("notes") or []
    lines = [f"Translation: {translation or _do_translate(text, source, target, 'natural')}"]
    if pronunciation:
        lines.append(f"Pronunciation: {pronunciation}")
    if isinstance(notes, list):
        for note in notes[:4]:
            if str(note).strip():
                lines.append("• " + str(note).strip())
    return "\n".join(lines)


def _do_batch(texts: list[str], source: str, target: str, tone: str) -> str:
    numbered = "\n".join(f"{index}. {item}" for index, item in enumerate(texts, 1))
    prompt = (
        f"Translate each numbered item into {target}"
        + (f" from {source}" if source else "")
        + f" using a {tone} register. Preserve markdown, URLs, code and "
        "placeholders. Return ONLY JSON: "
        '{"translations": ["...", "..."]} with exactly one string per item, '
        "in the same order.\n\nITEMS:\n" + numbered
    )
    data = _parse_json(
        _generate(prompt, prefer_lite=False, json_mode=True)
    ) or {}
    results = data.get("translations") or data.get("items") or []
    if not isinstance(results, list):
        results = []
    lines = []
    for index, item in enumerate(texts):
        translated = results[index] if index < len(results) else \
            _translate_chunk(item, source, target, tone)
        lines.append(f"{index + 1}. {translated}")
    return "\n".join(lines)


def translate_action(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Translate, detect, transliterate, explain, clipboard or batch text."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action", "translate")).strip().lower()
    target = _language(params.get("to", params.get("target", "")), "English")
    source = _language(params.get("from", params.get("source", "")), "")
    tone = str(params.get("tone", params.get("register", "natural"))).strip().lower()
    if tone not in _TONES:
        tone = "natural"

    if action in ("transliterate", "romanize"):
        target = _language(params.get("to", "Latin"), "Latin")

    text = str(params.get("text") or "").strip()
    if action == "clipboard" or (not text and params.get("use_clipboard")):
        text = _clipboard_read()

    if action == "batch":
        items = params.get("texts", params.get("items")) or []
        if isinstance(items, str):
            items = [part.strip() for part in items.split("\n") if part.strip()]
        texts = [str(item).strip() for item in items if str(item).strip()]
        if not texts:
            return "Please provide a list of texts to translate, sir."
        key = _cache_key(action, "|".join(texts), source, target, tone)
    else:
        if not text:
            return "What text should I translate, sir?"
        key = _cache_key(action, text, source, target, tone)

    fresh = bool(params.get("no_cache") or params.get("fresh"))
    if not fresh:
        cached = _cache_get(key)
        if cached:
            return cached

    try:
        if action in ("detect", "detect_language", "language"):
            result = _do_detect(text)
        elif action in ("transliterate", "romanize"):
            result = _do_transliterate(text, target)
        elif action in ("explain", "breakdown", "nuance"):
            result = _do_explain(text, source, target)
        elif action == "clipboard":
            result = _do_translate(text, source, target, tone)
            if bool(params.get("copy")):
                _clipboard_write(result)
                result += " (Copied to clipboard.)"
        elif action == "batch":
            result = _do_batch(texts, source, target, tone)
        elif action in ("translate", "translation", ""):
            result = _do_translate(text, source, target, tone)
        else:
            return (
                f"Unknown translation action: {action}, sir. Use translate | "
                "detect | transliterate | explain | clipboard | batch"
            )
    except Exception as exc:
        return f"Translation failed: {exc}"

    _cache_put(key, result)
    if session_memory:
        try:
            session_memory.set_last_search(
                query=f"translate to {target}: {text[:80]}", response=result
            )
        except Exception:
            pass
    return result
