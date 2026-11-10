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

A second mode, ``normalize: "llm"``, exists for documents the aliases cannot read: the model
is shown a compact fingerprint of the sheet and asked to PROPOSE a mapping (which row is the
header, which column plays which role) - it never sees the whole sheet and never rewrites a
single cell. The proposal is validated against the grid by :mod:`app.graph_engine.table_mapping`
and applied here by the same deterministic code as ``table``; an invalid or refused proposal
falls back to ``table``, and a sheet neither can read is rendered as-is with a warning. That
is the whole contract: the model decides WHERE things are, code decides WHAT they become.

What normalization does NOT do: it never filters rows to one person. The comparison a graph
performs is a business decision made downstream; this module only removes structural noise
and narrows a fourteen-column sheet to the five the comparison needs, so the size caps above
it stop having to cut the tail off the report.
"""

import json
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

# The one spelling that buys a model. It is NOT in _ENABLED_VALUES: `llm` is a mode of an
# enabled normalization, not a synonym for "on", and it is the only mode allowed to spend
# tokens (see llm_normalize_requested - the servicer resolves a client only for these nodes).
LLM_MODE = "llm"
MODE_TABLE = "table"

# A mapping the model rated below this is not applied. Self-reported, so it is a guard
# against the model's own doubt rather than a measurement - the hard checks are the
# per-column type probes in table_mapping.
DEFAULT_CONFIDENCE_MIN = 0.6

# Type probes: a mapped column must parse at this share of its NON-EMPTY cells to be
# accepted at all (fatal), and this share to be accepted without a warning.
TYPE_PROBE_FLOOR = 0.3
TYPE_PROBE_WARN_AT = 0.6
# Fewer non-empty cells than this and the column is simply unjudgeable - its ratio is 0 by
# definition, which must not fail a mapping for a column the document barely uses.
TYPE_PROBE_MIN_SAMPLES = 5


def llm_normalize_requested(metadata: Optional[Dict[str, Any]]) -> bool:
    """Does this node's metadata ask for an LLM mapping - the only mode that may call a model?

    One source of truth for the two call sites that must not drift: the servicer resolves an
    LLM client only for nodes where this is true, and the DOCUMENT handler only takes its
    proposal path there. Every other value (blank, `table`, rubbish) stays deterministic.
    """
    return str((metadata or {}).get("normalize") or "").strip().lower() == LLM_MODE

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

# The roles a mapping proposal may name, in the order they are listed to the model. Public
# because the prompt and the validator are in table_mapping and must not re-declare it: a
# role the prompt offers but the validator rejects (or vice versa) is a contract nobody can
# satisfy.
KNOWN_ROLES: Tuple[str, ...] = tuple(_COLUMN_LABELS)

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
    # Which engine runs when ``enabled``. The default is MODE_TABLE so the historical
    # ``NormalizeOptions(enabled=True)`` keeps meaning exactly what it always meant.
    mode: str = MODE_TABLE
    # Roles the mapping proposal has to consider - `normalize_schema`, the same vocabulary as
    # `columns` but WITHOUT forcing `person`: a foreign document has none. Unmapped roles are
    # reported, never fatal; what fails a proposal are the type probes on the roles it claims.
    schema: Tuple[str, ...] = DEFAULT_COLUMNS
    # A proposal the model rated below this is discarded (0 disables the rating check).
    confidence_min: float = DEFAULT_CONFIDENCE_MIN
    # sheet title -> an already-resolved mapping to apply. The DOCUMENT node fills this from
    # the proposal path (or from the pinned `normalize_mapping` metadata, stored under "")
    # BEFORE parsing, so the parser itself never calls a model - it only applies what it is
    # given, exactly like it applies the deterministic rules.
    sheet_mappings: Optional[Dict[str, Dict[str, Any]]] = None

    @property
    def is_llm(self) -> bool:
        """True when this node asked for the proposal mode (enabled AND `llm`)."""
        return self.enabled and self.mode == LLM_MODE

    def mapping_for(self, sheet_title: str) -> Optional[Dict[str, Any]]:
        """The mapping for one sheet, or ``None`` (fall back to the deterministic rules).

        A mapping stored under ``""`` is the pinned `normalize_mapping` metadata: a human
        asserted it, so it applies to every sheet that does not have one of its own - and any
        sheet it does not fit simply fails its bounds check and falls back.
        """
        if not self.sheet_mappings:
            return None
        return self.sheet_mappings.get(sheet_title) or self.sheet_mappings.get("")

    @classmethod
    def from_metadata(cls, metadata: Optional[Dict[str, Any]]) -> "NormalizeOptions":
        """Read the option block from node metadata; blank or rubbish means "off"."""
        meta = metadata or {}

        raw_mode = str(meta.get("normalize") or "").strip().lower()
        if raw_mode == LLM_MODE:
            enabled, mode = True, LLM_MODE
        else:
            enabled = raw_mode in _ENABLED_VALUES
            mode = MODE_TABLE
        if raw_mode and not enabled:
            logger.warning(
                "DOCUMENT node metadata normalize=%r is not one of %s; parsing as-is",
                raw_mode, ", ".join(sorted(_ENABLED_VALUES | {LLM_MODE})))

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

        raw_columns = meta.get("normalize_columns")
        columns = _parse_columns(raw_columns)
        # The schema defaults to the node's output columns: a node that says only
        # `normalize: "llm"` means "map the five fields I compare". `normalize_schema` is the
        # separate dial for a foreign document - roles to look for, person NOT forced in.
        schema = _parse_columns(meta.get("normalize_schema"), force_person=False) \
            if meta.get("normalize_schema") else columns

        raw_confidence = meta.get("normalize_confidence_min")
        confidence_min = DEFAULT_CONFIDENCE_MIN
        if raw_confidence is not None and str(raw_confidence).strip() != "":
            try:
                parsed_confidence = float(str(raw_confidence).strip().replace(",", "."))
                confidence_min = min(max(parsed_confidence, 0.0), 1.0)
            except (TypeError, ValueError):
                logger.warning(
                    "DOCUMENT node metadata normalize_confidence_min=%r is not a number, "
                    "using %s", raw_confidence, DEFAULT_CONFIDENCE_MIN)

        pinned = _parse_mapping(meta.get("normalize_mapping"))

        return cls(enabled=enabled, header_scan_rows=scan_rows, columns=columns,
                   mode=mode, schema=schema, confidence_min=confidence_min,
                   sheet_mappings={"": pinned} if pinned else None)


def _parse_columns(raw: Any, force_person: bool = True) -> Tuple[str, ...]:
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
            "DOCUMENT node metadata contains unknown column/schema name(s) %s; known: %s",
            ", ".join(sorted(unknown)), ", ".join(_COLUMN_LABELS))
    if not requested:
        return DEFAULT_COLUMNS if force_person else ()
    # The person column identifies every row; a graph cannot ask for an anonymous report.
    # A schema is the opposite contract: it names what to LOOK for, and "person" may simply
    # not exist in the document.
    if force_person and "person" not in requested:
        requested.insert(0, "person")
    return tuple(requested)


def _parse_mapping(raw: Any) -> Optional[Dict[str, Any]]:
    """``normalize_mapping`` metadata -> the mapping dict, or ``None`` with a warning.

    This is a human pinning a proposal they already trust (a golden mapping for a known
    document), so it is parsed here and validated at apply time exactly like a model's -
    bounds first, type probes never: the point of pinning is to bypass the model entirely.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        logger.warning("DOCUMENT node metadata normalize_mapping is not JSON; ignored")
        return None
    if not isinstance(parsed, dict):
        logger.warning("DOCUMENT node metadata normalize_mapping is not a JSON object; ignored")
        return None
    return parsed


