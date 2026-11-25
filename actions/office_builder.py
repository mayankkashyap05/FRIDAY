"""Executive-quality PowerPoint and Excel generation for Friday.

All document libraries are imported lazily so the module imports cleanly on
systems without office packages installed.
"""

import sys
from datetime import datetime
from pathlib import Path


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


OUT_DIR = Path.home() / "Documents" / "Friday"

_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Executive palette (deep navy + warm accent).
_NAVY = (23, 42, 69)
_ACCENT = (196, 142, 62)
_LIGHT = (238, 241, 245)


def _ensure_out_dir() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR


def _sanitize(name: str) -> str:
    name = "".join(c for c in str(name or "") if c not in '\\/:*?"<>|').strip().rstrip(".")
    if not name or name.upper() in _WIN_RESERVED:
        return "document" if not name else f"_{name}"
    return name


def _sheet_name(name: str, fallback: str = "Sheet1") -> str:
    safe = _sanitize(name or fallback)
    safe = "".join(c for c in safe if c not in "[]").strip() or fallback
    return safe[:31]


def _safe_output(name: str, suffix: str) -> Path:
    return _ensure_out_dir() / f"{_sanitize(name)}{suffix}"


def _typed(value):
    """Smart typing: numbers, booleans and formulas become real Excel values."""
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    text = str(value).strip()
    if text.startswith("="):
        return text  # openpyxl stores strings starting with '=' as formulas
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        if text and text.replace(",", "").replace(".", "", 1).lstrip("-").isdigit():
            return float(text.replace(",", "")) if "." in text else int(text.replace(",", ""))
    except (TypeError, ValueError):
        pass
    return text


def _ppt_theme(prs):
    from pptx.dml.color import RGBColor
    from pptx.util import Pt

    master = prs.slide_master
    background = master.background.fill
    background.solid()
    background.fore_color.rgb = RGBColor(*_LIGHT)
    for layout in prs.slide_layouts:
        for shape in layout.placeholders:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    paragraph.font.size = Pt(20)


def _add_footer(slide, text: str):
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    box = slide.shapes.add_textbox(Inches(0.5), Inches(7.05), Inches(9.0), Inches(0.3))
    frame = box.text_frame
    frame.text = text
    for paragraph in frame.paragraphs:
        paragraph.font.size = Pt(9)
        paragraph.font.color.rgb = RGBColor(*_ACCENT)


def _add_table_slide(prs, item: dict, index: int):
    from pptx.util import Inches, Pt

    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = str(item.get("title") or f"Slide {index}")
    table_data = item.get("table") or {}
    headers = [str(h) for h in (table_data.get("headers") if isinstance(table_data, dict) else [])]
    rows = table_data.get("rows", []) if isinstance(table_data, dict) else []
    rows = [list(r) if not isinstance(r, str) else [r] for r in rows]
    ncols = max(1, len(headers), *(len(r) for r in rows)) if rows else max(1, len(headers))
    nrows = max(1, len(rows) + (1 if headers else 0))
    shape = slide.shapes.add_table(nrows, ncols, Inches(0.6), Inches(1.5),
                                   Inches(12.1), Inches(0.4 * nrows))
    table = shape.table
    if headers:
        for c, head in enumerate(headers):
            table.cell(0, c).text = head
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.cell(r + (1 if headers else 0), c).text = str(value)
    for cell in table.iter_cells() if hasattr(table, "iter_cells") else []:
        for paragraph in cell.text_frame.paragraphs:
            paragraph.font.size = Pt(11)
    if item.get("notes"):
        slide.notes_slide.notes_text_frame.text = str(item["notes"])
    return slide


