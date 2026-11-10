"""The mapping proposal: what a model may decide about a document, and how little it is believed.

The split with :mod:`app.graph_engine.table_normalize` is the whole point of this module. A
document no header alias recognizes cannot be read by rules, but handing a spreadsheet to a
model and asking it to "normalize this and that" hands it the power to rewrite facts - which
is exactly how a report gets an employee who never worked an hour. So the model is shown a
FINGERPRINT (head, tail, per-column profiles - never the whole sheet) and asked for a mapping:
which row is the header, which column plays which role, where the appendix starts.

Code then decides everything else:

* :func:`validate_mapping` checks the proposal against the GRID - bounds, collisions, the
  per-column type probes that reuse the applier's own predicates - and rejects it whole
  rather than repairing it. A rejected proposal is not an error: the caller falls back to the
  deterministic rules, and a sheet they cannot read either is rendered as-is.
* :func:`app.graph_engine.table_normalize.normalize_timesheet` applies whatever survived, with
  the same row validation the deterministic path uses. The proposal can point; it cannot push.

Nothing here calls a model by itself: the caller passes in its own ``generate`` (the node's
``_generate``, with its retries, its refusal detection, its billing), so a proposal costs
exactly what any other model call costs the run and reports it the same way.
"""

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from app.graph_engine.graph_utils import JsonGenerationError, extract_json_from_response
from app.graph_engine.table_normalize import (
    KNOWN_ROLES,
    TYPE_PROBE_FLOOR,
    TYPE_PROBE_MIN_SAMPLES,
    TYPE_PROBE_WARN_AT,
    NormalizeOptions,
    SheetFingerprint,
    column_ratio,
    describe_sheet,
    looks_like_person,
    row_stop_markers,
)
from app.prompt_loader import _read_prompt

logger = logging.getLogger(__name__)

PROMPT_NAME = "normalize_mapper"

# Roles whose meaning has a cheap, shareable test - the same one the row validation runs.
_PROBE_ROLES: Tuple[str, ...] = ("person", "date", "hours")

# An answer without a usable confidence is not optimistically accepted: the node set a floor
# precisely because a model that does not state its doubt has not thought about it.
DEFAULT_CONFIDENCE = 0.5

# What a proposal may send back about appendix rows (see table_normalize.row_stop_markers).
MAX_ROW_STOP = 8

# The sheet title in the user line keeps a multi-sheet proposal unambiguous; it is also the
# key the resolved mapping is stored under.
_USER_LINE = "Sheet {title!r}: propose the mapping now. Output only the JSON object."


# --- What comes back ---------------------------------------------------------------------

@dataclass(frozen=True)
class MappingResult:
    """A proposal that survived validation - the only shape the applier is ever handed."""

    header_row: int                       # 1-based, as printed to the model
    columns: Dict[str, int]               # role -> 0-based column index
    row_stop: Tuple[str, ...]             # appendix markers, already bounded
    confidence: float
    unmapped: Tuple[str, ...] = ()        # schema roles this document has no column for
    warnings: Tuple[str, ...] = ()        # survived, but worth telling the operator

    def as_mapping(self) -> Dict[str, Any]:
        """The JSON ``NormalizeOptions.sheet_mappings`` applies - and ``norm_meta`` shows."""
        payload: Dict[str, Any] = {
            "header_row": self.header_row,
            "columns": dict(self.columns),
            "row_stop": list(self.row_stop),
            "confidence": round(self.confidence, 3),
        }
        if self.unmapped:
            payload["unmapped"] = list(self.unmapped)
        return payload


@dataclass(frozen=True)
class ValidationOutcome:
    """``mapping`` when accepted; ``reason`` (plus warnings) when the proposal was refused."""

    mapping: Optional[MappingResult] = None
    warnings: Tuple[str, ...] = ()
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.mapping is not None


@dataclass(frozen=True)
class MappingOutcome:
    """One resolved (or refused) proposal, with what the model call cost."""

    mapping: Optional[MappingResult]
    tokens: int = 0
    billing: str = ""
    warnings: Tuple[str, ...] = ()


GenerateFn = Callable[[str, str], Awaitable[Tuple[Any, str]]]


