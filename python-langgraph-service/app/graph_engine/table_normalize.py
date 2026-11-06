"""Deterministic normalization of time-report spreadsheets, for the ``DOCUMENT`` node.

Real exports are written for people, not for models, and the three features that make them
readable to a person are exactly what makes a model invent facts over them:

* **Preamble rows.** The title and the period sit ABOVE the table, so a parser that trusts
  the first row renders ``| Отчет по компании |`` as the column names and every later row
  becomes a cell of a one-column table.
* **Merged cells.** The person's name is typed once and merged down their block, so most
  rows carry no name at all - the model has to guess whose hours it is reading.
* **Appendix blocks.** Totals (``Всего billable:``) and pivot summaries (``Показатели``,
  ``*Направление QA``) are written BELOW the data in the SAME columns. They have a
  non-empty "date" and a non-empty "hours", so a naive "drop blank rows" filter keeps them
  as if they were somebody's working day.

Normalization is **opt-in**: a node sets ``normalize: "table"`` in its metadata and nothing
changes for any other node - without the option the parser renders the sheet exactly as it
always did. The rules are purely deterministic: a header row is located by column aliases,
block columns are filled down, a row only counts as somebody's working day when it has a
person-shaped name, a date-looking date and hours-looking hours, and the task key
(``INTGR-4931 Название`` fused into one cell, or a separate ``#`` / ``Ключ задачи`` column)
is pulled out on its own.

What normalization does NOT do: it never filters rows to one person. The comparison a graph
performs is a business decision made downstream; this module only removes structural noise
and narrows a fourteen-column sheet to the five the comparison needs, so the size caps above
it stop having to cut the tail off the report.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# How many rows below the top of a sheet may still turn out to be the header. Real exports
# carry one or two title rows; twenty-five is generous without reading a sheet twice.
DEFAULT_HEADER_SCAN_ROWS = 25

DEFAULT_COLUMNS: Tuple[str, ...] = ("person", "date", "key", "task", "hours")

# Metadata spellings that switch normalization on. Anything else (including a blank) leaves
# it off, so a node that says nothing keeps the historical behaviour byte for byte.
_ENABLED_VALUES = frozenset({"table", "1", "true", "yes", "on", "да", "вкл"})

# Logical field -> the header spellings it answers to. Comparison is on the lowered,
# whitespace-collapsed cell, first as a whole value and then as a substring, which is what
# lets "Дата" win its column while "Дата приема на работу" (a hire date, useless here)
# stays unclaimed.
_ALIASES: Dict[str, Tuple[str, ...]] = {
    "person": ("фио", "сотрудник", "employee", "имя", "name"),
    "date": ("дата", "дата работы", "дата выполнения", "date"),
    "task": ("задача", "задачи", "название задачи", "task", "title"),
    "key": ("#", "ключ задачи", "task key", "ключ", "key", "id задачи"),
    "project": ("проект", "project"),
    "hours": ("billable hours", "часы", "hours", "billable", "отработано часов"),
    "status": ("статус", "status"),
    "description": ("описание", "description"),
    "type": ("тип", "type"),
}

# The order fields claim columns in. Person first (it identifies the table), then the dates
# and the task, and hours LAST among them so "Billable" does not steal the column from
# "Billable hours" before both have been weighed.
_FIELD_PRIORITY: Tuple[str, ...] = (
    "person", "date", "task", "key", "project", "status", "description", "type", "hours",
)

# Output header for each column a node may ask for. Russian, because that is what the
# reports and the downstream prompts speak.
_COLUMN_LABELS: Dict[str, str] = {
    "person": "ФИО",
    "date": "Дата",
    "key": "Ключ",
    "task": "Задача",
    "hours": "Часы",
    "project": "Проект",
    "status": "Статус",
    "description": "Описание",
    "type": "Тип",
}

# A person is two to five capitalized tokens: "Миронов Сергей Андреевич", "Иванова-Пирс Анна",
# "John Smith", "С. А. Кузнецов". Every appendix label this report family fails it -
# "Показатели" (one token), "Отдел продаж ML" (lowercase token), "*Направление Frontend/JS"
# (symbols), "Всего billable:" (lowercase and a colon).
_PERSON_TOKEN = re.compile(r"^[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'’\.\-]*$")
_PERSON_TOKEN_COUNT = (2, 5)

# "INTGR-4931 Название задачи" -> ("INTGR-4931", "Название задачи"); a bare "INTGR-4931"
# is a key with no name; anything else is a task name with no key of its own.
_TASK_WITH_KEY = re.compile(r"^([A-Za-z][A-Za-z0-9]*-\d+)(?:\s+(.+))?$")

_DATE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:$|[T ])")
_DATE_DOT_Y = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_DATE_DOT_2Y = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2})$")
_DATE_SLASH_Y = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_DATE_DOT_NO_YEAR = re.compile(r"^\d{1,2}\.\d{1,2}$")



@dataclass(frozen=True)
class NormalizeOptions:
    """How a ``DOCUMENT`` node wants its spreadsheet normalized (its ``normalize`` metadata)."""

    enabled: bool = False
    header_scan_rows: int = DEFAULT_HEADER_SCAN_ROWS
    columns: Tuple[str, ...] = DEFAULT_COLUMNS

    @classmethod
    def from_metadata(cls, metadata: Optional[Dict[str, Any]]) -> "NormalizeOptions":
        """Read the option block from node metadata; blank or rubbish means "off"."""
        meta = metadata or {}

        raw_mode = str(meta.get("normalize") or "").strip().lower()
        enabled = raw_mode in _ENABLED_VALUES
        if raw_mode and not enabled:
            logger.warning(
                "DOCUMENT node metadata normalize=%r is not one of %s; parsing as-is",
                raw_mode, ", ".join(sorted(_ENABLED_VALUES)))

        raw_scan = meta.get("normalize_header_scan")
        scan_rows = DEFAULT_HEADER_SCAN_ROWS
        if raw_scan is not None and str(raw_scan).strip() != "":
            try:
                parsed_scan = int(float(str(raw_scan).strip()))
                scan_rows = parsed_scan if 0 < parsed_scan <= 1000 else DEFAULT_HEADER_SCAN_ROWS
            except (TypeError, ValueError):
                logger.warning(
                    "DOCUMENT node metadata normalize_header_scan=%r is not a number, using %s",
                    raw_scan, DEFAULT_HEADER_SCAN_ROWS)

        return cls(enabled=enabled, header_scan_rows=scan_rows,
                   columns=_parse_columns(meta.get("normalize_columns")))


def _parse_columns(raw: Any) -> Tuple[str, ...]:
    """``"person,date,project"`` -> tuple; unknown names are dropped with a warning."""
    if raw is None or str(raw).strip() == "":
        return DEFAULT_COLUMNS
    requested: List[str] = []
    unknown: List[str] = []
    for part in str(raw).split(","):
        name = part.strip().lower()
        if not name:
            continue
        if name in _COLUMN_LABELS:
            requested.append(name)
        else:
            unknown.append(name)
    if unknown:
        logger.warning(
            "DOCUMENT node metadata normalize_columns contains unknown column(s) %s; known: %s",
            ", ".join(sorted(unknown)), ", ".join(_COLUMN_LABELS))
    if not requested:
        return DEFAULT_COLUMNS
    # The person column identifies every row; a graph cannot ask for an anonymous report.
    if "person" not in requested:
        requested.insert(0, "person")
    return tuple(requested)


@dataclass
class NormalizedSheet:
    """One sheet after normalization: what to render, what it cost, what to warn about."""

    header: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    header_row: int = 0  # 1-based row the table's own column names were found on

_HOURS = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*(?:h|ч|час(?:а|ов)?)?"
    r"(?:\s+(\d+)\s*(?:m|м|мин(?:ут(?:а|ы))?)?)?\s*$",
    re.IGNORECASE,
)


def normalize_timesheet(rows: Sequence[Sequence[str]],
                        options: NormalizeOptions) -> Optional[NormalizedSheet]:
    """Turn raw sheet rows into a narrow, noise-free table, or ``None`` when it cannot.

    ``None`` means "the caller should parse this sheet as it always did": no header row was
    recognizable, or not a single data row survived validation. Falling back is the whole
    safety story here - a normalizer that misunderstands a format it has never seen is worse
    than no normalizer, and the caller (with its warning) says so out loud.
    """
    if not rows:
        return None

    header_index = _find_header(rows, options)
    if header_index is None:
        logger.info("normalize: no header row within the first %d row(s)",
                    options.header_scan_rows)
        return None

    header_row = list(rows[header_index])
    body = [list(row) for row in rows[header_index + 1:]]
    fields = _map_columns(header_row, body)
    if "person" not in fields:
        logger.info("normalize: header row %d has no person column", header_index + 1)
        return None
    if not body:
        logger.info("normalize: header row %d has no data below it", header_index + 1)
        return None

    warnings: List[str] = []
    level = 0
    kept, counts = _collect(body, fields, options, level)
    while not kept and counts["rows_dropped"] and level < 2:
        level += 1
        kept, counts = _collect(body, fields, options, level)
    if not kept:
        logger.info("normalize: 0 of %d row(s) survived validation; parsing the sheet as-is",
                    counts["rows_dropped"])
        return None
    if level:
        warnings.append(
            f"Row validation was relaxed ({level}) to keep {len(kept)} row(s); "
            "the strict pass kept none.")

    # A column the sheet does not have cannot be rendered. The derived key is the exception:
    # it may be carved out of the task text even when there is no key column at all.
    output_fields = [
        name for name in options.columns
        if name in fields or (name == "key" and "task" in fields)
    ]
    counts["rows_kept"] = len(kept)

    return NormalizedSheet(
        header=[_COLUMN_LABELS[name] for name in output_fields],
        rows=[[values[name] for name in output_fields] for values in kept],
        stats=counts,
        warnings=warnings,
        header_row=header_index + 1,
    )


def _find_header(rows: Sequence[Sequence[str]], options: NormalizeOptions) -> Optional[int]:
    """Index of the first row that looks like the table's own column names."""
    for index, row in enumerate(rows[: options.header_scan_rows]):
        fields = _map_columns(row)
        if "person" in fields and ({"date", "task", "hours"} & set(fields)):
            return index
    return None



