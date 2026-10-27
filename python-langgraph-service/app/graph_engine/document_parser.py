"""Deterministic text extraction for the ``DOCUMENT`` node.

The engine deliberately keeps document parsing OUT of the model: an .xlsx/.docx is a ZIP
container whose text is already text, so a few lines of library code recover it exactly and
for free - no OCR, no token spend, no hallucinated cells. The parser's only jobs are to turn
that text into something a model reads well (markdown tables) and to keep it SMALL: a 5 000
row sheet is ~100k tokens, which would not fit a context window and would silently get
truncated mid-answer by the provider. The caps below make that truncation explicit instead.

Anything semantic ("which column is the amount?") stays an ordinary ACTION node downstream:
the extracted text lands in ``variables.document_text``, and the engine's existing
``extract_json_from_response`` + ``merge_dicts`` reducers do the mapping.
"""

import io
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

KIND_XLSX = "xlsx"
KIND_DOCX = "docx"

TRUNCATION_MARKER = "\n\n...[truncated by the DOCUMENT node's size caps]"

# The parts inside the ZIP that unambiguously identify each format. Both formats are ZIP
# archives, so the mime a caller reports is not trustworthy on its own - a browser often
# sends application/octet-stream - and the container wins over the label.
_XLSX_MARKER = "xl/workbook.xml"
_DOCX_MARKER = "word/document.xml"


class DocumentParseError(Exception):
    """Base class for every failure this module can report."""


class UnsupportedDocumentError(DocumentParseError):
    """The bytes are not an .xlsx or .docx (or are not a ZIP at all)."""


class CorruptDocumentError(DocumentParseError):
    """The container is the right kind of file but could not be read."""



@dataclass(frozen=True)
class DocumentLimits:
    """Hard caps on how much of a document may reach a model prompt."""

    max_sheets: int = 5
    max_rows_per_sheet: int = 500
    max_cell_chars: int = 200
    max_total_chars: int = 20000

    @classmethod
    def from_metadata(cls, metadata: Optional[Dict[str, Any]]) -> "DocumentLimits":
        """Build limits from node metadata, ignoring blank or non-numeric values."""
        meta = metadata or {}
        defaults = cls()

        def _int(key: str, fallback: int) -> int:
            raw = meta.get(key)
            if raw is None or str(raw).strip() == "":
                return fallback
            try:
                value = int(float(str(raw).strip()))
            except (TypeError, ValueError):
                logger.warning(
                    "DOCUMENT node metadata %r=%r is not a number, using %s", key, raw, fallback)
                return fallback
            return value if value > 0 else fallback

        return cls(
            max_sheets=_int("max_sheets", defaults.max_sheets),
            max_rows_per_sheet=_int("max_rows_per_sheet", defaults.max_rows_per_sheet),
            max_cell_chars=_int("max_cell_chars", defaults.max_cell_chars),
            max_total_chars=_int("max_total_chars", defaults.max_total_chars),
        )


@dataclass
class ParsedDocument:
    """The extraction result. ``text`` is what a downstream prompt may contain."""

    text: str
    kind: str
    truncated: bool = False
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)


def _cell_text(value: Any, max_cell_chars: int) -> str:
    """Render one cell as prompt-safe single-line text."""
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, float) and value.is_integer():
        # openpyxl reads every number as a float: 1.0 is the same fact as 1.
        text = str(int(value))
    elif isinstance(value, (datetime, date, time)):
        text = value.isoformat()
    else:
        text = str(value)
    # A cell is data, not structure: a newline would break the markdown table row, and a
    # pipe would end the cell early, so both are neutralised before the text is capped.
    text = text.replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()
    return text[:max_cell_chars]


def _markdown_table(header: List[str], rows: List[List[str]]) -> str:
    """Render a table as GitHub-flavoured markdown, padding ragged rows."""
    width = len(header)
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in padded)
    return "\n".join(lines)