async def resolve_mapping(rows: Sequence[Sequence[str]], options: NormalizeOptions,
                          generate: GenerateFn, sheet_title: str = "") -> MappingOutcome:
    """Ask for one sheet's mapping and believe only what survives :func:`validate_mapping`.

    ``generate`` is the DOCUMENT node's own ``_generate``: retries, transient-error backoff,
    refusal detection, personal-key billing and the ``(response, billing_owner)`` contract all
    come with it, so a proposal is an ordinary node model call and reports like one.

    A REFUSAL is deliberately not caught here - it is raised to the node, which chooses
    between failing and falling back, because only the node knows what it must report. An
    answer that is merely wrong (prose instead of JSON, out-of-range, garbage) arrives here
    as text and is rejected with a reason.
    """
    if not rows:
        return MappingOutcome(mapping=None, warnings=("the sheet has no rows to map",))

    system, user = build_prompts(rows, options, sheet_title)
    response, billing = await generate(user, system)
    text = str(getattr(response, "content", response))
    usage = getattr(response, "usage", None)
    tokens = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0

    try:
        raw = extract_json_from_response(text)
    except JsonGenerationError as exc:
        logger.warning("normalize(llm) sheet %r: proposal is not JSON: %s", sheet_title, exc)
        return MappingOutcome(mapping=None, tokens=tokens, billing=billing,
                              warnings=(f"the proposal was not a JSON object ({exc})",))

    outcome = validate_mapping(raw, rows, options)
    if not outcome.accepted:
        logger.info("normalize(llm) sheet %r: proposal rejected - %s",
                    sheet_title, outcome.reason)
        return MappingOutcome(mapping=None, tokens=tokens, billing=billing,
                              warnings=tuple(outcome.warnings) + (outcome.reason,))
    logger.info("normalize(llm) sheet %r: mapping accepted - header_row=%d columns=%s "
                "confidence=%.2f", sheet_title, outcome.mapping.header_row,
                outcome.mapping.columns, outcome.mapping.confidence)
    return MappingOutcome(mapping=outcome.mapping, tokens=tokens, billing=billing,
                          warnings=outcome.warnings)


def build_prompts(rows: Sequence[Sequence[str]], options: NormalizeOptions,
                  sheet_title: str = "") -> Tuple[str, str]:
    """``(system, user)`` for one proposal - the rules with the roles, the sheet as payload.

    The fingerprint travels in the USER message for the same reason the router puts state
    there: the retry path in ``_generate`` shrinks the user message, while instructions and
    contract must survive a retry intact (the payload is small enough to be re-sent).
    """
    fingerprint = describe_sheet(rows, options)
    system = _read_prompt(PROMPT_NAME).format(roles=", ".join(KNOWN_ROLES))
    return system, _render_fingerprint(fingerprint, sheet_title)


