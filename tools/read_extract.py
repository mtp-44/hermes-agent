"""Document-to-text extraction for ``read_file``.

Supports Jupyter notebooks, DOCX, XLSX, and PDF. The notebook/Office formats are
parsed with the stdlib only (``zipfile`` + XML), no third-party deps. PDF uses
``pdfplumber`` (layout-aware text + ruled-table extraction) with ``pypdf`` as a
fallback — both core dependencies. A PDF's compressed content streams and
font-encoding maps can't be decoded reliably without a real parser; a naive
stdlib attempt silently produces garbage on exactly the documents that matter
(e.g. financial statements with custom font encodings).

Malformed or unextractable documents raise :class:`ExtractionError`; callers can
then fall back to normal text/binary handling.
"""

from __future__ import annotations

import json
import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

__all__ = ["EXTRACTABLE_EXTENSIONS", "ExtractionError", "extract_document_text", "is_extractable_document"]

EXTRACTABLE_EXTENSIONS = frozenset({".ipynb", ".docx", ".xlsx", ".pdf"})
MAX_XLSX_BYTES = 50 * 1024 * 1024
_MAX_XLSX_ROWS_PER_SHEET = 5000
_MAX_XLSX_COLS = 256
MAX_PDF_BYTES = 100 * 1024 * 1024
_MAX_PDF_PAGES = 300

_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


class ExtractionError(Exception):
    """Raised when a supported-looking document cannot be rendered as text."""


def _extension(path: str) -> str:
    ext = Path(path).suffix.lower()
    return ext if ext in EXTRACTABLE_EXTENSIONS else ""


def is_extractable_document(path: str) -> bool:
    return bool(_extension(path))


def extract_document_text(path: str) -> str:
    ext = _extension(path)
    if ext == ".ipynb":
        return _extract_notebook(path)
    if ext == ".docx":
        return _extract_docx(path)
    if ext == ".xlsx":
        return _extract_xlsx(path)
    if ext == ".pdf":
        return _extract_pdf(path)
    raise ExtractionError(f"Unsupported document type: {path!r}")


def _source_text(source) -> str:
    if isinstance(source, str):
        return source
    if isinstance(source, list):
        return "".join(item for item in source if isinstance(item, str))
    return ""


def _extract_notebook(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            nb = json.load(fh)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ExtractionError(f"Not a valid notebook: {exc}") from exc
    if not isinstance(nb, dict):
        raise ExtractionError("Notebook root is not an object")

    cells = nb.get("cells")
    if not isinstance(cells, list):
        cells = [
            cell
            for ws in nb.get("worksheets", [])
            if isinstance(ws, dict)
            for cell in ws.get("cells", [])
        ]
    if not cells:
        raise ExtractionError("Notebook contains no cells")

    counts = {"markdown": 0, "code": 0, "raw": 0}
    labels = {"markdown": "Markdown", "code": "Code", "raw": "Raw"}
    out: list[str] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        typ = cell.get("cell_type")
        if typ not in labels:
            continue
        counts[typ] += 1
        suffix = f" {counts[typ]}" if typ != "raw" else ""
        out.extend((f"# ── {labels[typ]} cell{suffix} ──", _source_text(cell.get("source", "")).rstrip("\n"), ""))
    if not out:
        raise ExtractionError("Notebook contains no readable cells")
    return "\n".join(out).rstrip("\n") + "\n"


def _extract_pdf(path: str) -> str:
    """Extract text (and ruled tables) from a PDF, one labelled block per page.

    Primary engine is ``pdfplumber``: its layout-aware ``extract_text`` keeps
    each logical row on one line (e.g. a bank-statement transaction with its
    amount attached), which pypdf's reading-order dump does not, and it can
    additionally pull *ruled* tables (bordered invoices/statements) as
    structured rows. ``pypdf`` is the fallback when pdfplumber is unavailable or
    chokes on a particular file.

    Scanned / image-only PDFs have no text layer, so extraction yields nothing —
    we raise :class:`ExtractionError` so the caller falls back to the
    binary/vision hint rather than returning an empty document. (OCR of scanned
    PDFs is the remaining Phase 8.1 follow-up.)

    Note on tables: only the default ("lines") strategy is used, so we emit a
    rendered table only when real ruled borders exist. The text-based table
    strategy was evaluated and rejected — on whitespace-aligned bank statements
    it splits words mid-token and is worse than the plain text.
    """
    try:
        size = Path(path).stat().st_size
    except OSError as exc:
        raise ExtractionError(str(exc)) from exc
    if size > MAX_PDF_BYTES:
        raise ExtractionError(
            f"PDF is {size:,} bytes, over the {MAX_PDF_BYTES:,}-byte extraction limit"
        )

    # Prefer pdfplumber; fall back to pypdf. A genuine "no text layer" result
    # (ExtractionError) is re-raised — pypdf won't do better on a scanned PDF —
    # but an unexpected pdfplumber failure on a parseable file falls through.
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        pass
    else:
        try:
            return _extract_pdf_pdfplumber(path)
        except ExtractionError:
            raise
        except Exception:  # noqa: BLE001 - malformed for pdfplumber; try pypdf
            pass
    return _extract_pdf_pypdf(path)


def _render_table(rows: list) -> str:
    """Render a pdfplumber table (list of rows of optional cells) as Markdown."""
    cleaned = [
        [(cell or "").replace("\n", " ").strip() for cell in row]
        for row in rows
    ]
    cleaned = [row for row in cleaned if any(row)]
    if not cleaned:
        return ""
    width = max(len(row) for row in cleaned)
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]
    lines = ["| " + " | ".join(row) + " |" for row in cleaned]
    if len(lines) > 1:
        sep = "| " + " | ".join(["---"] * width) + " |"
        lines.insert(1, sep)
    return "\n".join(lines)