@dataclass
class NormalizedSheet:
    """One sheet after normalization: what to render, what it cost, what to warn about."""

    header: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    header_row: int = 0  # 1-based row the table's own column names were found on
    # The mapping this sheet was rendered from - the model's (or a pinned) JSON exactly as it
    # was applied, so a run can show WHAT decided the layout. ``None`` for the deterministic
    # path, whose decision is reproducible from the sheet itself.
    mapping: Optional[Dict[str, Any]] = None
    mapping_source: str = ""  # "llm" | "pinned" | "" (deterministic)

_HOURS = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*(?:h|ч|час(?:а|ов)?)?"
    r"(?:\s+(\d+)\s*(?:m|м|мин(?:ут(?:а|ы))?)?)?\s*$",
    re.IGNORECASE,
)


def normalize_timesheet(rows: Sequence[Sequence[str]],
                        options: NormalizeOptions,
                        sheet_title: str = "") -> Optional[NormalizedSheet]:
    """Turn raw sheet rows into a narrow, noise-free table, or ``None`` when it cannot.

    ``None`` means "the caller should parse this sheet as it always did": no header row was
    recognizable, or not a single data row survived validation. Falling back is the whole
    safety story here - a normalizer that misunderstands a format it has never seen is worse
    than no normalizer, and the caller (with its warning) says so out loud.

    Which header/columns decision feeds the shared applier depends on the mode:

    * ``table`` - the deterministic rules find the header and claim the columns.
    * ``llm`` - a mapping resolved by the CALLER (a model proposal that passed
      :mod:`app.graph_engine.table_mapping`, or a pinned ``normalize_mapping``) is applied.
      No mapping, or one that does not fit THIS sheet, falls back to the same deterministic
      rules; if they cannot read it either, the parser renders the sheet as-is with a warning.
    """
    if not rows:
        return None

    if not options.is_llm:
        return _normalize_with_rules(rows, options)

    mapping = options.mapping_for(sheet_title)
    if mapping is None:
        logger.info("normalize(llm): no accepted mapping for sheet %r; using the table rules",
                    sheet_title)
        return _fall_back_to_rules(rows, options, reason="no mapping was accepted")
    result = _normalize_with_mapping(rows, mapping, options,
                                     _mapping_source(options, sheet_title))
    if result is None:
        logger.info("normalize(llm): the mapping does not fit sheet %r; using the table rules",
                    sheet_title)
        return _fall_back_to_rules(rows, options, reason="the mapping did not fit the sheet")
    return result