def create_presentation(
    title: str = "",
    slides=None,
    filename: str = None,
    subtitle: str = "",
    theme: str = "executive",
) -> str:
    """Create a themed .pptx with title, bullets, tables, notes and footers."""
    try:
        from pptx import Presentation
        from pptx.dml.color import RGBColor
        from pptx.util import Pt
    except ImportError as exc:
        return f"python-pptx is not installed: {exc}"

    if not title:
        return "Please provide a presentation title, sir."
    slides = slides or []

    try:
        prs = Presentation()
        _ppt_theme(prs)
        today = datetime.now().strftime("%d %B %Y")

        cover = prs.slides.add_slide(prs.slide_layouts[0])
        cover.shapes.title.text = str(title)
        for paragraph in cover.shapes.title.text_frame.paragraphs:
            paragraph.font.size = Pt(40)
            paragraph.font.color.rgb = RGBColor(*_NAVY)
        try:
            cover.placeholders[1].text = str(subtitle or f"Prepared by Friday • {today}")
        except Exception:
            pass
        _add_footer(cover, f"Friday • {today}")

        for idx, item in enumerate(slides, start=1):
            if isinstance(item, str):
                item = {"title": f"Slide {idx}", "bullets": [item]}
            if not isinstance(item, dict):
                item = {"title": f"Slide {idx}", "bullets": [str(item)]}
            if item.get("table"):
                slide = _add_table_slide(prs, item, idx)
            else:
                slide = prs.slides.add_slide(prs.slide_layouts[1])
                slide.shapes.title.text = str(item.get("title") or f"Slide {idx}")
                for paragraph in slide.shapes.title.text_frame.paragraphs:
                    paragraph.font.color.rgb = RGBColor(*_NAVY)
                if item.get("subtitle"):
                    try:
                        slide.placeholders[1].text = str(item["subtitle"])
                    except Exception:
                        pass
                body = slide.placeholders[1].text_frame
                bullets = item.get("bullets") or []
                if isinstance(bullets, str):
                    bullets = [bullets]
                first = True
                for bullet in bullets:
                    paragraph = body.paragraphs[0] if first else body.add_paragraph()
                    first = False
                    paragraph.text = str(bullet)
                    paragraph.level = int(item.get("level", 0))
                    for run in paragraph.runs:
                        run.font.size = Pt(18)
            if item.get("notes"):
                try:
                    slide.notes_slide.notes_text_frame.text = str(item["notes"])
                except Exception:
                    pass
            _add_footer(slide, f"{title} • {idx} • {today}")

        path = _safe_output(filename or title, ".pptx")
        prs.save(str(path))
        return f"Presentation created: {path}"
    except Exception as exc:
        return f"Could not create presentation: {exc}"


def _write_sheet(ws, headers, rows, summary=None, freeze=True):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    headers = list(headers or [])
    rows = [list(r) if not isinstance(r, (str, int, float)) else [r] for r in (rows or [])]
    if headers:
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col, value=str(header))
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="173A53")
            cell.alignment = Alignment(horizontal="center", vertical="center")
    start = 2 if headers else 1
    for row_idx, row in enumerate(rows, start=start):
        for col_idx, value in enumerate(row, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=_typed(value))
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    last_row = start + len(rows) - 1
    if summary:
        summary_row = last_row + 1 if rows else start
        values = summary if isinstance(summary, (list, tuple)) else [summary]
        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=summary_row, column=col_idx, value=_typed(value))
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="EEF1F5")
    ncols = max(1, len(headers), *(len(r) for r in rows)) if rows else max(1, len(headers))
    for col in range(1, ncols + 1):
        letter = get_column_letter(col)
        longest = max([len(str(headers[col - 1])) if col <= len(headers) else 0] +
                      [len(str(row[col - 1])) for row in rows if col <= len(row)] or [0])
        ws.column_dimensions[letter].width = min(48, max(12, longest + 2))
    if headers and rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(ncols)}{last_row}"
    if freeze and headers:
        ws.freeze_panes = "A2"


def create_spreadsheet(
    filename: str = "spreadsheet",
    headers=None,
    rows=None,
    sheet_name: str = "Sheet1",
    sheets=None,
    summary=None,
) -> str:
    """Create a styled .xlsx, optionally with multiple sheets and a summary."""
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        return f"openpyxl is not installed: {exc}"

    try:
        wb = Workbook()
        if sheets:
            definitions = []
            for item in sheets:
                if isinstance(item, dict):
                    definitions.append((
                        _sheet_name(str(item.get("name") or f"Sheet{len(definitions) + 1}")),
                        item.get("headers") or [], item.get("rows") or [],
                        item.get("summary"),
                    ))
                else:
                    definitions.append((_sheet_name(f"Sheet{len(definitions) + 1}"), [], item or [], None))
        else:
            definitions = [(_sheet_name(sheet_name, "Sheet1"), headers or [], rows or [], summary)]

        first = True
        used_names = set()
        for name, sheet_headers, sheet_rows, sheet_summary in definitions:
            ws = wb.active if first else wb.create_sheet()
            ws.title = name if name not in used_names else _sheet_name(f"{name}_{len(used_names)}")
            used_names.add(ws.title)
            _write_sheet(ws, sheet_headers, sheet_rows, sheet_summary)
            first = False

        path = _safe_output(filename, ".xlsx")
        wb.save(str(path))
        return f"Spreadsheet created: {path}"
    except Exception as exc:
        return f"Could not create spreadsheet: {exc}"
