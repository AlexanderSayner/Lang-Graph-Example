import json
import logging
import operator
import re
from typing import TypedDict, Dict, Any, Annotated, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# The one context key that names an attached document BY DEFAULT. A graph that needs several
# documents in one run gives each DOCUMENT node a key of its own (see DOCUMENT_REF_KEY below
# and `document_variables_of`); this remains the fallback for a node that names none, and the
# single key the Java facades gate by default. It is a run-level fact (a graph definition is
# persisted and shared, a file belongs to a single run), so it travels in the run's context
# rather than in node metadata; the metadata only names the KEY a DOCUMENT node should read.
# Single source of truth for the engine, the node handler and the "nobody will read this" guard
# in the servicer.
DOCUMENT_REF_KEY = "document_ref"

# Where a DOCUMENT node writes what it extracted, and the metadata that describes it. Both are
# per-node metadata (`output_variable` / `meta_variable`) so one run can carry several files
# without the second extraction overwriting the first - the failure behind "I uploaded two
# documents and the agent only saw one". The defaults keep the original single-document
# contract: a node that declares nothing writes exactly what it always wrote.
DOCUMENT_OUTPUT_VARIABLE = "document_text"
DOCUMENT_META_VARIABLE = "document_meta"

# The shape of a document id minted by DocumentService.newId: "<owner_kind>-<owner_hash>-<32
# chars of a-z0-9>". Used by the "nothing will read this" guard to tell a document handle in
# the run context from an ordinary business value such as {"tenant": "orchestra"}, so a graph
# that happens to pass context keys of its own is never warned about. Mirrored on the Java
# side by DocumentService.looksLikeDocumentId - keep the two in step.
DOCUMENT_ID_PATTERN = re.compile(r"^(ui|b2b)-[0-9a-f]{1,8}-[a-z0-9]{32}$")


def looks_like_document_id(value: Any) -> bool:
    """True when a context value is shaped like a document id this service would have minted."""
    return isinstance(value, str) and bool(DOCUMENT_ID_PATTERN.match(value.strip()))


def document_variables_of(metadata: Optional[Dict[str, Any]]) -> Tuple[str, str, str, str]:
    """Resolve one DOCUMENT node's slot: ``(ref_key, output_variable, meta_variable, label)``.

    Every part is metadata so a graph definition stays declarative and the same definition can
    be shared across runs while each run supplies its own files. Blank or missing metadata
    means the default, which is what keeps a graph built before this existed working
    unchanged: ``context[document_ref]`` -> ``variables.document_text`` +
    ``variables.document_meta``.

    ``meta_variable`` defaults to ``document_meta`` while the output is the default
    ``document_text``, and to ``<output_variable>_meta`` once the output is renamed - so the
    metadata always sits next to the text it describes, and the historical name survives for
    the historical layout.
    """
    meta = metadata or {}

    def _name(key: str, fallback: str) -> str:
        return str(meta.get(key) or "").strip() or fallback

    ref_key = _name("document_ref_key", DOCUMENT_REF_KEY)
    output_variable = _name("output_variable", DOCUMENT_OUTPUT_VARIABLE)
    if "meta_variable" in meta or output_variable == DOCUMENT_OUTPUT_VARIABLE:
        meta_variable = _name("meta_variable", DOCUMENT_META_VARIABLE)
    else:
        meta_variable = _name("meta_variable", f"{output_variable}_meta")
    label = str(meta.get("label") or "").strip()
    return ref_key, output_variable, meta_variable, label


