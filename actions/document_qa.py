# actions/document_qa.py
# Friday — question-answering over any document or web page
# (pdf, txt, md, html, docx, csv, pptx, xlsx, and http(s) URLs)

import io
import json
import os
import re
import sys
import threading
import time
import zipfile
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

from actions.pdf_reader import (
    _chunk_text,
    _pick_chunks,
    _ask_gemini,
)

try:
    from bs4 import BeautifulSoup
    _BS4 = True
except ImportError:
    _BS4 = False

try:
    import requests
    _REQUESTS = True
except ImportError:
    _REQUESTS = False


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_URL_RE = re.compile(r"^(https?://)", re.IGNORECASE)

_SEARCH_DIRS = [
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "Desktop",
    Path.home(),
]


_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".cache",
    "AppData", "Library", "site-packages", ".npm", ".mypy_cache", ".tox",
}
_MAX_SEARCH_DEPTH = 3


def _resolve_source(raw: str) -> str:
    """Resolve a path/URL; search well-known folders up to three levels deep."""
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        return None
    if _URL_RE.match(raw):
        return raw

    cand = Path(raw).expanduser()
    if cand.exists():
        return str(cand.resolve())

    needle = cand.name.lower()
    candidates: list[Path] = []
    for base in _SEARCH_DIRS:
        direct = base / cand
        if direct.exists():
            candidates.append(direct)
        base_path = Path(base)
        if not base_path.is_dir():
            continue
        for root, dirs, files in os.walk(base_path):
            dirs[:] = [d for d in dirs
                       if d not in _SKIP_DIRS and not d.startswith(".")]
            depth = Path(root).relative_to(base_path).parts
            if len(depth) >= _MAX_SEARCH_DEPTH:
                dirs[:] = []
            for name in files:
                if name.lower() == needle:
                    candidates.append(Path(root) / name)
    if not candidates:
        return None
    # Prefer exact path suffix matches, then shallowest, then most recent.
    unique = list(dict.fromkeys(p.resolve() for p in candidates))
    unique.sort(key=lambda p: (
        0 if str(p).lower().replace("\\", "/").endswith(raw.lower().replace("\\", "/")) else 1,
        len(p.parts),
        -p.stat().st_mtime if p.exists() else 0,
    ))
    return str(unique[0])


def _is_private_target(url: str) -> bool:
    """True if url resolves to a loopback/private/link-local/reserved host
    or uses a non-http(s) scheme (SSRF guard)."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme and parsed.scheme.lower() not in ("http", "https"):
            return True
        host = (parsed.hostname or "").strip().strip("[]").lower()
    except Exception:
        return True
    if not host:
        return True

    import socket
    import ipaddress
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except Exception:
        return True  # unresolvable — fail closed

    if not infos:
        return True

    for info in infos:
        ip = info[4][0].split("%")[0]
        try:
            addr = ipaddress.ip_address(ip)
            if (
                addr.is_private
                or addr.is_loopback
                or addr.is_link_local
                or addr.is_multicast
                or addr.is_reserved
                or addr.is_unspecified
            ):
                return True
        except Exception:
            return True
    return False


_MAX_URL_BYTES = 5 * 1024 * 1024


def _extract_url(url: str) -> str:
    if not _REQUESTS:
        raise RuntimeError("requests is not installed.")
    from urllib.parse import urljoin, urlparse
    headers = {"User-Agent": "Mozilla/5.0 (Friday/assistant)"}
    current_url = url
    resp = None
    for _ in range(5):
        scheme = (urlparse(current_url).scheme or "").lower()
        if scheme not in ("http", "https"):
            raise RuntimeError("That URL scheme is not allowed, sir.")
        if _is_private_target(current_url) or "169.254.169.254" in current_url or "metadata.google.internal" in current_url:
            raise RuntimeError("That URL is off-limits or points to an internal network, sir.")
        resp = requests.get(current_url, headers=headers, timeout=20, allow_redirects=False)
        if 300 <= resp.status_code < 400 and "Location" in resp.headers:
            current_url = urljoin(current_url, resp.headers["Location"])
            continue
        resp.raise_for_status()
        break
    else:
        raise RuntimeError("Too many redirects, sir.")
    if len(resp.content or b"") > _MAX_URL_BYTES:
        raise RuntimeError("That URL response is too large to process safely, sir.")
    if _BS4:
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
    else:
        text = re.sub(r"<[^>]+>", " ", resp.text)
    return re.sub(r"\n{3,}", "\n\n", text)


_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_node_text(node: ET.Element) -> str:
    return "".join(part.text or "" for part in node.iter(_W_NS + "t")).strip()


def _extract_docx(path: Path) -> str:
    """Read DOCX paragraphs *and* tables with ElementTree (no regex hacks)."""
    with zipfile.ZipFile(str(path)) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find(_W_NS + "body")
    if body is None:
        return ""
    lines: list[str] = []
    for element in body:
        if element.tag == _W_NS + "p":
            text = _docx_node_text(element)
            if text:
                lines.append(text)
        elif element.tag == _W_NS + "tbl":
            for row in element.findall(_W_NS + "tr"):
                cells = [_docx_node_text(cell) for cell in row.findall(_W_NS + "tc")]
                if any(cells):
                    lines.append(" | ".join(cells))
    return "\n".join(lines)


def _extract_pptx(path: Path) -> str:
    """Slide headers, shape text, tables and speaker notes."""
    try:
        from pptx import Presentation
    except ImportError:
        raise RuntimeError("python-pptx is not installed.")
    prs = Presentation(str(path))
    lines: list[str] = []
    for number, slide in enumerate(prs.slides, 1):
        lines.append(f"[Slide {number}]")
        for shape in slide.shapes:
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    if any(cells):
                        lines.append(" | ".join(cells))
            elif getattr(shape, "text", ""):
                lines.append(shape.text.strip())
        try:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                lines.append(f"[Notes] {notes}")
        except Exception:
            pass
    return "\n".join(lines)


def _extract_xlsx(path: Path) -> str:
    """Read sheets with their first row as a header and capped blank runs."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise RuntimeError("openpyxl is not installed.")
    wb = load_workbook(str(path), read_only=True, data_only=True)
    try:
        lines: list[str] = []
        for ws in wb.worksheets:
            lines.append(f"[Sheet: {ws.title}]")
            blank_run = 0
            for index, row in enumerate(ws.iter_rows(values_only=True)):
                cells = [str(cell).strip() for cell in row
                         if cell is not None and str(cell).strip()]
                if not cells:
                    blank_run += 1
                    if blank_run >= 5:
                        break
                    continue
                blank_run = 0
                prefix = "[Header] " if index == 0 else ""
                lines.append(prefix + " | ".join(cells))
        return "\n".join(lines)
    finally:
        wb.close()


