"""Tests for the opt-in table normalization the DOCUMENT node can ask for.

These fixtures mirror, in miniature, the three habits of a real time export: a title and a
period above the table, the person's name merged down their block, and appendix blocks
(totals, pivots) written below the data in the same columns. What has to hold is that
normalization removes exactly the structure noise and nothing else, that the task key is
recoverable from either of the two places it lives, and - most importantly - that a node
which does not ask for normalization gets byte-for-byte the output it always got.
"""

import io

import openpyxl

from app.graph_engine.document_parser import DocumentLimits, parse_document
from app.graph_engine.table_normalize import NormalizeOptions

# The real export's own column order: two date columns (hire date and work date), the key
# under "#", and Billable carrying the hours.
HEADERS = [
    "ФИО", "Дата приема на работу", "Направление", "#", "Проект", "Задача", "Тип",
    "Описание", "Дата", "Статус", "Billable", "Not Billable", "Офис", "Тип занятости",
]
PRELUDE = [
    ["Отчет по компании"],
    ["Период:  01 октября 2026  -  31 октября 2026"],
]


def make_report(*data_rows, prelude=True, headers=None):
    """A workbook shaped like the export: optional title rows, the header, the data."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "sheet1"
    if prelude:
        for row in PRELUDE:
            sheet.append(row)
    sheet.append(list(headers) if headers is not None else list(HEADERS))
    for row in data_rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def time_row(person, work_date, task, hours, key="INV-103905"):
    """One working day, in the export's fourteen columns (person merged down the block)."""
    return [
        person, "01.01.2025", "Направление QA", key, "Инвитро-ИТ|Инвитро", task, "task",
        "описание", work_date, "В работе", hours, "0", "Офис", "Полная",
    ]


TOTALS_ROW = [""] * 10 + ["Всего billable:"]
PIVOT_ROW = ["Показатели", "", "416", "", "Общая численность", "", "", "",
             "*Направление QA", "", "50"]

NORMALIZED = NormalizeOptions(enabled=True)


def parse(content, normalize=None, filename="report.xlsx"):
    return parse_document(content, DocumentLimits(), filename=filename, normalize=normalize)


# --- The header: the table's own names, not the title above them ------------------------


def test_the_title_rows_are_not_mistaken_for_the_header():
    parsed = parse(make_report(time_row("Миронов Сергей Андреевич", "01.10.2026",
                                        "INTGR-4914 Поддержка интеграции", "8")),
                   normalize=NORMALIZED)

    assert "| ФИО | Дата | Ключ | Задача | Часы |" in parsed.text
    assert "Отчет по компании" not in parsed.text  # the preamble carries no column names
    assert parsed.stats["header_row"] == 3
    assert parsed.stats["normalized_sheets"] == 1


def test_an_export_without_a_title_still_finds_its_header():
    parsed = parse(make_report(time_row("Клычников Денис Александрович", "02.10.2026",
                                        "Онбординг", "8"), prelude=False),
                   normalize=NORMALIZED)

    assert parsed.stats["header_row"] == 1
    assert parsed.stats["rows_kept"] == 1


# --- Merged person cells ---------------------------------------------------------------


def test_blank_person_cells_inherit_the_name_of_their_block():
    parsed = parse(
        make_report(
            time_row("Миронов Сергей Андреевич", "01.10.2026", "INTGR-4914 Название", "8"),
            time_row("", "02.10.2026", "INTGR-4914 Название", "8"),
            time_row("", "05.10.2026", "INTGR-4931 Другая задача", "3"),
        ),
        normalize=NORMALIZED,
    )

    assert parsed.stats["rows_kept"] == 3
    # Every rendered row carries the name, including the two whose cell was empty: an
    # empty first cell would render as "|  |".
    assert "|  |" not in parsed.text



# --- The appendix blocks: totals and pivots written in the data's own columns ----------


def test_totals_and_pivot_rows_are_dropped_with_their_reasons_counted():
    parsed = parse(
        make_report(
            time_row("Миронов Сергей Андреевич", "01.10.2026", "INTGR-4914 Название", "8"),
            TOTALS_ROW,
            PIVOT_ROW,
            time_row("", "02.10.2026", "INTGR-4914 Название", "8"),
        ),
        normalize=NORMALIZED,
    )

    # Neither the "Всего billable:" label nor the pivot's numbers are somebody's day, even
    # though both have a non-empty "date"-column cell and a non-empty "hours"-column cell.
    assert "Всего" not in parsed.text
    assert "Показатели" not in parsed.text
    assert "Направление QA" not in parsed.text  # the pivot's label sat in the date column
    assert parsed.stats["rows_kept"] == 2
    assert parsed.stats["dropped_not_person"] == 1  # Показатели is not a name
    assert parsed.stats["dropped_no_date"] == 1     # the totals row has no date at all


def test_a_section_label_never_becomes_the_name_the_next_rows_inherit():
    # The label sits where a name should be; rows below it have a blank person cell. They
    # must fall back to the last REAL name, not to the label.
    parsed = parse(
        make_report(
            time_row("Миронов Сергей Андреевич", "01.10.2026", "INTGR-4914 Название", "8"),
            PIVOT_ROW,
            time_row("", "03.10.2026", "INTGR-4914 Название", "8"),
        ),
        normalize=NORMALIZED,
    )

    assert parsed.stats["rows_kept"] == 2
    assert "Миронов" in parsed.text


# --- The task key, wherever the export put it ------------------------------------------