def _map_columns(header: Sequence[str], body: Sequence[Sequence[str]] = ()) -> Dict[str, int]:
    """Logical field -> column index, by exact alias first and substring second."""
    cells = [_norm_cell(cell) for cell in header]
    assigned: Dict[str, int] = {}
    taken: set = set()

    alias_sets = {name: set(values) for name, values in _ALIASES.items()}
    for name in _FIELD_PRIORITY:
        for index, cell in enumerate(cells):
            if index in taken or not cell:
                continue
            if cell in alias_sets[name]:
                assigned[name] = index
                taken.add(index)
                break

    for name in _FIELD_PRIORITY:
        if name in assigned:
            continue
        for index, cell in enumerate(cells):
            if index in taken or len(cell) < 3:
                continue
            if any(alias in cell or cell in alias
                   for alias in _ALIASES[name] if len(alias) >= 3):
                assigned[name] = index
                taken.add(index)
                break

    _refine_hours(assigned, cells, body)
    return assigned


def _refine_hours(assigned: Dict[str, int], cells: List[str],
                  body: Sequence[Sequence[str]]) -> None:
    """When both ``Billable`` and ``Billable hours`` exist, take the one holding numbers.

    An export may keep a Yes/No flag under ``Billable`` and the real hours under
    ``Billable hours``; which is which is decided by the values, not by the spelling.
    """
    alias_set = set(_ALIASES["hours"])
    candidates = [index for index, cell in enumerate(cells) if cell in alias_set]
    if len(candidates) <= 1:
        return

    def rate(index: int) -> float:
        seen = valid = 0
        for row in body[:100]:
            value = _cell(row, index)
            if not value:
                continue
            seen += 1
            if _parse_hours(value) is not None:
                valid += 1
        return valid / seen if seen else 0.0

    best = max(candidates, key=lambda index: (rate(index), -index))
    if rate(best) > 0:
        assigned["hours"] = best
    elif "hours" not in assigned:
        assigned["hours"] = best



