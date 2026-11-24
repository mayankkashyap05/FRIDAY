# actions/pdf_reader.py
# Friday — layout-aware, page-cited PDF reading and question answering.
#
# pypdf and google.genai are optional/ lazily used: the module imports and
# degrades cleanly when either is missing. All model calls go through
# api_keys.generate_with_retry and the shared config.models constants.

from __future__ import annotations

import math
import re
import sys
import threading
from pathlib import Path

from config.models import MODEL_FLASH, MODEL_FLASH_LITE
from api_keys import generate_with_retry

try:
    from pypdf import PdfReader
    _PYPdf = True
except ImportError:                                    # pragma: no cover - environment
    try:
        from PyPDF2 import PdfReader
        _PYPdf = True
    except ImportError:
        PdfReader = None
        _PYPdf = False


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = get_base_dir() / "config" / "api_keys.json"

_SEARCH_DIRS = [
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "Desktop",
    Path.home(),
]

#: Back-compat alias — the real single source of truth is config.models.
MODEL = MODEL_FLASH
CHUNK_SIZE = 2600
TOP_CHUNKS = 3
MAX_INLINE_BYTES = 20 * 1024 * 1024
_CACHE_TTL_S = 0.0            # mtime-keyed, so entries stay valid until the file changes
_CACHE_MAX = 16
_CACHE_LOCK = threading.RLock()
_PDF_CACHE: dict[tuple, list[str]] = {}


class PdfPasswordRequired(Exception):
    """Raised when an encrypted PDF needs a password."""


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _get_api_key() -> str:
    """Kept for callers that still import it; rotation now lives in api_keys."""
    from api_keys import next_key
    return next_key()


def _resolve_pdf(raw: str) -> Path | None:
    """Accept an absolute path, a path relative to base dir, or a bare name."""
    raw = str(raw or "").strip().strip('"')
    if not raw:
        return None
    cand = Path(raw)
    if cand.is_absolute() and cand.exists():
        return cand
    if not cand.suffix:
        cand = cand.with_suffix(".pdf")
    if cand.exists():
        return cand
    for d in _SEARCH_DIRS:
        p = d / cand.name
        if p.exists():
            return p
        if d == Path.home():
            for sub in ("Documents", "Downloads", "Desktop"):
                q = d / sub / cand.name
                if q.exists():
                    return q
    return None


def _open_reader(path: Path, password=None):
    if not _PYPdf:
        raise RuntimeError("pypdf is not installed. Run: pip install pypdf")
    reader = PdfReader(str(path))
    try:
        encrypted = bool(reader.is_encrypted)
    except Exception:
        encrypted = False
    if encrypted:
        result = None
        if password:
            try:
                result = reader.decrypt(str(password))
            except Exception:
                result = None
        if not result:
            raise PdfPasswordRequired(
                f"'{path.name}' is encrypted. Provide the correct password parameter.")
    return reader


def _page_text(page, layout: bool = False) -> str:
    if layout:
        try:
            return str(page.extract_text(extraction_mode="layout") or "")
        except TypeError:
            pass                      # older pypdf: fall through to plain mode
        except Exception:
            return ""
    try:
        return str(page.extract_text() or "")
    except Exception:
        return ""


def _cache_key(path: Path, layout: bool) -> tuple:
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return (str(path), mtime, bool(layout))


def _cached_pages(path: Path, layout: bool = False, password=None) -> list[str]:
    """Thread-safe, mtime-keyed page cache keyed by (path, mtime, layout)."""
    key = _cache_key(path, layout)
    with _CACHE_LOCK:
        if key in _PDF_CACHE:
            return list(_PDF_CACHE[key])
    reader = _open_reader(path, password)
    pages = [_page_text(page, layout) for page in reader.pages]
    with _CACHE_LOCK:
        _PDF_CACHE[key] = pages
        while len(_PDF_CACHE) > _CACHE_MAX:
            _PDF_CACHE.pop(next(iter(_PDF_CACHE)))
    return list(pages)


def _parse_pages(spec, total: int) -> list[int]:
    """Parse '1', '1-3', '1, 3-4' (1-based) into valid page numbers."""
    if spec in (None, "", [], ()):
        return list(range(1, total + 1))
    if isinstance(spec, (list, tuple, set)):
        raw_parts = [str(p) for p in spec]
    else:
        raw_parts = [p.strip() for p in str(spec).split(",")]
    chosen: list[int] = []
    for part in raw_parts:
        if not part:
            continue
        match = re.fullmatch(r"(\d+)\s*(?:-\s*(\d+))?", part)
        if not match:
            continue
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if end < start:
            start, end = end, start
        for number in range(start, min(end, total) + 1):
            if 1 <= number <= total and number not in chosen:
                chosen.append(number)
    return chosen