def _mapping_source(options: NormalizeOptions, sheet_title: str) -> str:
    """``"llm"`` for a proposal resolved for this sheet, ``"pinned"`` for ``normalize_mapping``."""
    if options.sheet_mappings and sheet_title in options.sheet_mappings:
        return "llm"
    return "pinned"


def _fall_back_to_rules(rows: Sequence[Sequence[str]], options: NormalizeOptions,
                        reason: str) -> Optional[NormalizedSheet]:
    """Run the deterministic rules for a sheet LLM mode could not map, saying so out loud.

    The warning is deliberate: without it a graph would silently receive heuristic output
    while its metadata claims ``normalize: "llm"``, and nobody could tell the two apart in
    the report. The rules themselves are exactly what ``normalize: "table"`` runs.
    """
    result = _normalize_with_rules(rows, options)
    if result is not None:
        result.warnings.append(
            f"LLM normalization fell back to the deterministic table rules ({reason}).")
    return result


def _normalize_with_rules(rows: Sequence[Sequence[str]],
                          options: NormalizeOptions) -> Optional[NormalizedSheet]:
    """The historical, purely deterministic path: aliases find the header, values fit columns."""
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
    return _apply_fields(rows, header_index, fields, options)


def _normalize_with_mapping(rows: Sequence[Sequence[str]], mapping: Dict[str, Any],
                            options: NormalizeOptions, source: str = "llm"
                            ) -> Optional[NormalizedSheet]:
    """Apply a resolved mapping (model-proposed or pinned) to one sheet, or ``None``.

    Bounds only - the type probes already ran when a proposal was accepted, and a PINNED
    mapping is a human's assertion of exactly the same shape; re-probing it here would make
    ``normalize_mapping`` unusable for precisely the documents the probes are strict about.
    Everything below the bounds check is the shared applier, so a mapping cannot invent
    behaviour the deterministic path does not have.
    """
    if not isinstance(mapping, dict):
        return None
    try:
        header_index = int(mapping.get("header_row")) - 1  # 1-based in the JSON, 0-based here
        fields = {str(name): int(index)
                  for name, index in (mapping.get("columns") or {}).items()}
    except (TypeError, ValueError):
        logger.info("normalize(llm): mapping header_row/columns are not integers")
        return None
    if not fields:
        logger.info("normalize(llm): mapping claims no columns")
        return None
    if not (0 <= header_index < len(rows) - 1):
        # Below 0 is nonsense; the second half means there is no body under the header.
        logger.info("normalize(llm): mapping header_row %s is outside %d row(s)",
                    header_index + 1, len(rows))
        return None
    width = max((len(row) for row in rows), default=0)
    if any(not (0 <= index < width) for index in fields.values()):
        logger.info("normalize(llm): mapping names a column outside the %d-column sheet", width)
        return None
    return _apply_fields(rows, header_index, fields, options, mapping=mapping, source=source)