def _collect(body: Sequence[Sequence[str]], fields: Dict[str, int],
             options: NormalizeOptions, level: int
             ) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    """Validate the body at one strictness level and build the output rows.

    ``level`` 0 requires a person, a date and hours; 1 drops the hours requirement; 2 only
    requires a person. Each level exists only because the previous one kept ZERO rows: an
    export with an unusual date or hours spelling must still reach the model rather than
    vanish behind a filter that decided it was junk.
    """
    counts = {
        "rows_seen": 0,
        "rows_dropped": 0,
        "dropped_not_person": 0,
        "dropped_no_date": 0,
        "dropped_no_hours": 0,
    }
    kept: List[Dict[str, str]] = []
    last_person = ""
    last_project = ""

    for row in body:
        if not any(row):
            continue  # a spacer row carries no fact
        counts["rows_seen"] += 1

        person_cell = _cell(row, fields.get("person"))
        if _looks_like_person(person_cell):
            # Only a real name extends the merged block: a section label such as
            # "Показатели" must not become the name the next blank rows inherit.
            last_person = person_cell
            person = person_cell
        else:
            person = person_cell or last_person  # blank inherits, a junk label does not
        if not _looks_like_person(person):
            counts["rows_dropped"] += 1
            counts["dropped_not_person"] += 1
            continue

        date_raw = _cell(row, fields.get("date")) if "date" in fields else ""
        if "date" in fields and level <= 1 and not _looks_like_date(date_raw):
            counts["rows_dropped"] += 1
            counts["dropped_no_date"] += 1
            continue

        hours_raw = _cell(row, fields.get("hours")) if "hours" in fields else ""
        if "hours" in fields and level == 0 and _parse_hours(hours_raw) is None:
            counts["rows_dropped"] += 1
            counts["dropped_no_hours"] += 1
            continue

        project_cell = _cell(row, fields.get("project")) if "project" in fields else ""
        if project_cell:
            last_project = project_cell
        project = project_cell or last_project

        task_raw = _cell(row, fields.get("task")) if "task" in fields else ""
        key, task_name = _split_task(task_raw)
        if not key:
            key = _cell(row, fields.get("key")) if "key" in fields else ""
        if not task_name:
            task_name = task_raw

        kept.append({
            "person": person,
            "date": _to_iso_date(date_raw),
            "key": key,
            "task": task_name,
            "hours": hours_raw,
            "project": project,
            "status": _cell(row, fields.get("status")) if "status" in fields else "",
            "description": _cell(row, fields.get("description")) if "description" in fields else "",
            "type": _cell(row, fields.get("type")) if "type" in fields else "",
        })

    return kept, counts



