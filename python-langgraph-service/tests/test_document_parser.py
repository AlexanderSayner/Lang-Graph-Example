"""Unit tests for the DOCUMENT node's deterministic parser.

A document is data, not a model call, so what matters here is that the extraction is exact,
that the size caps fire loudly (a truncated prompt must be visible, not silent), and that
anything which is not an .xlsx/.docx is refused rather than turned into an empty prompt that
a downstream ACTION node would then happily hallucinate over.
"""

import io

import openpyxl
import pytest
from docx import Document

from app.graph_engine.document_parser import (
    DocumentLimits,
    TRUNCATION_MARKER,
    UnsupportedDocumentError,
    parse_document,
)


def make_xlsx(sheets, headers=None):
    """Build a real .xlsx in memory. `sheets` is {title: [[cell, ...], ...]}."""
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title=title)
        if headers:
            sheet.append(headers)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def make_docx(paragraphs=(), table_rows=()):
    """Build a real .docx in memory with optional paragraphs and one table."""
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    if table_rows:
        table = document.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for row_index, row in enumerate(table_rows):
            for col_index, cell in enumerate(row):
                table.rows[row_index].cells[col_index].text = cell
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# --- Spreadsheets ---------------------------------------------------------------------


def test_xlsx_becomes_a_markdown_table_with_typed_cells():
    content = make_xlsx(
        {"Q1": [["ACME", 1250.0, True], ["Globex", 99.5, False]]},
        headers=["client", "amount", "paid"],
    )

    parsed = parse_document(content, filename="q1.xlsx")

    assert parsed.kind == "xlsx"
    assert parsed.truncated is False
    assert parsed.stats == {"sheets_read": 1, "rows_read": 3}
    # 1250.0 is the same fact as 1250; openpyxl reads every number as a float.
    assert "| client | amount | paid |" in parsed.text
    assert "| ACME | 1250 | true |" in parsed.text
    assert "| Globex | 99.5 | false |" in parsed.text


def test_xlsx_each_sheet_gets_its_own_section():
    content = make_xlsx({"Q1": [["a"]], "Q2": [["b"]]})

    parsed = parse_document(content)

    assert "## Sheet: Q1" in parsed.text
    assert "## Sheet: Q2" in parsed.text
    assert parsed.stats["sheets_read"] == 2


def test_xlsx_blank_rows_and_trailing_cells_are_dropped():
    content = make_xlsx(
        {"S": [["a", "b", "", ""], [None, None, None, None], ["c", "d", "", ""]]}
    )

    parsed = parse_document(content)

    # Two data rows (the blank spacer row is skipped) and no trailing pipe runs.
    assert parsed.stats["rows_read"] == 2
    assert "| a | b |" in parsed.text
    assert "| c | d |" in parsed.text


def test_row_cap_truncates_loudly():
    content = make_xlsx({"S": [[i] for i in range(50)]})

    parsed = parse_document(content, DocumentLimits(max_rows_per_sheet=5))

    assert parsed.truncated is True
    assert any("cut at 5 rows" in warning for warning in parsed.warnings)
    # The cap counts rows READ, and the first row is the table's header: 1 + 4 body rows.
    assert parsed.stats["rows_read"] == 5


def test_cell_cap_shortens_a_long_cell():
    content = make_xlsx({"S": [["x" * 500]]})

    parsed = parse_document(content, DocumentLimits(max_cell_chars=10))

    assert "x" * 11 not in parsed.text
    assert "x" * 10 in parsed.text


def test_total_char_cap_applies_a_visible_marker():
    content = make_xlsx({"S": [["y" * 200] for _ in range(50)]})

    parsed = parse_document(content, DocumentLimits(max_cell_chars=200, max_total_chars=300))

    assert len(parsed.text) <= 300 + len(TRUNCATION_MARKER) + 4
    assert parsed.truncated is True
    assert TRUNCATION_MARKER in parsed.text


def test_sheet_cap_reports_the_untouched_sheets():
    content = make_xlsx({f"S{i}": [["v"]] for i in range(8)})

    parsed = parse_document(content, DocumentLimits(max_sheets=2))

    assert parsed.stats["sheets_read"] == 2
    assert any("first 2 of 8 sheet(s)" in warning for warning in parsed.warnings)


def test_cells_cannot_break_out_of_their_row():
    content = make_xlsx({"S": [["line1\nline2", "has|pipe"]]}, headers=["a", "b"])

    parsed = parse_document(content)

    assert "line1 line2" in parsed.text
    assert "has\\|pipe" in parsed.text


# --- Word documents -------------------------------------------------------------------


def test_docx_keeps_paragraphs_and_renders_tables_as_markdown():
    content = make_docx(
        paragraphs=["Invoice summary"],
        table_rows=[["item", "total"], ["widgets", "42"]],
    )

    parsed = parse_document(content, filename="inv.docx")

    assert parsed.kind == "docx"
    assert "Invoice summary" in parsed.text
    assert "| item | total |" in parsed.text
    assert "| widgets | 42 |" in parsed.text
    assert parsed.stats == {"paragraphs": 1, "tables_read": 1}


def test_docx_with_nothing_is_reported_not_silent():
    parsed = parse_document(make_docx())

    assert parsed.text == ""
    assert parsed.truncated is True
    assert any("no text or tables" in warning for warning in parsed.warnings)


# --- Rejection ------------------------------------------------------------------------


def test_a_non_zip_is_refused():
    with pytest.raises(UnsupportedDocumentError):
        parse_document(b"just some plain text")


def test_another_zip_type_is_refused():
    # A real ZIP, but neither an xlsx nor a docx (e.g. a png is also a zip container).
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("image.png", b"\x89PNG")
    with pytest.raises(UnsupportedDocumentError):
        parse_document(buffer.getvalue())


def test_empty_bytes_are_refused():
    with pytest.raises(UnsupportedDocumentError):
        parse_document(b"")


def test_corrupt_xlsx_reports_a_parse_error():
    content = make_xlsx({"S": [["a"]]})[:-12]  # truncate the central directory

    with pytest.raises(Exception) as excinfo:
        parse_document(content)

    from app.graph_engine.document_parser import CorruptDocumentError
    assert isinstance(excinfo.value, (CorruptDocumentError, UnsupportedDocumentError))


# --- Limits from node metadata ---------------------------------------------------------


def test_limits_are_read_from_node_metadata():
    limits = DocumentLimits.from_metadata({
        "max_sheets": "2", "max_rows_per_sheet": "10",
        "max_cell_chars": "5", "max_total_chars": "100",
    })

    assert limits == DocumentLimits(2, 10, 5, 100)


def test_blank_or_rubbish_metadata_falls_back_to_defaults():
    assert DocumentLimits.from_metadata(None) == DocumentLimits()
    assert DocumentLimits.from_metadata({}) == DocumentLimits()
    assert DocumentLimits.from_metadata({"max_sheets": "", "max_cell_chars": "abc"}) == DocumentLimits()
    # A non-positive cap would disable the parser's only guard, so it is ignored.
    assert DocumentLimits.from_metadata({"max_sheets": "0"}).max_sheets == DocumentLimits().max_sheets