def test_the_key_is_carved_out_of_the_task_text_and_the_task_keeps_its_name():
    parsed = parse(
        make_report(time_row("Миронов Сергей Андреевич", "01.10.2026",
                             "INTGR-4931 Поддержка интеграции МиД", "8")),
        normalize=NORMALIZED,
    )

    assert "| Миронов Сергей Андреевич | 2026-10-01 | INTGR-4931 | Поддержка интеграции МиД | 8 |" \
        in parsed.text


def test_a_separate_key_column_is_used_when_the_task_text_has_no_key():
    headers = ["Ключ задачи", "ФИО", "Проект", "Задача", "Дата", "Billable hours"]
    parsed = parse(
        make_report(
            ["INV-64371", "Клычников Денис Александрович", "Инвитро-ИТ", "Онбординг",
             "02.10.2026", "8"],
            headers=headers,
        ),
        normalize=NORMALIZED,
    )

    # No prefix in "Онбординг", so the key comes from its own column - and the work-date
    # alias wins over anything that merely contains the word "Дата".
    assert "| Клычников Денис Александрович | 2026-10-02 | INV-64371 | Онбординг | 8 |" \
        in parsed.text


def test_the_hire_date_column_does_not_win_over_the_work_date_column():
    parsed = parse(
        make_report(time_row("Миронов Сергей Андреевич", "05.10.2026",
                             "INTGR-4914 Название", "8")),
        normalize=NORMALIZED,
    )

    assert "2025-01-01" not in parsed.text   # "Дата приема на работу" is not the work date
    assert "2026-10-05" in parsed.text


# --- Opt-out is the contract ------------------------------------------------------------


def test_a_node_that_does_not_ask_for_normalization_gets_the_old_output():
    content = make_report(time_row("Миронов Сергей Андреевич", "01.10.2026",
                                   "INTGR-4914 Название", "8"))

    parsed = parse(content)  # no normalize argument at all

    # The historical behaviour: the first non-empty row IS the header, title and all.
    assert "| Отчет по компании |" in parsed.text
    assert "| ФИО | Дата |" not in parsed.text
    # And the stats carry no normalization keys at all - unchanged contract, not zeros.
    assert set(parsed.stats) == {"sheets_read", "rows_read"}
    assert parsed.warnings == []


def test_a_sheet_whose_header_is_unrecognizable_is_parsed_as_is_with_a_warning():
    content = make_report(["alice", 1], ["bob", 2], prelude=False,
                          headers=["col_a", "col_b"])

    parsed = parse(content, normalize=NORMALIZED)

    assert "| col_a | col_b |" in parsed.text      # rendered the old way
    assert any("no table could be normalized" in warning for warning in parsed.warnings)
    assert parsed.stats["normalized_sheets"] == 0
    assert parsed.stats["rows_kept"] == 0


def test_validation_relaxes_only_when_the_strict_pass_keeps_nothing():
    # A date format the strict pass does not know: better to hand the model every row it
    # can identify than to hand it an empty table that looks authoritative.
    content = make_report(
        time_row("Миронов Сергей Андреевич", "October 1, 2026", "INTGR-4914 Название", "8"),
        time_row("", "October 2, 2026", "INTGR-4914 Название", "8"),
    )

    parsed = parse(content, normalize=NORMALIZED)

    assert parsed.stats["rows_kept"] == 2
    assert any("validation was relaxed" in warning for warning in parsed.warnings)
    # The date is kept verbatim rather than mangled into something plausible.
    assert "October 1, 2026" in parsed.text


def test_a_docx_is_never_normalized():
    from docx import Document

    document = Document()
    document.add_paragraph("Отчет по компании")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "ФИО"
    table.rows[0].cells[1].text = "Часы"
    table.rows[1].cells[0].text = "Миронов Сергей"
    table.rows[1].cells[1].text = "8"
    buffer = io.BytesIO()
    document.save(buffer)

    plain = parse_document(buffer.getvalue(), DocumentLimits(), filename="r.docx")
    with_option = parse(buffer.getvalue(), normalize=NORMALIZED, filename="r.docx")

    assert with_option.text == plain.text
    assert with_option.warnings == plain.warnings


# --- The option block -------------------------------------------------------------------


def test_options_are_read_from_node_metadata():
    assert NormalizeOptions.from_metadata(None).enabled is False
    assert NormalizeOptions.from_metadata({}).enabled is False
    assert NormalizeOptions.from_metadata({"normalize": ""}).enabled is False
    assert NormalizeOptions.from_metadata({"normalize": "widely"}).enabled is False

    on = NormalizeOptions.from_metadata({"normalize": "table", "normalize_header_scan": "5"})
    assert on.enabled is True
    assert on.header_scan_rows == 5
    assert on.columns == ("person", "date", "key", "task", "hours")

    assert NormalizeOptions.from_metadata(
        {"normalize": "TABLE"}).enabled is True  # case does not matter


def test_a_column_list_may_add_columns_but_may_not_drop_the_person():
    options = NormalizeOptions.from_metadata(
        {"normalize": "table", "normalize_columns": "date, project, nonsense"})

    # The unknown name is dropped; the person column is forced back in - a report with no
    # names in it is not a report of anybody's work.
    assert options.columns == ("person", "date", "project")


def test_an_extra_column_the_node_asked_for_is_rendered():
    content = make_report(time_row("Миронов Сергей Андреевич", "01.10.2026",
                                   "INTGR-4914 Название", "8"))
    options = NormalizeOptions(enabled=True, columns=("person", "date", "task", "project"))

    parsed = parse(content, normalize=options)

    assert "| ФИО | Дата | Задача | Проект |" in parsed.text
    # The pipe inside the project name is escaped by the parser, as it must be in markdown.
    assert "Инвитро-ИТ\\|Инвитро" in parsed.text
    assert "Часы" not in parsed.text            # hours were not requested
    assert "Ключ" not in parsed.text            # neither was the key

