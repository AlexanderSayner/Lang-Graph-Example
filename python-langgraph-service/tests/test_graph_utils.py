"""Unit tests for condition literals and router matching.

Conditions are hand-written strings compared against values that come out of the model's parsed
JSON, and the two disagree in ways that are easy to get wrong: a JSON boolean against Python's
"True", a quoted word against a number, a key the graph never set. These tests pin the matcher's
contract, including the regression that made ``output == true`` unreachable - no branch matched,
so the run ended at the END sentinel without ever executing an END node.
"""

import pytest

from app.graph_engine.graph_utils import (
    coerce_bool,
    parse_condition,
    parse_conditions,
    parse_literal,
    resolve_route,
    values_match,
)

# The state field a router most often has in common with a variable name: `output` always holds the
# previous node's raw text, even when no variable called `output` exists.
RAW_OUTPUT = "the raw node output"


def route(variables, conditions, **extra_state):
    """Mirrors the servicer call site end to end: parse at build time, then resolve_route with the
    triples and the full state.

    Every test routes through here so none of them can quietly exercise a lookup production never
    performs - omitting the full state is how the masked-null bug was able to pass its own test.
    """
    state = {"input": "4", "output": RAW_OUTPUT, "variables": variables, **extra_state}
    return resolve_route(variables, parse_conditions(conditions, "n"), state)


# --- Literal parsing ---

def test_parse_condition_returns_a_typed_literal():
    assert parse_condition("output == true") == ("output", True)
    assert parse_condition("output == false") == ("output", False)
    assert parse_condition("output ==  true  ") == ("output", True)
    assert parse_condition("count == 3") == ("count", 3)
    assert parse_condition("ratio == -2.5") == ("ratio", -2.5)
    assert parse_condition("mode == null") == ("mode", None)
    # Quoting forces a string, even when the text spells a boolean or a number.
    assert parse_condition('flag == "true"') == ("flag", "true")
    assert parse_condition('port == "8080"') == ("port", "8080")
    # A bare word is a string: routing by enum is what the docs show.
    assert parse_condition("intent == billing") == ("intent", "billing")
    assert parse_condition("intent == 'billing'") == ("intent", "billing")


def test_parse_condition_rejects_conditions_without_the_operator():
    assert parse_condition(None) == (None, None)
    assert parse_condition("") == (None, None)
    assert parse_condition("output") == (None, None)


def test_parse_literal_is_case_insensitive_and_strips_whitespace():
    assert parse_literal(" TRUE ") is True
    assert parse_literal("False") is False
    assert parse_literal("  billing ") == "billing"
    # 1 and 0 are NUMBERS in a literal: `x == 1` means the number 1. Pinning the identity matters -
    # `parse_literal("1") is True` would have been accepted by `== 1` too, since True == 1.
    assert parse_literal("1") == 1 and parse_literal("1") is not True
    assert parse_literal("0") == 0 and parse_literal("0") is not False


def test_numeric_literals_stay_numeric_but_a_boolean_still_matches_them():
    """`x == 1` parses to the number 1, so inspecting or logging a condition tells the truth, while
    the matcher still coerces: a model answering true (or "1") routes on it anyway."""
    assert parse_condition("x == 1") == ("x", 1)
    assert parse_condition("x == 0") == ("x", 0)
    assert route({"x": True}, [("n", "x == 1")]) == "n"
    assert route({"x": 1}, [("n", "x == 1")]) == "n"
    assert route({"x": 0}, [("n", "x == 1")]) is None


def test_a_signed_numeric_literal_parses_as_a_number():
    """x == +3 reads the way it is written: the sign belongs to the literal, not to some text that
    could then never equal a number."""
    assert parse_condition("x == +3") == ("x", 3)
    assert parse_condition("x == -3.5") == ("x", -3.5)
    assert route({"x": 3}, [("n", "x == +3")]) == "n"
    assert route({"x": -3}, [("n", "x == -3")]) == "n"
    assert route({"x": 3}, [("n", "x == -3")]) is None


def test_quoting_controls_parsing_but_not_matching():
    """parse_literal returns a string for a quoted word, yet the matcher coerces both sides, so
    flag == 'true' matches a real boolean True. Quoting must never be required to make an edge
    fire - the previous docstring claimed otherwise, and a docstring that lies about routing is how
    subtle bugs get introduced."""
    assert parse_condition("flag == 'true'") == ("flag", "true")
    assert route({"flag": True}, [("n", "flag == 'true'")]) == "n"
    assert route({"flag": "true"}, [("n", "flag == 'true'")]) == "n"
    assert route({"flag": False}, [("n", "flag == 'true'")]) is None


# --- Matching ---

def test_boolean_conditions_route_a_json_boolean():
    """The regression: {"output": true} against `output == true` used to compare "True" with
    "true", match neither edge, and end the run before either END node executed."""
    conditions = [("end_true", "output == true"), ("end_false", "output == false")]
    assert route({"output": True}, conditions) == "end_true"
    assert route({"output": False}, conditions) == "end_false"