def _extract_pdf_pdfplumber(path: str) -> str:
    import pdfplumber

    out: list[str] = []
    found_text = False
    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages[:_MAX_PDF_PAGES], start=1):
            out.append(f"# ── Page {i} ──")
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001 - one bad page shouldn't sink the doc
                text = ""
            if text.strip():
                found_text = True
                out.append(text.rstrip("\n"))
            else:
                out.append("(no extractable text — page may be scanned or image-only)")
            # Ruled tables only (default strategy); skip on any failure.
            try:
                tables = page.extract_tables() or []
            except Exception:  # noqa: BLE001
                tables = []
            for tnum, table in enumerate(tables, start=1):
                rendered = _render_table(table)
                if rendered:
                    found_text = True
                    out.append(f"\n[Table {i}.{tnum}]\n{rendered}")
            out.append("")
        if total > _MAX_PDF_PAGES:
            out.append(f"(truncated: showing first {_MAX_PDF_PAGES} of {total} pages)")

    if not found_text:
        raise ExtractionError(
            "PDF contains no extractable text (likely scanned or image-only); "
            "use vision_analyze on rendered pages instead"
        )
    return "\n".join(out).rstrip("\n") + "\n"


def _extract_pdf_pypdf(path: str) -> str:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError
    except ImportError as exc:  # pragma: no cover - pypdf is a core dependency
        raise ExtractionError("PDF extraction requires the 'pypdf' package") from exc

    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            # Many PDFs are encrypted only with an empty owner password
            # (permission flags, not real access control); try that before
            # giving up.
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001 - pypdf raises various types
                raise ExtractionError("PDF is password-protected") from exc
        pages = reader.pages
    except ExtractionError:
        raise
    except (PyPdfError, OSError, ValueError) as exc:
        raise ExtractionError(f"Not a valid PDF: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - pypdf can raise undocumented errors
        raise ExtractionError(f"Could not parse PDF: {exc}") from exc

    total = len(pages)
    out: list[str] = []
    found_text = False
    for i, page in enumerate(pages[:_MAX_PDF_PAGES], start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - one bad page shouldn't sink the doc
            text = ""
        out.append(f"# ── Page {i} ──")
        if text.strip():
            found_text = True
            out.append(text.rstrip("\n"))
        else:
            out.append("(no extractable text — page may be scanned or image-only)")
        out.append("")
    if total > _MAX_PDF_PAGES:
        out.append(f"(truncated: showing first {_MAX_PDF_PAGES} of {total} pages)")

    if not found_text:
        raise ExtractionError(
            "PDF contains no extractable text (likely scanned or image-only); "
            "use vision_analyze on rendered pages instead"
        )
    return "\n".join(out).rstrip("\n") + "\n"


def _zip_xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        return ET.fromstring(zf.read(name))
    except KeyError as exc:
        raise ExtractionError(f"Missing {name}") from exc
    except ET.ParseError as exc:
        raise ExtractionError(f"Malformed XML in {name}: {exc}") from exc


def _extract_docx(path: str) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            root = _zip_xml(zf, "word/document.xml")
    except zipfile.BadZipFile as exc:
        raise ExtractionError(f"Not a valid DOCX: {exc}") from exc
    except OSError as exc:
        raise ExtractionError(str(exc)) from exc

    w = f"{{{_NS_W}}}"
    lines: list[str] = []
    for para in root.iter(f"{w}p"):
        buf: list[str] = []
        for node in para.iter():
            if node.tag == f"{w}t":
                buf.append(node.text or "")
            elif node.tag == f"{w}tab":
                buf.append("\t")
            elif node.tag in {f"{w}br", f"{w}cr"}:
                buf.append("\n")
        lines.extend("".join(buf).split("\n"))
    if not any(line.strip() for line in lines):
        raise ExtractionError("DOCX contains no extractable text")
    return "\n".join(lines).rstrip("\n") + "\n"


def _extract_xlsx(path: str) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            shared = _shared_strings(zf, names)
            sheets = _workbook_sheets(zf)
            rels = _workbook_rels(zf, names)
            out: list[str] = []
            for name, state, rid in sheets:
                if state in {"hidden", "veryHidden"}:
                    continue
                part = _sheet_part(rels.get(rid, ""))
                if part not in names:
                    continue
                try:
                    rows = _sheet_rows(zf.read(part), shared)
                except ET.ParseError:
                    continue
                out.append(f"# ── Sheet: {name} ──")
                out.extend("\t".join(row) for row in rows)
                if not rows:
                    out.append("(empty)")
                out.append("")
    except zipfile.BadZipFile as exc:
        raise ExtractionError(f"Not a valid XLSX: {exc}") from exc
    except OSError as exc:
        raise ExtractionError(str(exc)) from exc

    if not out:
        raise ExtractionError("XLSX has no visible sheets with content")
    return "\n".join(out).rstrip("\n") + "\n"


def _shared_strings(zf: zipfile.ZipFile, names: set[str]) -> list[str]:
    if "xl/sharedStrings.xml" not in names:
        return []
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except ET.ParseError:
        return []
    s = f"{{{_NS_S}}}"
    return ["".join(t.text or "" for t in item.iter(f"{s}t")) for item in root.iter(f"{s}si")]


def _workbook_sheets(zf: zipfile.ZipFile) -> list[tuple[str, str, str]]:
    root = _zip_xml(zf, "xl/workbook.xml")
    s, r = f"{{{_NS_S}}}", f"{{{_NS_REL}}}"
    return [
        (sheet.get("name", "Sheet"), sheet.get("state", "visible"), sheet.get(f"{r}id", ""))
        for sheet in root.iter(f"{s}sheet")
    ]


def _workbook_rels(zf: zipfile.ZipFile, names: set[str]) -> dict[str, str]:
    rels_path = "xl/_rels/workbook.xml.rels"
    if rels_path not in names:
        return {}
    try:
        root = ET.fromstring(zf.read(rels_path))
    except ET.ParseError:
        return {}
    rel_tag = f"{{{_NS_PKG_REL}}}Relationship"
    return {rel.get("Id", ""): rel.get("Target", "") for rel in root.iter(rel_tag) if rel.get("Id")}


def _sheet_part(target: str) -> str:
    target = target.lstrip("/")
    return posixpath.normpath(target if target.startswith("xl/") else f"xl/{target}")


def _col_index(ref: str) -> int:
    idx = 0
    for ch in ref:
        if not ch.isalpha():
            break
        idx = idx * 26 + ord(ch.upper()) - ord("A") + 1
    return max(idx - 1, 0)


def _sheet_rows(xml_bytes: bytes, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(xml_bytes)
    s = f"{{{_NS_S}}}"
    rows: list[list[str]] = []
    for row in root.iter(f"{s}row"):
        if len(rows) >= _MAX_XLSX_ROWS_PER_SHEET:
            break
        cells: dict[int, str] = {}
        max_col = -1
        for cell in row.iter(f"{s}c"):
            col = _col_index(cell.get("r", "")) if cell.get("r") else max_col + 1
            if col >= _MAX_XLSX_COLS:
                continue
            cells[col] = _cell_value(cell, shared, s)
            max_col = max(max_col, col)
        rows.append([cells.get(i, "") for i in range(max_col + 1)] if max_col >= 0 else [])
    while rows and not any(value.strip() for value in rows[-1]):
        rows.pop()
    return rows


def _cell_value(cell: ET.Element, shared: list[str], s: str) -> str:
    value = cell.findtext(f"{s}v") or ""
    typ = cell.get("t", "")
    if typ == "s":
        try:
            return shared[int(value)]
        except (ValueError, IndexError):
            return ""
    if typ == "inlineStr":
        inline = cell.find(f"{s}is")
        return "" if inline is None else "".join(t.text or "" for t in inline.iter(f"{s}t"))
    if typ == "b":
        return "TRUE" if value.strip() in {"1", "true", "TRUE"} else "FALSE"
    if typ == "e":
        return value or "#ERROR"
    return value