def _decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_text_file(path: Path) -> str:
    return _decode_bytes(path.read_bytes())


def _format_structured(path: Path) -> str | None:
    suffix = path.suffix.lower()
    text = _read_text_file(path)
    if suffix == ".json":
        try:
            return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except Exception:
            return text
    if suffix in (".csv", ".tsv"):
        import csv
        delimiter = "\t" if suffix == ".tsv" else ","
        try:
            rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
            return "\n".join(" | ".join(cell.strip() for cell in row) for row in rows)
        except Exception:
            return text
    return None


def _strip_html(raw: str) -> str:
    if not _BS4:
        return re.sub(r"<[^>]+>", " ", raw)
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer",
                     "aside", "form", "iframe"]):
        tag.decompose()
    article = soup.find("article") or soup.find("main") or soup.find("body")
    source = article if article is not None else soup
    text = source.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)


def _download_bytes(url: str, max_bytes: int = _MAX_URL_BYTES) -> tuple[bytes, str]:
    """SSRF-checked, redirect-aware streaming download with a hard byte cap."""
    if not _REQUESTS:
        raise RuntimeError("requests is not installed.")
    headers = {"User-Agent": "Mozilla/5.0 (Friday/assistant)"}
    current = url
    for _ in range(6):
        if (urlparse(current).scheme or "").lower() not in ("http", "https"):
            raise RuntimeError("That URL scheme is not allowed, sir.")
        if _is_private_target(current):
            raise RuntimeError("That URL points to an internal network, sir.")
        response = requests.get(current, headers=headers, timeout=(5, 15),
                                allow_redirects=False, stream=True)
        if 300 <= response.status_code < 400 and response.headers.get("Location"):
            current = urljoin(current, response.headers["Location"])
            response.close()
            continue
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            data.extend(chunk)
            if len(data) > max_bytes:
                response.close()
                raise RuntimeError("That URL response is too large to process safely, sir.")
        response.close()
        return bytes(data), current
    raise RuntimeError("Too many redirects, sir.")


def _remote_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("pypdf is not installed.")
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


# ── bounded, thread-safe extraction cache ───────────────────────────────────
_CACHE_LOCK = threading.RLock()
_CACHE: OrderedDict[tuple, tuple[float, str]] = OrderedDict()
_CACHE_TTL_S = 600
_CACHE_LIMIT = 32


def _cache_key(source: str) -> tuple:
    source = str(source)
    if _URL_RE.match(source):
        return ("url", source)
    path = Path(source)
    try:
        stat = path.stat()
        return ("file", str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    except Exception:
        return ("file", str(path), None, None)


def _cached_extract(source: str, fresh: bool = False) -> str:
    source = str(source)
    key = _cache_key(source)
    if not fresh:
        with _CACHE_LOCK:
            entry = _CACHE.get(key)
            if entry and time.time() - entry[0] < _CACHE_TTL_S:
                _CACHE.move_to_end(key)
                return entry[1]
    text = _extract_text_uncached(source)
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), text)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
    return text