def _apply_fields(rows: Sequence[Sequence[str]], header_index: int,
                  fields: Dict[str, int], options: NormalizeOptions,
                  mapping: Optional[Dict[str, Any]] = None, source: str = ""
                  ) -> Optional[NormalizedSheet]:
    """Validate the body under a header/columns decision and render it. Shared by both modes.

    ``level`` 0 requires a person, a date and hours; each level exists only because the
    previous one kept ZERO rows (see :func:`_collect`). ``row_stop`` - a mapping's appendix
    markers - is applied BEFORE that ladder, because an appendix row is not a data row the
    ladder gets to relax about.
    """
    body = [list(row) for row in rows[header_index + 1:]]
    if not body:
        logger.info("normalize: header row %d has no data below it", header_index + 1)
        return None

    warnings: List[str] = []
    if mapping:
        row_stop = row_stop_markers(mapping)
        if row_stop:
            remaining = [row for row in body if not _matches_row_stop(row, row_stop)]
            stopped = len(body) - len(remaining)
            if stopped:
                warnings.append(
                    f"{stopped} appendix row(s) removed by the mapping's row_stop markers.")
                body = remaining
            if not body:
                logger.info("normalize(llm): row_stop removed every row below the header")
                return None

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
    if not output_fields:
        # A mapping that claims none of the columns this node asked for would render an empty
        # table, which is worse than the sheet as it was written.
        logger.info("normalize: the mapping covers none of the requested columns %s",
                    options.columns)
        return None
    counts["rows_kept"] = len(kept)

    return NormalizedSheet(
        header=[_COLUMN_LABELS[name] for name in output_fields],
        rows=[[values[name] for name in output_fields] for values in kept],
        stats=counts,
        warnings=warnings,
        header_row=header_index + 1,
        mapping=mapping if source else None,
        mapping_source=source,
    )


def row_stop_markers(mapping: Dict[str, Any]) -> Tuple[str, ...]:
    """The mapping's appendix markers, already bounded - anything else is dropped."""
    raw = mapping.get("row_stop")
    if not isinstance(raw, (list, tuple)):
        return ()
    markers: List[str] = []
    for item in raw[:8]:
        text = str(item).strip().lower()
        if len(text) >= 3:
            markers.append(text)
    return tuple(markers)


def _matches_row_stop(row: Sequence[str], markers: Sequence[str]) -> bool:
    """True when any cell of the row carries an appendix marker (``Всего billable: 416``)."""
    for cell in row:
        text = _norm_cell(cell)
        if text and any(marker in text for marker in markers):
            return True
    return False


# --- The sheet's fingerprint: everything a mapping proposal is allowed to see -------------
#
# A proposal is deliberately NOT given the document: the head (where a title and the real
# header live) and the tail (where appendix blocks live) are what decides the layout, and a
# bounded view keeps the call cheap on every run. Rows are numbered from 1 exactly as the
# model is shown them - `header_row` comes back in that same numbering.
FINGERPRINT_HEAD_ROWS = 12
FINGERPRINT_TAIL_ROWS = 5
FINGERPRINT_MAX_COLUMNS = 40
FINGERPRINT_SAMPLE_CHARS = 60
FINGERPRINT_RATIO_ROWS = 500


@dataclass(frozen=True)
class ColumnProfile:
    """One column as a proposal is shown it: where it is, what it holds, what it parses as."""

    index: int                      # 0-based, left to right - the number the model returns
    filled: float                   # non-empty share of the probed body
    samples: Tuple[str, ...]        # first values, trimmed: enough to recognise the content
    person_ratio: float             # all ratios are over NON-EMPTY cells: a merged block
    date_ratio: float               # inherits its value, so a blank is not a miss
    hours_ratio: float


@dataclass(frozen=True)
class SheetFingerprint:
    """A sheet reduced to the facts a mapping is decided on - never the cells themselves."""

    width: int
    rows_total: int
    columns: Tuple[ColumnProfile, ...]
    head: Tuple[Tuple[int, Tuple[str, ...]], ...]   # (1-based row number, cells)
    tail: Tuple[Tuple[int, Tuple[str, ...]], ...]
    # 1-based row the deterministic rules already suspect is the header, 0 for "they found
    # nothing". The proposal is told: confirming a guess is a different (and safer) answer
    # than inventing one, and the difference is visible in the log.
    suspected_header_row: int = 0


