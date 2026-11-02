"""The deterministic `compare` ACTION metadata: array-vs-array matching with no model.

The point of the feature: the compare step of the discrepancy graph kept losing rows,
inventing them and reshuffling hours - all model behaviour. With `compare` in the node's
metadata the engine does the pairing in code: one record in, one object out, by
construction. These tests pin the mechanics and the guarantees (no LLM touched, exact
counts, findings first, tolerant parsing, graceful errors).
"""

import asyncio
import json
import types

from app.graph_engine.node_handlers import NodeHandler


def _handler():
    # No LLM at all: a deterministic node must never need one.
    return NodeHandler(graph_loader=None)


def _meta(**overrides):
    meta = {"label": "Сопоставить", "output_variable": "matched",
            "compare": {"left": "doc1", "right": "doc2"}}
    meta.update(overrides)
    return meta


def _state(doc1, doc2):
    return {"input": "go", "variables": {"doc1": doc1, "doc2": doc2}}


def test_pairs_on_person_and_date_and_classifies_by_hours():
    doc1 = json.dumps([
        {"person": "Новиков", "date": "2026-10-02", "task": "A", "hours": 1},
        {"person": "Новиков", "date": "2026-10-06", "task": "B", "hours": 3},
        {"person": "Тепечин", "date": "2026-10-08", "task": "C", "hours": 4},
    ])
    doc2 = json.dumps([
        {"person": "Новиков", "date": "2026-10-02", "task": "AA", "hours": 2},
        {"person": "Тепечин", "date": "2026-10-08", "task": "CC", "hours": 4.5},
    ])
    result = asyncio.run(_handler()._process_action("n", _meta(), _state(doc1, doc2)))

    matched = json.loads(result["variables"]["matched"])
    assert len(matched) == 3   # 3 left records; both right records paired away
    by_task = {o["task"]: o for o in matched}
    assert by_task["A"]["status"] == "hours_mismatch"      # 1 != 2
    assert by_task["A"]["hours_1"] == 1.0 and by_task["A"]["hours_2"] == 2.0
    assert by_task["B"]["status"] == "only_in_1"           # no partner on 06.10
    assert by_task["B"]["hours_2"] is None
    assert by_task["C"]["status"] == "hours_mismatch"      # 4 != 4.5
    # Findings come first, ok pairs last - the report node's contract.
    statuses = [o["status"] for o in matched]
    assert statuses == ["hours_mismatch", "only_in_1", "hours_mismatch"]


def test_equal_hours_are_ok_and_findings_go_first():
    doc1 = json.dumps([{"person": "A", "date": "2026-10-02", "task": "T", "hours": 4}])
    doc2 = json.dumps([{"person": "A", "date": "2026-10-02", "task": "TT", "hours": 4},
                       {"person": "B", "date": "2026-10-03", "task": "U", "hours": 2}])
    result = asyncio.run(_handler()._process_action("n", _meta(), _state(doc1, doc2)))
    matched = json.loads(result["variables"]["matched"])
    assert [o["status"] for o in matched] == ["only_in_2", "ok"]
    assert matched[0]["hours_1"] is None and matched[0]["hours_2"] == 2.0
    assert matched[0]["task"] == "U", "an only_in_2 object carries the RIGHT side's task"


def test_no_model_is_called_and_the_step_is_free():
    doc1 = json.dumps([{"person": "A", "date": "2026-10-02", "task": "T", "hours": 1}])
    doc2 = json.dumps([{"person": "A", "date": "2026-10-02", "task": "TT", "hours": 1}])
    result = asyncio.run(_handler()._process_action("n", _meta(), _state(doc1, doc2)))
    assert result["llm_billing"] == ""
    assert result["total_tokens"] == 0
    assert "Детерминированное сопоставление" in result["output"]


def test_fenced_pretty_and_ndjson_variables_all_parse():
    doc1 = "```\n[ {\"person\": \"A\", \"date\": \"2026-10-02\", \"task\": \"T\", \"hours\": 1} ]\n```"
    doc2 = ("{\"person\": \"A\", \"date\": \"2026-10-02\", \"task\": \"TT\", \"hours\": 2}\n"
            "{\"person\": \"B\", \"date\": \"2026-10-09\", \"task\": \"U\", \"hours\": 5}")
    result = asyncio.run(_handler()._process_action("n", _meta(), _state(doc1, doc2)))
    matched = json.loads(result["variables"]["matched"])
    assert len(matched) == 2   # 1 left record + 1 unpaired right record


def test_dates_in_different_spellings_and_name_case_still_pair():
    doc1 = json.dumps([{"person": "Новиков ", "date": "04.10.2026", "task": "T", "hours": 1}])
    doc2 = json.dumps([{"person": "новиков", "date": "2026-10-04", "task": "TT", "hours": 9}])
    result = asyncio.run(_handler()._process_action("n", _meta(), _state(doc1, doc2)))
    matched = json.loads(result["variables"]["matched"])
    assert len(matched) == 1 and matched[0]["status"] == "hours_mismatch"
    assert matched[0]["date"] == "04.10.2026", "the record's own spelling is preserved"


