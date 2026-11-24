"""100%-offline combinatorial tests for actions.pdf_reader."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from actions import document_qa
from actions import pdf_reader as pr
from config.models import MODEL_FLASH, MODEL_FLASH_LITE


class FakePage:
    def __init__(self, text="", strict=False):
        self.text = text
        self.strict = strict
        self.calls = []

    def extract_text(self, **kwargs):
        self.calls.append(kwargs)
        if self.strict and "extraction_mode" in kwargs:
            raise TypeError("unexpected keyword argument")
        return self.text


class FakeReader:
    instances = []

    def __init__(self, pages=None, encrypted=False, password="secret", metadata=None):
        self.pages = pages if pages is not None else [FakePage("Page one.")]
        self.is_encrypted = encrypted
        self.password = password
        self.metadata = metadata or {"title": "Demo", "author": "Ada", "subject": "Tests"}
        FakeReader.instances.append(self)

    def decrypt(self, value):
        return 1 if value == self.password else 0


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    with pr._CACHE_LOCK:
        pr._PDF_CACHE.clear()
    FakeReader.instances = []
    monkeypatch.setattr(pr, "PdfReader", FakeReader, raising=False)
    monkeypatch.setattr(pr, "_PYPdf", True)
    monkeypatch.setattr(pr, "generate_with_retry", lambda **kw: types.SimpleNamespace(text="OK"))
    yield
    with pr._CACHE_LOCK:
        pr._PDF_CACHE.clear()


def _pdf(tmp_path, name="demo.pdf", size=1024):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def _fake_part_module(monkeypatch):
    seen = {}

    def from_bytes(*, data, mime_type):
        seen["mime"] = mime_type
        seen["size"] = len(data)
        return f"PART:{len(data)}"

    fake_genai = types.ModuleType("google.genai")
    fake_genai.types = types.SimpleNamespace(
        Part=types.SimpleNamespace(from_bytes=from_bytes))
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    return seen


# ---------------------------------------------------------------------------
# Extraction, pages, layout, encryption, cache
# ---------------------------------------------------------------------------

def test_extract_text_marks_pages_and_selects_ranges(tmp_path):
    path = _pdf(tmp_path)
    pr._PDF_CACHE[_pr_key := pr._cache_key(path, False)] = [
        "one", "two", "three", "four"]
    text = pr._extract_text(path, pages="1, 3-4")
    assert "[Page 1]" in text and "[Page 3]" in text and "[Page 4]" in text
    assert "[Page 2]" not in text
    assert "[Page 9]" not in pr._extract_text(path, pages="9")


def test_layout_mode_falls_back_on_older_pypdf(tmp_path):
    path = _pdf(tmp_path)
    page = FakePage("Layout text", strict=True)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pr, "PdfReader", lambda _p: types.SimpleNamespace(
        pages=[page], is_encrypted=False, metadata={}))
    text = pr._extract_text(path, layout=True)
    monkeypatch.undo()
    assert "Layout text" in text
    assert any("extraction_mode" in call for call in page.calls)


def test_encrypted_pdf_password_guard(tmp_path):
    path = _pdf(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pr, "PdfReader", lambda _p: FakeReader(encrypted=True))
    with pytest.raises(pr.PdfPasswordRequired):
        pr._extract_text(path, password="wrong")
    assert "Page one" in pr._extract_text(path, password="secret")
    monkeypatch.undo()


def test_page_cache_keyed_by_path_mtime_and_layout(tmp_path):
    path = _pdf(tmp_path)
    pr._cached_pages(path, False)
    pr._cached_pages(path, False)
    assert len(FakeReader.instances) == 1
    pr._cached_pages(path, True)
    assert len(FakeReader.instances) == 2
    import time
    import os
    os.utime(path, (time.time() + 5, time.time() + 5))
    pr._cached_pages(path, False)
    assert len(FakeReader.instances) == 3
    assert len(pr._PDF_CACHE) == 3


# ---------------------------------------------------------------------------
# Chunking and retrieval
# ---------------------------------------------------------------------------

def test_chunk_text_splits_on_sentences_with_overlap():
    text = " ".join(f"Sentence {i} explains a separate idea." for i in range(80))
    chunks = pr._chunk_text(text, chunk_size=260, overlap=60)
    assert len(chunks) > 2
    assert all(len(c) <= 330 for c in chunks)
    shared = any(chunks[i][-12:] in chunks[i + 1] for i in range(len(chunks) - 1))
    assert shared


def test_pick_chunks_weights_terms_phrases_headings_and_numbers():
    chunks = [
        "[Page 1] unrelated introduction to weather.",
        "[Page 2] The warranty period is 24 months and includes parts.",
        "[Page 3] Notes about shipping and returns.",
    ]
    picked = pr._pick_chunks("What is the warranty period for 24 months?", chunks)
    assert picked[0] == chunks[1]
    assert pr._pick_chunks("nothing relevant", chunks)[0] == chunks[0]


def test_extract_and_chunk_remain_document_qa_compatible():
    assert document_qa._chunk_text is pr._chunk_text
    assert document_qa._pick_chunks is pr._pick_chunks
    assert document_qa._ask_gemini is pr._ask_gemini


# ---------------------------------------------------------------------------
# Gemini routing and multimodal fallback
# ---------------------------------------------------------------------------

def test_ask_gemini_uses_generate_with_retry_and_flash_model(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(text="answer")

    monkeypatch.setattr(pr, "generate_with_retry", fake)
    assert pr._ask_gemini("q", "ctx") == "answer"
    assert calls[0]["model"] == MODEL_FLASH
    assert "ctx" in calls[0]["contents"]


def test_quota_error_falls_back_to_flash_lite_only(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == MODEL_FLASH:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return types.SimpleNamespace(text="fallback answer")

    monkeypatch.setattr(pr, "generate_with_retry", fake)
    assert pr._ask_gemini("q", "ctx") == "fallback answer"
    assert calls == [MODEL_FLASH, MODEL_FLASH_LITE]


def test_non_quota_error_does_not_fallback(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs["model"])
        raise ValueError("bad argument")

    monkeypatch.setattr(pr, "generate_with_retry", fake)
    assert "failed" in pr._ask_gemini("q", "ctx")
    assert calls == [MODEL_FLASH]


def test_scanned_pdf_uses_native_multimodal_part(monkeypatch, tmp_path):
    path = _pdf(tmp_path, "scan.pdf", size=2048)
    seen = _fake_part_module(monkeypatch)
    calls = []
    monkeypatch.setattr(pr, "generate_with_retry", lambda **kw: (
        calls.append(kw) or types.SimpleNamespace(text="visual answer")))
    monkeypatch.setattr(pr, "_cached_pages", lambda *a, **k: [" "])
    answer = pr.pdf_qa({"path": str(path), "question": "What is this?"})
    assert answer == "visual answer"
    assert seen["mime"] == "application/pdf"
    assert seen["size"] == 2048
    assert calls[0]["model"] == MODEL_FLASH
    assert calls[0]["contents"][0] == "PART:2048"


def test_oversized_pdf_multimodal_graceful_error(monkeypatch, tmp_path):
    path = _pdf(tmp_path, "large.pdf", size=pr.MAX_INLINE_BYTES + 1)
    monkeypatch.setattr(pr, "_cached_pages", lambda *a, **k: [" "])
    result = pr.pdf_qa({"path": str(path), "question": "Read this", "visual": True})
    assert "20 MB" in result


def test_visual_flag_forces_multimodal_on_text_pdf(monkeypatch, tmp_path):
    path = _pdf(tmp_path)
    monkeypatch.setattr(pr, "_cached_pages", lambda *a, **k: ["Text " * 100])
    monkeypatch.setattr(pr, "_multimodal_answer", lambda *a, **k: "visual")
    assert pr.pdf_qa({"path": str(path), "question": "q", "visual": True}) == "visual"


# ---------------------------------------------------------------------------
# read_pdf actions and safe defaults
# ---------------------------------------------------------------------------

def test_read_pdf_actions(monkeypatch, tmp_path):
    path = _pdf(tmp_path)
    monkeypatch.setattr(pr, "_cached_pages", lambda *a, **k: [
        "Alpha beta. " * 20, "Gamma delta. " * 20])
    monkeypatch.setattr(pr, "_ask_gemini", lambda q, c: "summary answer")
    assert "Preview" in pr.read_pdf({"path": str(path)})
    assert pr.read_pdf({"path": str(path), "action": "pages", "pages": "2"}).startswith("[Page 2]")
    assert "Alpha" in pr.read_pdf({"path": str(path), "action": "search", "query": "alpha"})
    assert "summary answer" in pr.read_pdf({"path": str(path), "action": "summary"})
    meta = pr.read_pdf({"path": str(path), "action": "metadata"})
    assert "pages" in meta and "encrypted" in meta


def test_parameters_none_and_missing_file():
    assert "couldn't find" in pr.read_pdf(None).lower()
    assert "couldn't find" in pr.pdf_qa(None).lower()