def _detect_kind(content: bytes) -> Optional[str]:
    """Identify the format from the ZIP's own parts; None when it is not one of ours."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return None
    if _XLSX_MARKER in names:
        return KIND_XLSX
    if _DOCX_MARKER in names:
        return KIND_DOCX
    return None



def _parse_xlsx(content: bytes, limits: DocumentLimits) -> ParsedDocument:
    # read_only + data_only: stream the rows instead of materialising the sheet, and take the
    # CACHED computed values - a formula string would be noise to a model asked about numbers.
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises a zoo of types for a damaged package
        raise CorruptDocumentError(f"Could not read the spreadsheet: {exc}") from exc

    warnings: List[str] = []
    sections: List[str] = []
    total_rows = 0
    sheet_count = len(workbook.worksheets)
    try:
        if sheet_count > limits.max_sheets:
            warnings.append(
                f"Only the first {limits.max_sheets} of {sheet_count} sheet(s) were read.")
        for sheet in workbook.worksheets[: limits.max_sheets]:
            rows: List[List[str]] = []
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index >= limits.max_rows_per_sheet:
                    warnings.append(
                        f"Sheet '{sheet.title}' was cut at {limits.max_rows_per_sheet} rows.")
                    break
                cells = [_cell_text(value, limits.max_cell_chars) for value in row]
                while cells and not cells[-1]:
                    cells.pop()  # trailing empty cells are formatting, not data
                if not any(cells):
                    continue  # a fully blank spacer row carries no fact
                rows.append(cells)
            total_rows += len(rows)

            if not rows:
                sections.append(f"## Sheet: {sheet.title}\n\n(empty sheet)")
                continue
            header, body = rows[0], rows[1:]
            sections.append(f"## Sheet: {sheet.title}\n\n{_markdown_table(header, body)}")
    finally:
        # read_only workbooks keep the archive handle open until closed.
        workbook.close()

    if not sections:
        warnings.append("The spreadsheet contains no rows.")

    return ParsedDocument(
        text="\n\n".join(sections),
        kind=KIND_XLSX,
        truncated=bool(warnings),
        warnings=warnings,
        stats={"sheets_read": min(sheet_count, limits.max_sheets), "rows_read": total_rows},
    )


def _parse_docx(content: bytes, limits: DocumentLimits) -> ParsedDocument:
    from docx import Document

    try:
        document = Document(io.BytesIO(content))
    except Exception as exc:  # a damaged package surfaces as several different types
        raise CorruptDocumentError(f"Could not read the document: {exc}") from exc

    warnings: List[str] = []
    sections: List[str] = []

    paragraphs: List[str] = []
    for para in document.paragraphs:
        text = _cell_text(para.text, limits.max_cell_chars)
        if text:
            paragraphs.append(text)
    if paragraphs:
        sections.append("\n".join(paragraphs))

    tables = document.tables
    if len(tables) > limits.max_sheets:
        warnings.append(
            f"Only the first {limits.max_sheets} of {len(tables)} table(s) were read.")
    for index, table in enumerate(tables[: limits.max_sheets]):
        rows: List[List[str]] = []
        for position, row in enumerate(table.rows):
            if position >= limits.max_rows_per_sheet:
                warnings.append(
                    f"Table {index + 1} was cut at {limits.max_rows_per_sheet} rows.")
                break
            cells = [_cell_text(cell.text, limits.max_cell_chars) for cell in row.cells]
            if any(cells):
                rows.append(cells)
        if not rows:
            continue
        header, body = rows[0], rows[1:]
        sections.append(f"### Table {index + 1}\n\n{_markdown_table(header, body)}")

    if not sections:
        warnings.append("The document contains no text or tables.")

    return ParsedDocument(
        text="\n\n".join(sections),
        kind=KIND_DOCX,
        truncated=bool(warnings),
        warnings=warnings,
        stats={"paragraphs": len(paragraphs), "tables_read": min(len(tables), limits.max_sheets)},
    )


def parse_document(content: bytes, limits: Optional[DocumentLimits] = None,
                   filename: str = "") -> ParsedDocument:
    """Extract prompt-ready text from an .xlsx or .docx.

    Raises ``UnsupportedDocumentError`` for anything that is not one of those two containers
    and ``CorruptDocumentError`` for one that is ours but cannot be read - the DOCUMENT node
    turns both into a node error rather than silently producing an empty prompt.
    """
    if not content:
        raise UnsupportedDocumentError("The document is empty.")

    limits = limits or DocumentLimits()
    kind = _detect_kind(content)
    if kind is None:
        raise UnsupportedDocumentError(
            f"'{filename or 'document'}' is not a readable .xlsx or .docx file."
        )

    parsed = _parse_xlsx(content, limits) if kind == KIND_XLSX else _parse_docx(content, limits)

    # The per-sheet caps bound the WORK; this final cap bounds the PROMPT.
    if limits.max_total_chars and len(parsed.text) > limits.max_total_chars:
        parsed.text = parsed.text[: limits.max_total_chars] + TRUNCATION_MARKER
        parsed.truncated = True

    logger.info(
        "Parsed document %s (%s): %d chars, truncated=%s",
        filename or "(unnamed)", parsed.kind, len(parsed.text), parsed.truncated,
    )
    return parsed