def _extract_text(path: Path, layout: bool = False, pages=None, password=None) -> str:
    """Extract page-numbered text; one-argument calls stay backward compatible."""
    pages_text = _cached_pages(path, layout=_as_bool(layout), password=password)
    wanted = _parse_pages(pages, len(pages_text))
    return "\n\n".join(f"[Page {n}]\n{pages_text[n - 1]}" for n in wanted)


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = 250) -> list[str]:
    """Split on paragraph/sentence boundaries with a trailing overlap window."""
    text = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    if not text:
        return []
    size = max(200, int(chunk_size or CHUNK_SIZE))
    overlap = max(0, min(int(overlap or 0), size // 2))
    segments = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n{2,}", text) if s.strip()]
    chunks: list[str] = []
    current = ""

    def flush(value: str):
        if value.strip():
            chunks.append(value.strip())

    for segment in segments:
        while len(segment) > size:
            space = segment.rfind(" ", 0, size)
            cut = space if space > size // 2 else size
            flush(current + (" " if current else "") + segment[:cut])
            tail = segment[cut:].strip()
            current = ""
            segment = tail
        if not current:
            current = segment
        elif len(current) + 1 + len(segment) <= size:
            current += " " + segment
        else:
            flush(current)
            tail = current[-overlap:] if overlap else ""
            space = tail.find(" ")
            if space >= 0:
                tail = tail[space + 1:]
            current = (tail + " " + segment).strip()
    flush(current)
    return chunks


_STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "what", "where",
    "when", "which", "how", "about", "are", "was", "pdf", "file", "tell",
    "does", "did", "into", "their", "there", "have", "has", "please",
}


def _terms(text: str) -> list[str]:
    return [w for w in re.findall(r"\w{3,}", str(text or "").lower())
            if w not in _STOP_WORDS]


def _pick_chunks(question: str, chunks: list[str], top_k: int = TOP_CHUNKS) -> list[str]:
    """IDF-weighted term frequency + exact phrase, heading and number bonuses."""
    if not chunks:
        return []
    k = max(1, min(int(top_k or TOP_CHUNKS), len(chunks)))
    docs = [set(_terms(chunk)) for chunk in chunks]
    n = len(chunks)
    idf = {word: math.log((n + 1) / (sum(word in d for d in docs) + 1)) + 1.0
           for doc in docs for word in doc}
    q_terms = _terms(question)
    q_counts: dict[str, int] = {}
    for word in q_terms:
        q_counts[word] = q_counts.get(word, 0) + 1
    phrase = " ".join(q_terms)
    q_numbers = set(re.findall(r"\d+(?:\.\d+)?", str(question or "")))

    scored = []
    for i, chunk in enumerate(chunks):
        low = chunk.lower()
        score = sum(idf.get(w, 1.0) * min(q_counts[w], 3)
                    for w in set(q_terms) if w in docs[i])
        if phrase and phrase in low:
            score += len(q_terms) * 2.0
        if re.search(r"(?m)^\s*(?:#|\[Page\s+\d+\]|chapter|section)", low):
            score += 1.5
        if q_numbers and any(number in low for number in q_numbers):
            score += 2.0
        scored.append((score, i, chunk))
    scored.sort(key=lambda row: (-row[0], row[1]))
    picked = [row[2] for row in scored if row[0] > 0][:k]
    return picked or list(chunks[:k])


def _is_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "quota" in text or "resource_exhausted" in text


def _generate(contents, model: str = MODEL_FLASH) -> str:
    """Generate with multi-key rotation; fall back to the lite tier on quota only."""
    try:
        resp = generate_with_retry(model=model, contents=contents)
        return (getattr(resp, "text", None) or "").strip()
    except Exception as exc:
        if model == MODEL_FLASH and _is_quota_error(exc):
            resp = generate_with_retry(model=MODEL_FLASH_LITE, contents=contents)
            return (getattr(resp, "text", None) or "").strip()
        raise


def _ask_gemini(question: str, context: str) -> str:
    """Back-compatible QA helper used by actions/document_qa.py."""
    prompt = (
        "You are Friday. Answer the user's question using ONLY the document "
        "excerpt below. Be accurate and concise (max 4 sentences). If the answer "
        "is not in the excerpt, say you couldn't find it in the document.\n\n"
        f"DOCUMENT EXCERPT:\n{context}\n\n"
        f"QUESTION: {question}"
    )
    try:
        return _generate(prompt) or "No answer, sir."
    except Exception as e:
        return f"PDF question failed: {e}"


def _multimodal_answer(question: str, path: Path, visual: bool = False) -> str | None:
    """Native PDF vision for scans/diagrams; returns None when not applicable."""
    size = path.stat().st_size if path.exists() else 0
    if visual and size > MAX_INLINE_BYTES:
        return ("That PDF is over the 20 MB inline limit, sir — I can't send it "
                "to Gemini for visual reading.")
    if size > MAX_INLINE_BYTES:
        return None
    try:
        from google.genai import types
        part = types.Part.from_bytes(data=path.read_bytes(), mime_type="application/pdf")
    except Exception as e:
        return f"Could not prepare the PDF for visual analysis: {e}"
    prompt = (
        "Read this PDF visually — it may be a scan or contain diagrams. "
        "Answer the user, keeping page numbers in your answer when possible.\n\n"
        f"QUESTION: {question or 'Summarise this document.'}"
    )
    try:
        return _generate([part, prompt]) or "I couldn't read that PDF visually, sir."
    except Exception as e:
        return f"PDF visual analysis failed: {e}"


def _context(pages_text: str, question: str) -> str:
    chunks = _chunk_text(pages_text)
    if not chunks:
        return ""
    return "\n\n---\n\n".join(_pick_chunks(question, chunks))


def read_pdf(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Preview, summarise, search, inspect metadata or extract PDF pages."""
    params = parameters if isinstance(parameters, dict) else {}
    path = _resolve_pdf(params.get("path", params.get("file", "")))
    if path is None:
        return ("I couldn't find that PDF, sir. Give me the file name or a path "
                "(e.g. 'manual.pdf' or 'C:\\Users\\you\\Documents\\manual.pdf').")

    action = str(params.get("action") or "preview").strip().lower()
    layout = _as_bool(params.get("layout", False))
    visual = _as_bool(params.get("visual", False))
    password = params.get("password")

    if action == "metadata":
        try:
            reader = _open_reader(path, password)
            meta = dict(getattr(reader, "metadata", {}) or {})
            fields = []
            for key in ("title", "author", "subject"):
                if meta.get(key):
                    fields.append(f"{key}: {meta[key]}")
            size_kb = path.stat().st_size // 1024
            return (f"'{path.name}' — {len(reader.pages)} pages, {size_kb} KB, "
                    "encrypted: " + ("yes" if getattr(reader, "is_encrypted", False) else "no") +
                    (". " + "; ".join(fields) if fields else "."))
        except Exception as e:
            return f"Could not read PDF metadata: {e}"

    try:
        pages_text = _extract_text(path, layout=layout, pages=params.get("pages"),
                                   password=password)
    except PdfPasswordRequired as e:
        return str(e)
    except Exception as e:
        return f"Could not read the PDF: {e}"

    if action == "pages":
        return pages_text or f"'{path.name}' has no text on those pages, sir."

    if action == "search":
        query = str(params.get("query") or params.get("question") or "").strip()
        if not query:
            return "What would you like me to search for in the PDF, sir?"
        hits = []
        for line in pages_text.splitlines():
            if query.lower() in line.lower():
                hits.append(line.strip()[:240])
        if not hits:
            return f"I couldn't find '{query}' in that PDF, sir."
        return f"Matches in {path.name}:\n" + "\n".join(hits[:12])

    if action == "summary":
        if len(re.sub(r"\s+", "", pages_text)) < 40 or visual:
            answer = _multimodal_answer("Summarise this PDF", path, visual=visual)
            return answer or f"'{path.name}' is a scanned PDF with no extractable text, sir."
        context = _context(pages_text, "Summarise this document")
        return _ask_gemini("Summarise this document with the key points.", context)

    # Default: preview.
    preview = pages_text[:1200] + ("…" if len(pages_text) > 1200 else "")
    return (f"'{path.name}' — about {len(pages_text)} characters.\n\n"
            f"Preview:\n{preview}")


def pdf_qa(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Answer a question about a PDF with page-cited retrieval or PDF vision."""
    params = parameters if isinstance(parameters, dict) else {}
    path = _resolve_pdf(params.get("path", params.get("file", "")))
    question = str(params.get("question", params.get("query", ""))).strip()
    if path is None:
        return "I couldn't find that PDF, sir. Give me a file name or a path."
    if not question:
        return "What would you like to know from the document, sir?"

    layout = _as_bool(params.get("layout", False))
    visual = _as_bool(params.get("visual", False))
    try:
        pages_text = _extract_text(path, layout=layout, pages=params.get("pages"),
                                   password=params.get("password"))
    except PdfPasswordRequired as e:
        return str(e)
    except Exception as e:
        return f"Could not read the PDF: {e}"

    if len(re.sub(r"\s+", "", pages_text)) < 40 or visual:
        answer = _multimodal_answer(question, path, visual=visual)
        return answer or f"'{path.name}' has no extractable text, sir."
    answer = _ask_gemini(question, _context(pages_text, question))
    if player:
        try:
            player.write_log(f"[pdf] {answer[:160]}")
        except Exception:
            pass
    if session_memory:
        try:
            session_memory.set_last_search(query=f"pdf: {question}", response=answer)
        except Exception:
            pass
    return answer