def test_boolean_conditions_accept_the_spellings_a_model_can_produce():
    conditions = [("end_true", "output == true"), ("end_false", "output == false")]
    for spelling in ("true", "True", "TRUE", "yes", "1", 1, 1.0, True):
        assert route({"output": spelling}, conditions) == "end_true", spelling
    for spelling in ("false", "False", "FALSE", "no", "0", 0, False):
        assert route({"output": spelling}, conditions) == "end_false", spelling


def test_boolean_conditions_never_match_a_mismatched_or_absent_value():
    conditions = [("end_true", "output == true"), ("end_false", "output == false")]
    assert route({"output": "maybe"}, conditions) is None  # not a boolean
    # Not in variables: the state's raw `output` text exists, but it never reads as a boolean.
    assert route({}, conditions) is None
    assert route({"other": True}, conditions) is None
    assert route({"output": None}, conditions) is None     # present, but null


def test_numeric_conditions_match_numbers_texts_and_floats():
    conditions = [("eq", "count == 3"), ("other", "count == 4")]
    assert route({"count": 3}, conditions) == "eq"
    assert route({"count": "3"}, conditions) == "eq"   # model returned a string
    assert route({"count": 3.0}, conditions) == "eq"
    assert route({"count": 4}, conditions) == "other"
    assert route({"count": 5}, conditions) is None


def test_text_conditions_are_case_insensitive():
    conditions = [("billing", "intent == 'billing'"), ("general", "intent == 'general'")]
    assert route({"intent": "billing"}, conditions) == "billing"
    assert route({"intent": "Billing"}, conditions) == "billing"   # model capitalised it
    assert route({"intent": "GENERAL"}, conditions) == "general"
    assert route({"intent": "other"}, conditions) is None


def test_conditions_may_read_a_plain_state_key_as_well():
    state = {"input": "yes", "variables": {}}
    parsed = parse_conditions([("y", "input == 'yes'")], "n")
    assert resolve_route(state["variables"], parsed, state) == "y"
    # The state fallback only applies when the variables do not define the key themselves.
    assert resolve_route({"input": "no"}, parsed, {"input": "yes"}) is None


def test_a_null_condition_never_matches_an_unset_key():
    state = {"input": "4", "output": RAW_OUTPUT, "variables": {}}
    null_check = parse_conditions([("n", "x == null")], "n")
    assert resolve_route({}, null_check, state) is None     # absent != null
    assert resolve_route({"x": None}, null_check, state) == "n"
    assert resolve_route({"x": 1}, null_check, state) is None


def test_an_explicit_null_is_not_masked_by_a_same_named_state_field():
    """The servicer passes the whole state, and `output` exists there as the previous node's raw
    text. Reading that fallback when the variable was explicitly None made `output == null`
    unsatisfiable in production, while the unit test above - written without extra_state - claimed
    the opposite. The variables must win whenever the key exists in them."""
    state = {"input": "4", "output": RAW_OUTPUT, "variables": {"output": None}}
    null_on_output = parse_conditions([("n", "output == null")], "n")
    exact_text = parse_conditions([("n", "output == 'x'")], "n")
    raw_text = parse_conditions([("n", f"output == '{RAW_OUTPUT}'")], "n")

    assert resolve_route({"output": None}, null_on_output, state) == "n"
    # A variable that is set at all still beats the same-named state field.
    assert resolve_route({"output": "x"}, exact_text, state) == "n"
    # Only when the variables never define the key is the state field consulted.
    assert resolve_route({}, raw_text, state) == "n"


def test_a_malformed_condition_is_rejected_at_build_time():
    """An edge that cannot be parsed can never route. Skipping it at runtime turned a typo into a
    graph that quietly ended without running a branch node; the build names the node, the condition
    and the expected shape instead."""
    with pytest.raises(ValueError) as excinfo:
        parse_conditions([("ok", "output")], "judge")
    assert "Malformed condition" in str(excinfo.value)
    assert "judge" in str(excinfo.value)

    with pytest.raises(ValueError):
        parse_conditions([("ok", "just text")], "judge")
    with pytest.raises(ValueError):
        parse_conditions([("ok", "")], "judge")
    # A valid sibling does not save it - the node would be half-routable.
    with pytest.raises(ValueError):
        parse_conditions([("ok", "output == true"), ("bad", "output = true")], "judge")

    # A clean list parses to triples, in order, with typed literals.
    assert parse_conditions([("a", "output == true"), ("b", "input == 'yes'")], "judge") == [
        ("a", "output", True), ("b", "input", "yes")
    ]


def test_values_match_reports_a_missing_key_as_no_match():
    assert values_match(True, True, present=True) is True
    assert values_match(True, True, present=False) is False


def test_coerce_bool_reads_common_spellings():
    assert coerce_bool(True) is True and coerce_bool(False) is False
    assert coerce_bool("true") is True and coerce_bool(" FALSE ") is False
    assert coerce_bool(1) is True and coerce_bool(0) is False and coerce_bool(1.0) is True
    assert coerce_bool("maybe") is None
    assert coerce_bool(2) is None
    assert coerce_bool(None) is None


