import json
import logging
import operator
import re
from typing import TypedDict, Dict, Any, Annotated, List

logger = logging.getLogger(__name__)


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
    # Automatically append new history items
    variables: Annotated[Dict[str, Any], merge_dicts]
    history: Annotated[List[Dict[str, Any]], operator.add]
    total_tokens: Annotated[int, operator.add]


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


def format_state_context(state: GraphState, include_history: bool = True,
                          include_http_result: bool = True) -> str:
    """Prepares the conversation history and variables for the LLM.

    `include_history` / `include_http_result` let a node ask for a smaller context: a very long
    prompt (full API payload + full history) makes the model noticeably more likely to refuse,
    and a node usually needs only part of it. Values that are switched off are simply not shown.
    """
    # We MUST show the LLM the current variables so it knows what is already filled.
    current_vars = state.get("variables", {}) or {}
    if not include_http_result:
        current_vars = {k: v for k, v in current_vars.items() if k != "http_result"}
    vars_json_str = json.dumps(current_vars, indent=2)

    # Dynamic History (Conversation memory)
    # This is CRITICAL. Without this, the LLM doesn't know what happened before.
    history = (state.get("history") or []) if include_history else []
    history_str = ""
    if history:
        # Format: "Node Name: Output"
        history_str = "Conversation History:\n" + "\n".join(
            [f"- {h.get('node')}: {h.get('output')}" for h in history if h.get('output')]
        )

    user_input = state.get("input") or ""

    # We create a clear "Data Block" so the LLM cannot hallucinate state.
    data_block = (
        f"=== CURRENT STATE VARIABLES ===\n{vars_json_str}\n\n"
        f"=== CURRENT USER INPUT ===\n{user_input}"
    )

    # Append History to the data block if it exists
    if history_str:
        data_block = f"=== CONVERSATION HISTORY ===\n{history_str}\n\n" + data_block

    return data_block