def column_ratio(rows: Sequence[Sequence[str]], index: int, kind: str,
                 offset: int = 0, sample: int = FINGERPRINT_RATIO_ROWS) -> Tuple[int, int]:
    """``(valid, seen)`` over one column's non-empty cells below ``offset``.

    Shares its predicates with the row validation below - ``_looks_like_person``,
    ``_looks_like_date``, ``_parse_hours`` - so a column the applier would accept is the same
    column the probe calls a match. There is deliberately no second opinion about what a
    person or a date looks like: disagreement between the validator and the applier would be
    a mapping nobody could predict.
    """
    if kind == "person":
        def matches(text: str) -> bool:
            return _looks_like_person(text)
    elif kind == "date":
        def matches(text: str) -> bool:
            return _looks_like_date(text)
    elif kind == "hours":
        def matches(text: str) -> bool:
            return _parse_hours(text) is not None
    else:
        raise ValueError(f"unknown column probe {kind!r}")

    seen = valid = 0
    for row in rows[offset: offset + sample]:
        value = _cell(row, index)
        if not value:
            continue
        seen += 1
        if matches(value):
            valid += 1
    return valid, seen


def describe_sheet(rows: Sequence[Sequence[str]],
                   options: Optional[NormalizeOptions] = None,
                   head_rows: int = FINGERPRINT_HEAD_ROWS,
                   tail_rows: int = FINGERPRINT_TAIL_ROWS) -> SheetFingerprint:
    """Reduce a sheet to the facts a mapping proposal is decided on.

    The ratios are measured below whatever row the deterministic rules already suspect is
    the header (none at all when they recognise nothing - which is exactly the case that
    makes a proposal necessary); a title row above it would otherwise drag every ratio down.
    """
    rows = list(rows or ())
    if not rows:
        return SheetFingerprint(width=0, rows_total=0, columns=(), head=(), tail=())

    probe_from = 0
    header = _find_header(rows, options or NormalizeOptions())
    if header is not None:
        probe_from = header + 1
    body = rows[probe_from:]

    width = max(len(row) for row in rows)
    columns: List[ColumnProfile] = []
    for index in range(min(width, FINGERPRINT_MAX_COLUMNS)):
        filled = valid = 0
        samples: List[str] = []
        for row in body:
            value = _cell(row, index)
            if not value:
                continue
            filled += 1
            if len(samples) < 3:
                samples.append(value[:FINGERPRINT_SAMPLE_CHARS])
        person_valid, person_seen = column_ratio(rows, index, "person", probe_from)
        date_valid, date_seen = column_ratio(rows, index, "date", probe_from)
        hours_valid, hours_seen = column_ratio(rows, index, "hours", probe_from)
        probe_rows = len(body) or 1
        columns.append(ColumnProfile(
            index=index,
            filled=filled / probe_rows,
            samples=tuple(samples),
            person_ratio=person_valid / person_seen if person_seen else 0.0,
            date_ratio=date_valid / date_seen if date_seen else 0.0,
            hours_ratio=hours_valid / hours_seen if hours_seen else 0.0,
        ))

    def numbered(indexes: Sequence[int]) -> Tuple[Tuple[int, Tuple[str, ...]], ...]:
        return tuple(
            (index + 1, tuple(str(cell)[:FINGERPRINT_SAMPLE_CHARS] for cell in rows[index]))
            for index in indexes)

    last = len(rows)
    head_indexes = list(range(min(head_rows, last)))
    tail_indexes = list(range(max(head_rows, last - tail_rows), last))
    if tail_indexes and tail_indexes[0] < head_indexes[-1] + 1:
        tail_indexes = [index for index in tail_indexes if index > head_indexes[-1]]

    return SheetFingerprint(
        width=width,
        rows_total=last,
        columns=tuple(columns),
        head=numbered(head_indexes),
        tail=numbered(tail_indexes),
        suspected_header_row=(header + 1) if header is not None else 0,
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

        if "person" not in fields:
            # A document with no person column (the `llm` mode mapping a foreign file): no
            # row may be judged by a rule about a column it does not have - only the roles
            # the mapping actually claims filter the body here. The deterministic path never
            # reaches this: it refuses a header row without one.
            person = ""
        else:
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


# Public alias: the mapping validator asks the SAME question about the header cell that the
# row validation asks about a data cell. Two implementations of "is this a person" would let
# a proposal pass the validator and then have every row dropped at apply time - a mapping
# nobody could predict.
looks_like_person = _looks_like_person


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