def _render_fingerprint(fp: SheetFingerprint, sheet_title: str = "") -> str:
    """The payload: everything the layout decision rests on, nothing else."""
    lines: List[str] = []
    if sheet_title:
        lines.append(f"Sheet: {sheet_title}")
    lines.append(f"{fp.rows_total} row(s) x {fp.width} column(s).")
    if fp.suspected_header_row:
        lines.append(
            f"The deterministic rules already suspect the header is row {fp.suspected_header_row} "
            "- confirm it or correct it.")
    else:
        lines.append("The deterministic rules recognised no header row - it is yours to find.")
    lines.append("")
    lines.append("Columns (index is 0-based; ratios are over that column's non-empty cells):")
    for column in fp.columns:
        samples = " | ".join(column.samples) if column.samples else "(empty)"
        lines.append(
            f"  col {column.index}: filled {_pct(column.filled)}, "
            f"person {_pct(column.person_ratio)}, date {_pct(column.date_ratio)}, "
            f"hours {_pct(column.hours_ratio)}; samples: {samples}")
    if fp.width > len(fp.columns):
        lines.append(f"  ... {fp.width - len(fp.columns)} more column(s) not profiled")
    lines.append("")
    lines.append("Rows, numbered exactly as you must return them (1-based):")
    for number, cells in fp.head:
        lines.append(f"  row {number}: {' | '.join(cells)}")
    if fp.tail:
        lines.append("  ...")
        for number, cells in fp.tail:
            lines.append(f"  row {number}: {' | '.join(cells)}")
    return "\n".join(lines)


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def validate_mapping(raw: Any, rows: Sequence[Sequence[str]],
                     options: NormalizeOptions) -> ValidationOutcome:
    """Accept a proposal whole or reject it whole - never repair it.

    Rejecting is the cheap, safe outcome: the caller falls back to the deterministic rules
    and, if they cannot read the sheet either, to the sheet as written. A half-applied
    mapping would be worse than either, so every check answers YES or NO for the proposal as
    a whole, with a reason a log line and ``norm_meta`` can both show.

    Checks in order: shape and bounds (a proposal may not point outside the grid), one role
    per column (a collision means the model did not look), the off-by-one guard (a person
    NAME in the mapped person column means ``header_row`` points at data), the type probes
    (the column must parse as the role it was given), the node's own confidence floor, and
    finally whether the mapping serves any column this node actually asked for.
    """
    rows = list(rows)
    if not rows:
        return ValidationOutcome(reason="the sheet has no rows")
    if not isinstance(raw, dict):
        return ValidationOutcome(
            reason=f"the proposal is not a JSON object: {type(raw).__name__}")

    warnings: List[str] = []

    # 1. The header row: a real row with a body under it.
    try:
        header_row = int(raw.get("header_row"))
    except (TypeError, ValueError):
        return ValidationOutcome(
            reason=f"header_row {raw.get('header_row')!r} is not a row number")
    if header_row == 0 and not raw.get("columns"):
        return ValidationOutcome(reason="the model says this sheet is not a table")
    if not (1 <= header_row <= len(rows) - 1):
        return ValidationOutcome(
            reason=f"header_row {header_row} is outside the {len(rows)} row(s) of the sheet")

    # 2. The columns: known roles, in-range indexes, one column per role.
    raw_columns = raw.get("columns")
    if not isinstance(raw_columns, dict) or not raw_columns:
        return ValidationOutcome(reason="columns is not a non-empty object")
    width = max(len(row) for row in rows)
    columns: Dict[str, int] = {}
    for name, value in raw_columns.items():
        role = str(name).strip().lower()
        if role not in KNOWN_ROLES:
            warnings.append(f"unknown role {name!r} ignored (known: {', '.join(KNOWN_ROLES)})")
            continue
        try:
            index = int(value)
        except (TypeError, ValueError):
            return ValidationOutcome(reason=f"column for {role} is not an index: {value!r}")
        if not (0 <= index < width):
            return ValidationOutcome(
                reason=f"column {index} for {role} is outside the {width}-column sheet")
        if index in columns.values():
            taken = next(other for other, col in columns.items() if col == index)
            return ValidationOutcome(reason=f"{role} and {taken} both claim column {index}")
        columns[role] = index
    if not columns:
        return ValidationOutcome(reason="the proposal named no role this engine knows")

    # 3. Off-by-one guard: the cell IN the person column ON the header row must be a label.
    #    A person's name there means the row the model chose is already DATA.
    if "person" in columns:
        header_row_cells = rows[header_row - 1]
        person_index = columns["person"]
        header_cell = str(header_row_cells[person_index]).strip() \
            if person_index < len(header_row_cells) else ""
        if header_cell and looks_like_person(header_cell):
            return ValidationOutcome(
                reason=f"row {header_row} holds a name in the person column, so it is a data "
                       f"row - the header must be elsewhere")

    # 4. Type probes: the column must parse as the role it was given. Thresholds and
    #    predicates both come from table_normalize - see column_ratio.
    for role in _PROBE_ROLES:
        if role not in columns:
            continue
        valid, seen = column_ratio(rows, columns[role], role, header_row)
        if seen < TYPE_PROBE_MIN_SAMPLES:
            if seen == 0:
                warnings.append(f"column {columns[role]} ({role}) is empty below the header")
            continue
        ratio = valid / seen
        if ratio < TYPE_PROBE_FLOOR:
            return ValidationOutcome(
                reason=f"column {columns[role]} parses as {role} in only {valid} of {seen} cells")
        if ratio < TYPE_PROBE_WARN_AT:
            warnings.append(
                f"column {columns[role]} parses as {role} in only {valid} of {seen} cells")


    # 5. The node's own confidence floor.
    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError):
        confidence = DEFAULT_CONFIDENCE
        warnings.append("the proposal stated no usable confidence; treated as 0.5")
    confidence = min(max(confidence, 0.0), 1.0)
    if confidence < options.confidence_min:
        return ValidationOutcome(
            reason=f"confidence {confidence:.2f} is below the {options.confidence_min:.2f} "
                   f"this node requires")

    # 6. Appendix markers - bounded here, matched later by table_normalize.
    raw_stop = raw.get("row_stop")
    if raw_stop is not None and not isinstance(raw_stop, (list, tuple)):
        warnings.append("row_stop was not a list and was ignored")
        raw_stop = ()
    if isinstance(raw_stop, (list, tuple)) and len(raw_stop) > MAX_ROW_STOP:
        warnings.append(f"row_stop was cut to the first {MAX_ROW_STOP} marker(s)")
    row_stop = row_stop_markers({"row_stop": raw_stop})

    # 7. It must serve this node: a mapping that claims none of the columns the graph asked
    #    for would render an empty table, which is worse than the sheet as it was written.
    if not (set(columns) & set(options.columns)):
        return ValidationOutcome(
            reason=f"the proposal covers none of the columns this node asks for "
                   f"({', '.join(options.columns)})")

    # 8. Honest omissions, reported rather than punished.
    unmapped = tuple(role for role in options.schema if role not in columns)
    if unmapped:
        warnings.append(f"no column found for: {', '.join(unmapped)}")

    return ValidationOutcome(
        mapping=MappingResult(
            header_row=header_row,
            columns=columns,
            row_stop=row_stop,
            confidence=confidence,
            unmapped=unmapped,
            warnings=tuple(warnings),
        ),
        warnings=tuple(warnings),
    )