def _extract_text_uncached(source: str) -> str:
    if _URL_RE.match(source):
        data, final_url = _download_bytes(source)
        suffix = urlparse(final_url).path.lower().split("?")[0]
        if suffix.endswith(".pdf"):
            return _remote_pdf(data)
        return _strip_html(_decode_bytes(data))

    path = Path(source)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from actions.pdf_reader import _extract_text as _pdf_text
        return _pdf_text(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix in (".pptx", ".ppt"):
        return _extract_pptx(path)
    if suffix in (".xlsx", ".xlsm", ".xls"):
        return _extract_xlsx(path)
    if suffix in (".html", ".htm"):
        return _strip_html(_read_text_file(path))
    structured = _format_structured(path)
    if structured is not None:
        return structured
    return _read_text_file(path)


def _extract_text(source: str, fresh: bool = False) -> str:
    return _cached_extract(source, fresh=fresh)

def _describe(source: str) -> str:
    if _URL_RE.match(source):
        return source
    p = Path(source)
    return f"{p.name} ({p.stat().st_size // 1024} KB)"


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")
    return bool(value) if value is not None else default


def _summary_text(text: str, limit: int = 1200) -> str:
    normalized = re.sub(r"[ \t]+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    picked: list[str] = []
    total = 0
    for sentence in sentences:
        if total + len(sentence) > limit and picked:
            break
        picked.append(sentence)
        total += len(sentence) + 1
    result = " ".join(picked)
    return (result[:limit].rsplit(" ", 1)[0] + "…") if len(result) > limit else result


def _search_text(text: str, query: str, limit: int = 5) -> str:
    needle = str(query or "").strip()
    if not needle:
        return "What would you like me to search for, sir?"
    matches = list(re.finditer(re.escape(needle), text, flags=re.I))
    if not matches:
        return f"No matches for '{needle}'."
    lines = [f"{len(matches)} match(es) for '{needle}':"]
    for match in matches[:limit]:
        start = max(0, match.start() - 100)
        end = min(len(text), match.end() + 160)
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()
        lines.append(f"…{snippet}…")
    return "\n".join(lines)


def read_document(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Preview, summarize, search or profile any local document or web page."""
    params = parameters if isinstance(parameters, dict) else {}
    source = _resolve_source(str(params.get("source", params.get("path", ""))))
    if not source:
        return ("I couldn't find that document, sir. Give me a file name, path, "
                "or a web address.")

    action = str(params.get("action", "preview") or "preview").strip().lower()
    fresh = _as_bool(params.get("fresh"), False)
    try:
        try:
            text = _extract_text(source, fresh=fresh)
        except TypeError as exc:
            if "unexpected keyword" not in str(exc):
                raise
            text = _extract_text(source)
    except Exception as e:
        return f"Could not read that source: {e}"
    if not text.strip():
        return f"'{_describe(source)}' has no extractable text, sir."

    label = _describe(source)
    if action == "stats":
        words = len(re.findall(r"\b\w+\b", text))
        return (f"Source: {label}\nCharacters: {len(text)}\nWords: {words}\n"
                f"Lines: {len(text.splitlines())}\nChunks: {len(_chunk_text(text))}")
    if action == "summary":
        return f"Summary of {label}:\n{_summary_text(text)}"
    if action in ("search", "find"):
        return _search_text(text, params.get("query", params.get("question", "")),
                            int(params.get("limit", 5) or 5))

    size = int(params.get("limit", 1400) or 1400)
    preview = text[:size] + ("…" if len(text) > size else "")
    return f"Source: {label} — {len(text)} characters.\n\n{preview}"

def document_qa(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Answer a question from any document or web page (lightweight RAG)."""
    params   = parameters or {}
    source   = _resolve_source(str(params.get("source", "")))
    question = str(params.get("question", params.get("query", ""))).strip()

    if not source:
        return ("I couldn't find that source, sir. Give a file name, path, or URL.")
    if not question:
        return "What would you like to know, sir?"

    try:
        try:
            text = _extract_text(source, fresh=_as_bool(params.get("fresh"), False))
        except TypeError as exc:
            if "unexpected keyword" not in str(exc):
                raise
            text = _extract_text(source)
    except Exception as e:
        return f"Could not read that source: {e}"

    text_full = re.sub(r"\s+", " ", text).strip()
    if not text_full:
        return f"'{_describe(source)}' has no extractable text, sir."
    chunks = _chunk_text(text_full)
    picked = _pick_chunks(question, chunks)
    answer = _ask_gemini(question, "\n\n---\n\n".join(picked))
    return f"From {_describe(source)}:\n{answer}"