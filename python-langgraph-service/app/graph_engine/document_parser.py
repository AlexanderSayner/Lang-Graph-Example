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

A file that Excel accepts is accepted here too. The one thing openpyxl is strict about is
the shape of a colour (aRGB hex in the stylesheet), and exported spreadsheets do emit
malformed ones - so a workbook that fails to load is retried once with that part repaired
in memory (see ``_repair_xlsx_styles``). The repair is confined to the stylesheet, so the
number formats that turn date cells into dates survive it.
"""

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Dict, List, Optional, Tuple

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



# --- Tolerating a stylesheet Excel accepts but openpyxl refuses ------------------------
# openpyxl validates colour values strictly: every ``rgb`` attribute must be aRGB hex of
# exactly 8 (or 6) digits. Exported spreadsheets are not always written that way - a
# generator that emits the RGB triplet with the alpha pair written once ("F44546A"), or a
# name like "auto", still opens in Excel - and rejecting the WHOLE workbook over a colour
# would throw away every row of a perfectly readable file. The stylesheet is therefore
# repaired in memory (see _repair_xlsx_styles), and only after the first load has already
# failed, so a well-formed workbook is never touched and a genuinely damaged one still
# reports its own original error.

_XLSX_STYLES_PART = "xl/styles.xml"
_RGB_ATTRIBUTE = re.compile(rb'(?<![A-Za-z])rgb\s*=\s*"([^"]*)"')
# openpyxl's own acceptance rule (openpyxl.styles.colors.aRGB_REGEX): 8 or 6 hex digits.
# Six is accepted as it stands (openpyxl prepends a transparent alpha), so such a value is
# not something to repair.
_ACCEPTABLE_COLOUR = re.compile(rb"^[0-9A-Fa-f]{8}$|^[0-9A-Fa-f]{6}$")
_HEX_7 = re.compile(rb"^[0-9A-Fa-f]{7}$")
# openpyxl.styles.colors.BLACK - what openpyxl itself writes for a <color> with no rgb
# attribute at all, and therefore a value it always accepts.
_FALLBACK_COLOUR = b"00000000"


def _normalise_colour(value: bytes) -> Optional[bytes]:
    """A spelling openpyxl accepts, or ``None`` when the value may stay as it is.

    Eight- and six-hex-digit values are accepted as they stand, so ``None`` means "leave
    this one alone" (cleaning still applies: a "#"-prefixed value is valid once the hash is
    gone, and is then reported as a fix). Seven hex digits read as an RGB triplet whose
    alpha pair was written once - padded, that is the colour the author saw. Anything else
    is replaced with openpyxl's own default rather than dropped: ``<rgbColor>`` entries of
    the indexed palette REQUIRE the attribute, so removing it would turn a repairable
    workbook into an unreadable one. Either way the value only decides whether the file
    loads at all - a colour never changes a cell's text, which is all this parser reads.
    """
    cleaned = value.strip().lstrip(b"#").replace(b" ", b"")
    if _ACCEPTABLE_COLOUR.match(cleaned):
        return None if cleaned == value else cleaned
    if _HEX_7.match(cleaned):
        return b"F" + cleaned
    return _FALLBACK_COLOUR


def _fix_rgb_attributes(styles_xml: bytes) -> Tuple[bytes, int]:
    """The stylesheet with every ``rgb`` attribute openpyxl would refuse corrected."""
    fixed = 0

    def replace(match: "re.Match[bytes]") -> bytes:
        nonlocal fixed
        normalised = _normalise_colour(match.group(1))
        if normalised is None:
            return match.group(0)  # already valid, left byte-for-byte as it was
        fixed += 1
        return b'rgb="' + normalised + b'"'

    return _RGB_ATTRIBUTE.sub(replace, styles_xml), fixed


def _repair_xlsx_styles(content: bytes) -> Optional[bytes]:
    """The same workbook with a repaired stylesheet, or ``None`` when nothing to repair.

    ``None`` is also the answer for a package without a stylesheet or one that cannot be
    opened at all - the caller then re-raises its ORIGINAL failure, so a repair attempt
    never masks a different, real problem. Every entry is copied through its own
    ``ZipInfo``, so the container keeps its own compression method and order; only the
    stylesheet's bytes differ. Operates on bytes throughout: the XML is not decoded, so
    a workbook carrying non-UTF-8 elsewhere is passed through untouched.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as source:
            try:
                styles_xml = source.read(_XLSX_STYLES_PART)
            except KeyError:
                return None  # no stylesheet to repair (unusual, but legal)

            repaired, fixed = _fix_rgb_attributes(styles_xml)
            if fixed == 0:
                return None

            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
                for info in source.infolist():
                    payload = repaired if info.filename == _XLSX_STYLES_PART \
                        else source.read(info.filename)
                    target.writestr(info, payload)
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        logger.warning("Could not repair the workbook stylesheet: %s", exc)
        return None

    logger.info("Repaired %d malformed colour value(s) in the workbook stylesheet", fixed)
    return output.getvalue()


def _parse_xlsx(content: bytes, limits: DocumentLimits) -> ParsedDocument:
    # read_only + data_only: stream the rows instead of materialising the sheet, and take the
    # CACHED computed values - a formula string would be noise to a model asked about numbers.
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises a zoo of types for a damaged package
        # One retry, with the stylesheet normalised: openpyxl's strict colour validation is
        # the single most common reason a file that opens fine in Excel is refused here, and
        # the repair is confined to that part so number formats (and with them every date
        # cell) survive. The repaired workbook is loaded with the SAME read_only/data_only
        # flags, so the extraction below is identical whichever attempt succeeded.
        repaired = _repair_xlsx_styles(content)
        if repaired is None:
            raise CorruptDocumentError(f"Could not read the spreadsheet: {exc}") from exc
        try:
            workbook = openpyxl.load_workbook(io.BytesIO(repaired), read_only=True,
                                              data_only=True)
        except Exception as repaired_exc:
            # The stylesheet was not the real problem after all: report the failure the
            # caller saw, chained to what the retry found, so nothing is hidden.
            raise CorruptDocumentError(f"Could not read the spreadsheet: {exc}") \
                from repaired_exc

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