def test_custom_status_names_flow_through():
    doc1 = json.dumps([{"person": "A", "date": "2026-10-02", "task": "T", "hours": 1},
                       {"person": "C", "date": "2026-10-05", "task": "Z", "hours": 3}])
    doc2 = json.dumps([{"person": "A", "date": "2026-10-02", "task": "TT", "hours": 2}])
    meta = {"output_variable": "matched",
            "compare": {"left": "doc1", "right": "doc2",
                        "status_names": {"ok": "совпадает", "mismatch": "разночтение",
                                         "left_only": "нет_во_2", "right_only": "нет_в_1"}}}
    result = asyncio.run(_handler()._process_action("n", meta, _state(doc1, doc2)))
    statuses = [o["status"] for o in json.loads(result["variables"]["matched"])]
    # Findings first: left-record findings in doc1 order, then the unpaired right record.
    assert statuses == ["разночтение", "нет_во_2"]


def test_missing_variables_fail_readably_without_crashing():
    doc1 = json.dumps([{"person": "A", "date": "2026-10-02", "task": "T", "hours": 1}])
    state = {"input": "go", "variables": {"doc1": doc1}}
    result = asyncio.run(_handler()._process_action("n", _meta(), state))
    assert result["error"].startswith("missing_variables:doc2")
    assert "doc2" in result["output"]


class _EchoLLM:
    provider = "openai"
    billing_owner = "user"
    calls = 0

    async def generate(self, user_message, system_message="", credentials=None):
        self.calls += 1
        return types.SimpleNamespace(content="echo", usage={"total_tokens": 1})


def test_a_node_without_compare_metadata_still_uses_the_llm_path():
    state = {"input": "hi", "context": {}}
    llm = _EchoLLM()
    result = asyncio.run(_handler()._process_action("n", {"system_prompt": "answer"},
                                                    state, llm=llm))
    assert llm.calls == 1
    assert result["output"] == "echo"


def test_an_action_with_compare_but_no_prompt_never_touches_the_llm_path():
    # The graph keeps the node promptless: the deterministic branch must return before the
    # "no prompt -> pass-through" check, or the output variable would silently vanish.
    doc1 = json.dumps([{"person": "A", "date": "2026-10-02", "task": "T", "hours": 1}])
    doc2 = json.dumps([{"person": "A", "date": "2026-10-02", "task": "TT", "hours": 1}])
    result = asyncio.run(_handler()._process_action(
        "n", {"label": "Сопоставить", "output_variable": "matched",
              "compare": {"left": "doc1", "right": "doc2"}}, _state(doc1, doc2)))
    assert "matched" in result["variables"]
    assert len(json.loads(result["variables"]["matched"])) == 1


def test_a_string_json_config_and_the_java_toString_spelling_both_work():
    # The gRPC transport types metadata as map<string, string>, so the config can only ride
    # as a string. Two spellings exist in the wild: JSON (the current Java mapper) and the
    # older Map.toString() form ({"left=doc1"}) persisted into older graph definitions.
    json_config = {"output_variable": "matched",
                   "compare": "{\"left\": \"doc1\", \"right\": \"doc2\"}"}
    java_config = {"output_variable": "matched",
                   "compare": "{left=doc1, right=doc2}"}
    for meta in (json_config, java_config):
        doc1 = json.dumps([{"person": "A", "date": "2026-10-02", "task": "T", "hours": 1}])
        doc2 = json.dumps([{"person": "A", "date": "2026-10-02", "task": "TT", "hours": 1}])
        result = asyncio.run(_handler()._process_action("n", meta, _state(doc1, doc2)))
        matched = json.loads(result["variables"]["matched"])
        assert len(matched) == 1 and matched[0]["status"] == "ok"


def test_nested_status_names_survive_the_java_toString_spelling():
    doc1 = json.dumps([{"person": "A", "date": "2026-10-02", "task": "T", "hours": 1},
                       {"person": "C", "date": "2026-10-05", "task": "Z", "hours": 3}])
    doc2 = json.dumps([{"person": "A", "date": "2026-10-02", "task": "TT", "hours": 2}])
    meta = {"output_variable": "matched",
            "compare": "{left=doc1, right=doc2, "
                       "status_names={ok=ok, mismatch=РАЗНЫЕ, left_only=НЕТ_ВО_2, "
                                     "right_only=НЕТ_В_1}}"}
    result = asyncio.run(_handler()._process_action("n", meta, _state(doc1, doc2)))
    matched = json.loads(result["variables"]["matched"])
    # doc1 A pairs (2 != 1) and doc1 C stays unpaired; the custom names prove the nested
    # map inside the stringified config was parsed, not taken as a literal.
    assert [o["status"] for o in matched] == ["РАЗНЫЕ", "НЕТ_ВО_2"]
