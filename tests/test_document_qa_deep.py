"""Offline deep tests for the document_qa extraction/cache upgrade."""

import io
import json
import os
import zipfile
from unittest.mock import MagicMock

import pytest

from actions import document_qa as dq


def _write_docx(path):
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    xml = f'''<?xml version="1.0"?>
<w:document xmlns:w="{ns}"><w:body>
  <w:p><w:r><w:t>Hello DOCX</w:t></w:r></w:p>
  <w:tbl><w:tr>
    <w:tc><w:p><w:r><w:t>Name</w:t></w:r></w:p></w:tc>
    <w:tc><w:p><w:r><w:t>Age</w:t></w:r></w:p></w:tc>
  </w:tr><w:tr>
    <w:tc><w:p><w:r><w:t>Aman</w:t></w:r></w:p></w:tc>
    <w:tc><w:p><w:r><w:t>30</w:t></w:r></w:p></w:tc>
  </w:tr></w:tbl>
</w:body></w:document>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


def test_docx_elementtree_reads_paragraphs_and_tables(tmp_path):
    path = tmp_path / "sample.docx"
    _write_docx(path)
    text = dq._extract_docx(path)
    assert "Hello DOCX" in text
    assert "Name | Age" in text
    assert "Aman | 30" in text


def test_pptx_slide_headers_shapes_and_notes(tmp_path):
    from pptx import Presentation

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(0, 0, 100, 100)
    box.text = "Revenue grew"
    slide.notes_slide.notes_text_frame.text = "Remember to chart Q4"
    path = tmp_path / "deck.pptx"
    prs.save(str(path))
    text = dq._extract_pptx(path)
    assert "[Slide 1]" in text
    assert "Revenue grew" in text
    assert "[Notes] Remember to chart Q4" in text


def test_xlsx_header_and_blank_row_cap_with_close(tmp_path):
    from openpyxl import load_workbook, Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Name", "Score"])
    for _ in range(6):
        ws.append([None, None])
    ws.append(["After blanks", 1])
    path = tmp_path / "book.xlsx"
    wb.save(str(path))
    text = dq._extract_xlsx(path)
    assert "[Sheet: Data]" in text
    assert "[Header] Name | Score" in text
    assert "After blanks" not in text
    assert load_workbook(str(path)) is not None


@pytest.mark.parametrize("name,content,expected", [
    ("data.csv", "a,b\n1,2", "a | b\n1 | 2"),
    ("data.tsv", "a\tb\n1\t2", "a | b\n1 | 2"),
    ("data.json", '{"x": 1}', '"x": 1'),
])
def test_structured_formats(tmp_path, name, content, expected):
    path = tmp_path / name
    path.write_text(content)
    assert expected in dq._extract_text(path)


def test_encoding_fallback_for_legacy_text(tmp_path):
    path = tmp_path / "legacy.txt"
    path.write_bytes("Café naîve".encode("cp1252"))
    assert "Café" in dq._extract_text(path)


def _streaming_response(data: bytes, url="https://example.com/doc.txt"):
    response = MagicMock()
    response.status_code = 200
    response.url = url
    response.headers = {"Content-Length": str(len(data))}
    response.iter_content.return_value = [data[:4], data[4:]]
    return response


def test_url_extraction_streams_with_byte_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(dq, "_is_private_target", lambda url: False)
    monkeypatch.setattr("requests.get", lambda *a, **kw: _streaming_response(b"hello streaming world"))
    assert "hello streaming world" in dq._extract_text("https://example.com/doc.txt")

    huge = MagicMock()
    huge.headers = {"Content-Length": "99999999"}
    huge.iter_content.return_value = [b"x" * (5 * 1024 * 1024 + 1)]
    huge.status_code = 200
    huge.url = "https://example.com/big.txt"
    monkeypatch.setattr("requests.get", lambda *a, **kw: huge)
    with pytest.raises(RuntimeError, match="too large"):
        dq._extract_text("https://example.com/big.txt", fresh=True)


def test_remote_pdf_uses_pypdf_from_bytes(monkeypatch):
    monkeypatch.setattr(dq, "_is_private_target", lambda url: False)
    monkeypatch.setattr("requests.get", lambda *a, **kw: _streaming_response(b"%PDF-fake", "https://example.com/a.pdf"))
    seen = []
    monkeypatch.setattr(dq, "_remote_pdf", lambda data: seen.append(data) or "PDF TEXT")
    assert dq._extract_text("https://example.com/a.pdf", fresh=True) == "PDF TEXT"
    assert seen == [b"%PDF-fake"]


def test_html_boilerplate_removed_and_article_preferred():
    html = """<html><head><script>alert(1)</script></head><body>
      <nav>Menu</nav><article><h1>Real headline</h1><p>Useful body text.</p></article>
      <footer>Copyright</footer></body></html>"""
    text = dq._strip_html(html)
    assert "Real headline" in text and "Useful body text" in text
    assert "Menu" not in text and "Copyright" not in text and "alert" not in text


def test_cache_bounded_ttl_and_fresh_bypass(monkeypatch, tmp_path):
    dq._CACHE.clear()
    calls = []
    path = tmp_path / "cached.txt"
    path.write_text("original")
    monkeypatch.setattr(dq, "_extract_text_uncached", lambda src: calls.append(src) or "value")

    dq._extract_text(path)
    dq._extract_text(path)
    assert len(calls) == 1
    dq._extract_text(path, fresh=True)
    assert len(calls) == 2
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))
    dq._extract_text(path)
    assert len(calls) == 3

    for index in range(35):
        (tmp_path / f"f{index}.txt").write_text(str(index))
        dq._extract_text(tmp_path / f"f{index}.txt")
    assert len(dq._CACHE) <= dq._CACHE_LIMIT
    dq._CACHE.clear()


def test_read_document_actions_offline(monkeypatch, tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("Alpha line. Beta line. Gamma line.")
    monkeypatch.setattr(dq, "_resolve_source", lambda raw: str(path))
    monkeypatch.setattr(dq, "_extract_text", lambda source: path.read_text())

    assert "Alpha line" in dq.read_document({"source": str(path)})
    assert "Summary" in dq.read_document({"source": str(path), "action": "summary"})
    matches = dq.read_document({"source": str(path), "action": "search", "query": "Beta"})
    assert "1 match" in matches and "Beta" in matches
    stats = dq.read_document({"source": str(path), "action": "stats"})
    assert "Characters:" in stats and "Words:" in stats
    assert "no extractable text" not in stats


def test_document_qa_delegates_to_pdf_reader_helpers(monkeypatch, tmp_path):
    path = tmp_path / "doc.txt"
    path.write_text("content")
    monkeypatch.setattr(dq, "_resolve_source", lambda raw: str(path))
    monkeypatch.setattr(dq, "_extract_text", lambda source: "Alpha context about pricing.")
    chunks = []
    picked = []
    asked = []

    monkeypatch.setattr(dq, "_chunk_text", lambda text: chunks.append(text) or ["c1", "c2"])
    monkeypatch.setattr(dq, "_pick_chunks", lambda q, ch: picked.append((q, ch)) or ["c1"])
    monkeypatch.setattr(dq, "_ask_gemini", lambda q, ctx: asked.append((q, ctx)) or "Pricing is 10 USD.")

    result = dq.document_qa({"source": str(path), "question": "What is pricing?"})
    assert "10 USD" in result
    assert chunks and picked and asked
    assert "From " in result


def test_preserved_ssrf_helpers_still_refuse_internal_targets(monkeypatch):
    assert dq._is_private_target("http://127.0.0.1:8000/x")
    assert dq._is_private_target("http://169.254.169.254/latest/")
    assert not dq._is_private_target("http://example.com/x")

    redirect = MagicMock()
    redirect.status_code = 302
    redirect.headers = {"Location": "http://169.254.169.254/internal"}
    monkeypatch.setattr(dq, "_is_private_target", lambda url: False)
    monkeypatch.setattr("requests.get", lambda *a, **kw: redirect)
    with pytest.raises(RuntimeError, match="internal"):
        dq._extract_url("https://example.com/redirect")
