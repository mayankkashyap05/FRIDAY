"""Fully offline combinatorial tests for actions.translate."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from actions import translate as tr
from config.models import MODEL_FLASH, MODEL_FLASH_LITE


@pytest.fixture(autouse=True)
def _clear_cache():
    with tr._CACHE_LOCK:
        tr._CACHE.clear()
    yield
    with tr._CACHE_LOCK:
        tr._CACHE.clear()


def _patch_generate(monkeypatch, payload, *, fail_models=()):
    import api_keys

    calls = []

    def fake(model, contents, config=None):
        calls.append((model, contents, config))
        if model in fail_models:
            raise RuntimeError(f"{model} unavailable")
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return SimpleNamespace(text=text)

    monkeypatch.setattr(api_keys, "generate_with_retry", fake)
    return calls


# ---------------------------------------------------------------------------
# All action modes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tone", ["natural", "formal", "casual", "literal", "business"])
def test_translate_tones_use_lite_model(monkeypatch, tone):
    calls = _patch_generate(monkeypatch, "BONJOUR")
    result = tr.translate_action({
        "text": "Hello world", "to": "French", "tone": tone, "fresh": True,
    })
    assert result == "BONJOUR"
    assert calls[0][0] == MODEL_FLASH_LITE
    assert tone in calls[0][1]


def test_translate_preserves_placeholders_in_prompt(monkeypatch):
    calls = _patch_generate(monkeypatch, "Résultat: {name}")
    result = tr.translate_action({"text": "Result: {name}", "to": "fr"})
    assert result == "Résultat: {name}"
    assert "{placeholders}" in calls[0][1]


def test_detect_returns_structured_details(monkeypatch):
    _patch_generate(monkeypatch, {
        "language": "Hindi", "code": "hi", "script": "Devanagari",
        "summary": "A greeting in Hindi.",
    })
    result = tr.translate_action({"action": "detect", "text": "नमस्ते"})
    assert "Hindi (hi)" in result and "Devanagari" in result
    assert "A greeting" in result


def test_transliterate_uses_latin_target(monkeypatch):
    calls = _patch_generate(monkeypatch, "Namaste")
    result = tr.translate_action({"action": "romanize", "text": "नमस्ते"})
    assert result == "Namaste"
    assert calls[0][0] == MODEL_FLASH_LITE
    assert "Latin" in calls[0][1]


def test_explain_uses_flash_and_formats_notes(monkeypatch):
    calls = _patch_generate(monkeypatch, {
        "translation": "Hello", "pronunciation": "namaste",
        "notes": ["Formal greeting", "Uses oblique case"],
    })
    result = tr.translate_action({"action": "explain", "text": "नमस्ते", "to": "English"})
    assert "Translation: Hello" in result
    assert "Pronunciation: namaste" in result
    assert "Formal greeting" in result
    assert calls[0][0] == MODEL_FLASH


def test_clipboard_read_and_optional_copy(monkeypatch):
    monkeypatch.setattr(tr, "_clipboard_read", lambda: "copy me")
    _patch_generate(monkeypatch, "copié")
    written = []
    monkeypatch.setattr(tr, "_clipboard_write", lambda text: written.append(text) or True)
    plain = tr.translate_action({"action": "clipboard", "to": "French", "fresh": True})
    copied = tr.translate_action({
        "action": "clipboard", "to": "French", "copy": True, "fresh": True,
    })
    assert plain == "copié" and "Copied to clipboard" in copied
    assert written == ["copié"]


def test_use_clipboard_when_text_missing(monkeypatch):
    monkeypatch.setattr(tr, "_clipboard_read", lambda: "from clipboard")
    _patch_generate(monkeypatch, "translated")
    assert tr.translate_action({"use_clipboard": True, "to": "French"}) == "translated"


def test_batch_keeps_input_alignment(monkeypatch):
    calls = _patch_generate(monkeypatch, {
        "translations": ["Un", "Deux", "Trois"],
    })
    result = tr.translate_action({
        "action": "batch", "texts": ["One", "Two", "Three"], "to": "French",
    })
    assert result.splitlines() == ["1. Un", "2. Deux", "3. Trois"]
    assert calls[0][0] == MODEL_FLASH


def test_batch_short_response_fills_missing_translations(monkeypatch):
    _patch_generate(monkeypatch, {"translations": ["Only one"]})
    calls = []
    monkeypatch.setattr(tr, "_translate_chunk", lambda *a: "fallback")
    result = tr.translate_action({"action": "batch", "texts": ["A", "B"]})
    assert result == "1. Only one\n2. fallback"


# ---------------------------------------------------------------------------
# Language codes, chunking, cache, failure handling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code,name", [
    ("hi", "Hindi"), ("as", "Assamese"), ("bn", "Bengali"), ("ta", "Tamil"),
    ("ja", "Japanese"), ("ru", "Russian"), ("zh", "Chinese"), ("ar", "Arabic"),
    ("uz", "Uzbek"), ("es", "Spanish"), ("Klingon", "Klingon"),
])
def test_language_normalization(code, name):
    assert tr._language(code) == name


def test_chunk_text_splits_long_input_without_loss():
    text = ("Paragraph one. " * 300) + "\n\n" + ("Paragraph two. " * 300)
    chunks = tr._chunk_text(text, limit=400)
    assert len(chunks) > 1
    assert all(len(chunk) <= 400 for chunk in chunks)
    assert "".join(chunks).replace("\n", " ").split() == text.replace("\n", " ").split()


def test_long_translation_reassembles_all_chunks(monkeypatch):
    text = ("Sentence one. " * 400) + "\n\n" + ("Sentence two. " * 400)
    seen = []
    monkeypatch.setattr(tr, "_translate_chunk", lambda chunk, *a: seen.append(chunk) or chunk)
    result = tr.translate_action({"text": text, "to": "French"})
    assert len(seen) > 1 and all(len(chunk) <= tr.CHUNK_LIMIT for chunk in seen)
    assert "Sentence one" in result and "Sentence two" in result
    assert len(result) >= len(text) - 10


def test_model_failover_lite_to_flash(monkeypatch):
    calls = _patch_generate(monkeypatch, "Fallback result",
                            fail_models=(MODEL_FLASH_LITE,))
    assert tr.translate_action({"text": "hello", "fresh": True}) == "Fallback result"
    assert [call[0] for call in calls] == [MODEL_FLASH_LITE, MODEL_FLASH]


def test_ttl_cache_hit_and_no_cache_bypass(monkeypatch):
    calls = _patch_generate(monkeypatch, "Cached")
    params = {"text": "hello", "to": "French"}
    assert tr.translate_action(params) == "Cached"
    assert tr.translate_action(params) == "Cached"
    assert len(calls) == 1
    assert tr.translate_action(dict(params, no_cache=True)) == "Cached"
    assert len(calls) == 2


def test_empty_and_malformed_parameters():
    assert "What text" in tr.translate_action(None)
    assert "What text" in tr.translate_action({"text": "   "})
    assert "list of texts" in tr.translate_action({"action": "batch"})
    assert "Unknown translation action" in tr.translate_action(
        {"action": "bogus", "text": "hi"}
    )


def test_api_errors_are_honest(monkeypatch):
    import api_keys

    def fail(**kwargs):
        raise RuntimeError("no keys")

    monkeypatch.setattr(api_keys, "generate_with_retry", fail)
    assert "Translation failed" in tr.translate_action({"text": "hello"})


def test_session_memory_called(monkeypatch):
    _patch_generate(monkeypatch, "Result")
    calls = []
    memory = SimpleNamespace(
        set_last_search=lambda **kwargs: calls.append(kwargs)
    )
    tr.translate_action({"text": "hello"}, session_memory=memory)
    assert calls and calls[0]["query"].startswith("translate to English")


def test_source_uses_shared_model_constants():
    source = (tr.__file__ and __import__("pathlib").Path(tr.__file__).read_text())
    assert "MODEL_FLASH_LITE" in source and "MODEL_FLASH" in source
    assert "gemini-2.5-flash" not in source


def test_string_false_booleans_are_not_true(monkeypatch):
    calls = _patch_generate(monkeypatch, "Result")
    params = {"text": "hello", "no_cache": "false"}
    tr.translate_action(params)
    tr.translate_action(params)
    assert len(calls) == 1  # "false" must not bypass cache

    written = []
    monkeypatch.setattr(tr, "_clipboard_read", lambda: "hello")
    monkeypatch.setattr(tr, "_clipboard_write", lambda text: written.append(text) or True)
    tr.translate_action({"action": "clipboard", "copy": "false", "fresh": True})
    assert written == []