# --- Reducer for Variables ---
# This function ensures that we MERGE new variables with old ones,
# preventing the "amnesia" issue where the LLM drops previous values.
def merge_dicts(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    if left is None: left = {}
    if right is None: right = {}
    return {**left, **right}


# --- State Definition ---
class GraphState(TypedDict):
    input: str
    context: Dict[str, Any]
    timestamp: float
    last_node: str
    output: str
    # A node's own failure ("missing_context:doc1_ref", "llm_refusal:yandex", ...). This
    # MUST be declared: langgraph silently drops keys that are not part of the state
    # schema, so before it was declared here the key never survived into the update
    # event and the NODE_END frame's `error_message` was always empty in real runs
    # (only the fake-graph tests ever saw it). Not rendered into prompts -
    # format_state_context reads variables/history/input only.
    error: str
    # Automatically append new history items
    variables: Annotated[Dict[str, Any], merge_dicts]
    history: Annotated[List[Dict[str, Any]], operator.add]
    total_tokens: Annotated[int, operator.add]
    # Who paid for the model call this node made: "user" | "service" | "" (no model call).
    # Plain metadata, never a credential, and deliberately NOT rendered into prompts -
    # format_state_context only reads variables/history/input. It exists so the engine can
    # report a subsidised run ("billing=service") per node instead of keeping it silent.
    llm_billing: str


class JsonGenerationError(Exception):
    pass


def extract_json_from_response(response_text: str) -> dict:
    """
        Extracts and parses JSON from LLM output.
        Raises JsonGenerationError if JSON is missing or invalid.
    """
    # Robust Markdown Stripping
    # Remove ```json and ``` wrappers explicitly
    clean_response = response_text.strip()
    if clean_response.startswith("```"):
        # Remove first line (```json) and last line (```)
        lines = clean_response.splitlines()
        if len(lines) > 2:
            clean_response = "\n".join(lines[1:-1])

    # Looks for text between { and }
    json_match = re.search(r'\{.*}', clean_response, re.DOTALL)

    if not json_match:
        raise JsonGenerationError(f"No JSON object found in response: {response_text[:100]}...")

    json_str = json_match.group(0)
    try:
        raw_data = json.loads(json_str)
        parsed_data = {str(k): v for k, v in raw_data.items()}
        return parsed_data
    except json.JSONDecodeError as e:
        raise JsonGenerationError(f"Invalid JSON syntax: {e}")


# --- Condition evaluation ---
# Conditions are a tiny DSL ("key == value") typed by hand in the builder or suggested by the
# copilot, while the value they are compared against comes out of the LLM's parsed JSON. Keeping
# the right-hand side a raw string used to break boolean edges: str(True) is "True" but the
# condition says "true", so no branch ever matched and the run ended at the END sentinel without
# executing an END node. Literals are therefore typed at parse time and compared through
# values_match().

# Signed, so a condition can be written the way it reads: x == +3 and x == -3 both parse to numbers.
_INT_LITERAL = re.compile(r"[+-]?\d+")
_FLOAT_LITERAL = re.compile(r"[+-]?\d+\.\d+")

# Bare boolean spellings the PARSER turns into a real bool: `output == true` has to route on a JSON
# boolean. 1 and 0 are deliberately NOT here - `x == 1` is the number 1, and turning the literal
# into True would make inspecting a parsed condition lie about what was written. The numeric/boolean
# ambiguity is resolved at comparison time instead, by coerce_bool.
_BOOL_LITERALS = {"true": True, "yes": True, "false": False, "no": False}

# What coerce_bool accepts at RUNTIME: the literal spellings plus 1/0, because a model may answer
# with a JSON boolean, a number, or the same value as a string.
_BOOL_VALUES = {**_BOOL_LITERALS, "1": True, "0": False}


def parse_literal(val: str):
    """Turns the right-hand side of a condition into a typed literal.

    Quoted text becomes a string, bare true/false/yes/no become bool, null/none become None and
    numeric text becomes int/float - 1 and 0 included, because `x == 1` is a number. Anything else
    is returned stripped and unchanged.

    Quoting controls PARSING, not matching: values_match() coerces at comparison time, so
    `flag == 'true'` still matches a boolean True and `x == '1'` still matches 1 or True. That is
    deliberate - a condition must not have to guess how the model chose to spell a value.
    """
    text = val.strip()
    if (text.startswith("'") and text.endswith("'")) or \
            (text.startswith('"') and text.endswith('"')):
        return text[1:-1]

    lowered = text.lower()
    if lowered in _BOOL_LITERALS:
        return _BOOL_LITERALS[lowered]
    if lowered in ("null", "none"):
        return None
    if _INT_LITERAL.fullmatch(text):
        return int(text)
    if _FLOAT_LITERAL.fullmatch(text):
        return float(text)
    return text


def parse_condition(condition_str: str) -> tuple:
    """
        Safely parses a condition string like "key == 'value'".
        Returns (key, expected_value), where expected_value is a typed literal (parse_literal):
        "output == true" yields (output, True), "intent == 'billing'" yields (intent, "billing").
    """
    if not condition_str or "==" not in condition_str:
        return None, None

    key_part, val_part = condition_str.split("==", 1)

    # Clean key: strip whitespace
    key = key_part.strip()

    # Typed literal: "output == true" -> True, "count == 3" -> 3, quoted text stays a string.
    return key, parse_literal(val_part)


def coerce_bool(value):
    """True/False when a value can be read as a boolean, otherwise None.

    Accepts real booleans, 0/1 numbers and the textual spellings models tend to produce
    ("true", "True", "yes", "1"), so a router condition matches whether the LLM returned a JSON
    boolean or the same value quoted as a string.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in _BOOL_VALUES:
        return _BOOL_VALUES[text]
    return None


def values_match(expected, actual, present: bool = True) -> bool:
    """Compares a condition's expected literal against the value currently held for the key.

    `present` is False when neither the state variables nor the state itself define the key: a
    missing variable must never satisfy a condition (including `key == null`), otherwise a branch
    would fire on a value the graph never computed.
    """
    if not present:
        return False

    # Booleans must be checked before the numeric branch: bool is a subclass of int in Python,
    # so True would otherwise be absorbed by `isinstance(True, (int, float))`.
    if isinstance(expected, bool) or isinstance(actual, bool):
        expected_bool, actual_bool = coerce_bool(expected), coerce_bool(actual)
        return expected_bool is not None and actual_bool is not None and expected_bool == actual_bool

    if expected is None or actual is None:
        return expected is None and actual is None

    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return expected == actual

    # Text comparison, case-insensitive: LLM-produced enums and hand-typed conditions differ in
    # casing far more often than they genuinely differ as values.
    return str(actual).strip().casefold() == str(expected).strip().casefold()


def parse_conditions(conditions, node_id: str):
    """Parses one node's outgoing conditions ONCE, at build time.

    `conditions` is the list of (target, condition_str) pairs taken from the outgoing conditional
    edges; the result is a list of (target, key, expected_literal) triples that the router compares
    directly on every pass instead of re-splitting strings. A condition that cannot be parsed can
    never route at all, so it fails the build here - naming the node - rather than being skipped on
    every evaluation, where the only trace was a warning nobody set a log level for.

    A key that is not a simple identifier still parses (data keys are arbitrary names); only a
    missing `key == value` shape is fatal.
    """
    parsed = []
    for target, condition_str in conditions:
        key, expected = parse_condition(condition_str)
        if not key:
            raise ValueError(
                f"Malformed condition {condition_str!r} on node {node_id!r}: expected "
                f"'key == value' (e.g. output == true)"
            )
        parsed.append((target, key, expected))
    return parsed


def resolve_route(variables: Dict[str, Any], parsed_conditions, extra_state: Dict[str, Any]):
    """First target whose condition matches, or None when nothing matches.

    `parsed_conditions` is the (target, key, expected_literal) list parse_conditions() produced at
    build time, `variables` is the state's variable dict, `extra_state` the full graph state. The
    state is consulted ONLY for a key the variables do not define at all, so a condition on a plain
    state key (e.g. `input`) still routes while an explicit null in the variables stays null.

    `extra_state` is deliberately not optional: the servicer always passes the whole state, so a
    caller (or a test) that omits it would exercise a lookup production never performs - which is
    exactly how the masked-null bug in the first review slipped through.
    """
    variables = variables or {}
    extra_state = extra_state or {}

    for target, key, expected in parsed_conditions:
        if key in variables:
            # An explicit null must stay null: falling back to the same-named state field (output,
            # input, last_node are all plain state keys) would mask it and make `key == null`
            # unsatisfiable at runtime.
            actual, present = variables[key], True
        else:
            actual, present = extra_state.get(key), key in extra_state

        match = values_match(expected, actual, present)
        # %s formatting rather than an f-string: this runs on every evaluation of every outgoing
        # edge, and the message must not be built when nobody listens at DEBUG.
        logger.debug("Route check: Key=%r, Expected=%r, Actual=%r, Present=%s, Match=%s -> %s",
                     key, expected, actual, present, match, target)
        if match:
            return target

    return None


def _renderable_variables(variables: Dict[str, Any]) -> Dict[str, Any]:
    """The variables a model may see, with what it cannot act on removed.

    Every ``*_meta`` map a DOCUMENT node wrote records which reference produced its text. The
    filename, the label and the parse statistics are useful context; ``document_ref`` is an
    opaque handle - a model can do nothing but quote it back, and doing so is exactly how a
    run ends with an answer about being unable to open a file. The state keeps the handle (the
    state inspector shows which reference produced the text, and a condition may still want
    it); only the rendered copy drops it.

    The scan is over every ``*_meta`` variable rather than the one historical name, because a
    graph may give each of its DOCUMENT nodes a meta variable of its own (see
    ``document_variables_of``) - scrubbing only ``document_meta`` would leak the handles of
    every slot that was renamed. The name suffix is required so an unrelated variable the graph
    happens to call ``*_meta`` is left alone unless it really holds a handle.

    Returns the ORIGINAL dict when there is nothing to remove, so the common case costs
    neither a copy nor a lookup beyond one key probe.
    """
    rendered = None
    for name, value in variables.items():
        if not (name.endswith("_meta") or name == DOCUMENT_META_VARIABLE):
            continue
        if not (isinstance(value, dict) and DOCUMENT_REF_KEY in value):
            continue
        if rendered is None:
            rendered = dict(variables)
        rendered[name] = {k: v for k, v in value.items() if k != DOCUMENT_REF_KEY}
    return rendered if rendered is not None else variables


def _is_block_text(value: Any) -> bool:
    """A string the model has to READ as text rather than parse as a value.

    `json.dumps` escapes every newline, so a document extraction arrives as one enormous line
    of ``\\n`` - and with `ensure_ascii` (the default) every non-ASCII character becomes a
    ``\\uXXXX`` sequence, turning «Новиков» into ``\\u041d\\u043e\\u0432\\u0438\\u043a\\u043e\\u0432``.
    Reading a ragged block layout - where a person's name sits on its own row above their
    rows and must be carried down - out of such a line is not a reasonable ask, and the
    symptom is exactly what this feature reports: rows attributed to the wrong employee.
    Anything with a newline is therefore rendered raw instead of as a JSON string.
    """
    return isinstance(value, str) and "\n" in value


def format_state_context(state: GraphState, include_history: bool = True,
                          include_http_result: bool = True,
                          only_variables: Optional[Set[str]] = None) -> str:
    """Prepares the conversation history and variables for the LLM.

    `include_history` / `include_http_result` let a node ask for a smaller context: a very long
    prompt (full API payload + full history) makes the model noticeably more likely to refuse,
    and a node usually needs only part of it. Values that are switched off are simply not shown.

    `only_variables` narrows this further: when a node declares the variables it consumes, only
    those are rendered. A node in a pipeline that works from the previous node's normalised
    table otherwise still receives the two raw documents it was told not to use - and, being
    right there in the prompt, they win: the model re-parses them and the sums come out wrong.
    """
    # We MUST show the LLM the current variables so it knows what is already filled.
    current_vars = state.get("variables", {}) or {}
    if not include_http_result:
        current_vars = {k: v for k, v in current_vars.items() if k != "http_result"}
    if only_variables is not None:
        current_vars = {k: v for k, v in current_vars.items() if k in only_variables}
    current_vars = _renderable_variables(current_vars)

    # Scalars stay in the JSON dump - compact, and the types stay unambiguous. Multi-line text
    # (a document extraction, an HTML payload) is emitted raw, because escaping it is what makes
    # it unreadable; see _is_block_text.
    scalars = {k: v for k, v in current_vars.items() if not _is_block_text(v)}
    blocks = {k: v for k, v in current_vars.items() if _is_block_text(v)}
    # ensure_ascii=False: the transport is UTF-8 end to end (gRPC, Postgres, HTTP), so there is
    # nothing to escape, and escaping it only made the prompt bigger and unreadable.
    vars_json_str = json.dumps(scalars, indent=2, ensure_ascii=False)
    blocks_str = ""
    if blocks:
        blocks_str = "\n\n" + "\n\n".join(
            f"--- {name} ---\n{text}\n--- end {name} ---" for name, text in blocks.items()
        )

    # Dynamic History (Conversation memory)
    # This is CRITICAL. Without this, the LLM doesn't know what happened before.
    history = (state.get("history") or []) if include_history else []
    history_str = ""
    if history:
        # Format: "Node Name: Output"
        history_str = "Conversation History:\n" + "\n".join(
            [f"- {h.get('node')}: {h.get('output')}" for h in history if h.get("output")]
        )

    user_input = state.get("input") or ""

    # We create a clear "Data Block" so the LLM cannot hallucinate state.
    data_block = (
        f"=== CURRENT STATE VARIABLES ===\n{vars_json_str}{blocks_str}\n\n"
        f"=== CURRENT USER INPUT ===\n{user_input}"
    )

    # Append History to the data block if it exists
    if history_str:
        data_block = f"=== CONVERSATION HISTORY ===\n{history_str}\n\n" + data_block

    return data_block
