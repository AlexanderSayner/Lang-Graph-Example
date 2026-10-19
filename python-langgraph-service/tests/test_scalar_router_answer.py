"""Unit tests for the bare-answer fallback of a single-key router.

A router that expects one key should not fail because the model replied ``true`` instead of
``{"output": true}`` - and when it did fail, the old code returned without touching `variables`,
so no outgoing edge condition could ever match and the run ended without executing a branch.
"""

import pytest

from app.graph_engine.graph_utils import JsonGenerationError
from app.graph_engine.node_handlers import parse_scalar_answer


def test_single_key_router_accepts_a_bare_scalar_answer():
    assert parse_scalar_answer("true", {"output"}) == {"output": True}
    assert parse_scalar_answer("  False ", {"output"}) == {"output": False}
    assert parse_scalar_answer("```\nfalse\n```", {"output"}) == {"output": False}
    assert parse_scalar_answer("  4  ", {"count"}) == {"count": 4}
    assert parse_scalar_answer("billing", {"intent"}) == {"intent": "billing"}
    assert parse_scalar_answer("'very good'", {"verdict"}) == {"verdict": "very good"}
    assert parse_scalar_answer("null", {"output"}) == {"output": None}
    # A decimal is a single token with one point - still a value.
    assert parse_scalar_answer("3.14", {"ratio"}) == {"ratio": 3.14}


def test_unusable_bare_answers_are_rejected():
    with pytest.raises(JsonGenerationError):
        parse_scalar_answer("Да, ответ верный", {"output"})        # prose, not a value
    with pytest.raises(JsonGenerationError):
        parse_scalar_answer("", {"output"})                        # empty
    with pytest.raises(JsonGenerationError):
        parse_scalar_answer(None, {"output"})                      # empty
    with pytest.raises(JsonGenerationError):
        parse_scalar_answer("Не могу обсуждать это", {"output"})   # refusal
    with pytest.raises(JsonGenerationError):
        parse_scalar_answer("true", {"a", "b"})                    # multi-key needs JSON


def test_truncated_json_and_punctuated_prose_are_rejected():
    """Every one of these has no spaces and fits in 40 characters, so the original no-space guard
    accepted them and stored a value no condition could ever match - the same silent end, only now
    with a misleading variable in the state and no parse error to point at the cause."""
    for garbage in ('{"output":true', "output:true", "{true}", "true}", "true.", "true!", "Да.",
                    "4."):
        with pytest.raises(JsonGenerationError):
            parse_scalar_answer(garbage, {"output"})