def _norm_cell(text: Any) -> str:
    """Lowered, whitespace-collapsed, colon-stripped cell - the shape aliases match on."""
    return re.sub(r"\s+", " ", str(text)).strip().rstrip(":").lower()


def _cell(row: Sequence[str], index: Optional[int]) -> str:
    if index is None or index >= len(row):
        return ""
    return str(row[index]).strip()


def _looks_like_person(text: str) -> bool:
    if not text:
        return False
    tokens = text.split()
    low, high = _PERSON_TOKEN_COUNT
    if not low <= len(tokens) <= high:
        return False
    return all(_PERSON_TOKEN.match(token) for token in tokens)


def _looks_like_date(text: str) -> bool:
    if not text:
        return False
    return bool(
        _DATE_ISO.match(text) or _DATE_DOT_Y.match(text) or _DATE_DOT_2Y.match(text)
        or _DATE_SLASH_Y.match(text) or _DATE_DOT_NO_YEAR.match(text)
    )


def _to_iso_date(text: str) -> str:
    """``01.10.2026`` -> ``2026-10-01``; anything unrecognised is kept exactly as written."""
    if not text:
        return text
    match = _DATE_ISO.match(text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    match = _DATE_DOT_Y.match(text) or _DATE_SLASH_Y.match(text)
    if match:
        day, month, year = match.group(1), match.group(2), match.group(3)
        return f"{year}-{int(month):02d}-{int(day):02d}"
    match = _DATE_DOT_2Y.match(text)
    if match:
        day, month, year = match.group(1), match.group(2), match.group(3)
        return f"20{year}-{int(month):02d}-{int(day):02d}"
    return text


def _parse_hours(text: str) -> Optional[float]:
    """``8`` / ``4,5`` / ``4h 30m`` / ``4 ч 30 мин`` -> hours, or ``None``."""
    if not text:
        return None
    match = _HOURS.match(text)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    if match.group(2):
        value += int(match.group(2)) / 60.0
    return value


def _split_task(text: str) -> Tuple[str, str]:
    """``INTGR-4931 Название`` -> ``("INTGR-4931", "Название")``."""
    if not text:
        return "", ""
    match = _TASK_WITH_KEY.match(text)
    if match:
        return match.group(1), match.group(2) or ""
    return "", text

