"""Safety-filter refusals must END the run with an explicit message, not poison it.

The incident (run 1793704220952): yandexGPT refused node_u1793704220952 twice with
«Я не могу обсуждать эту тему»; `_generate` returned the refusal as a normal answer,
`_process_action` wrote it into `doc1`, the deterministic compare could not parse it,
and the report node - answering from nothing - reported «Расхождений не найдено»:
a confident lie built entirely on a refusal.

Pinned here:
- after the retry budget, prose refusals raise LLMRefusalError, while data payloads
  that merely CONTAIN a refusal marker (a task named «Не могу подтвердить отчёт»)
  are still returned unchanged;
- a data node or a router turns that into a terminal stop; a prose node keeps the
  model's own words as its output (historic behaviour - the answer IS the output);
- the servicer converts the abort into NODE_END(error) + END(message) - never ERROR -
  and downstream nodes never run;
- `error` is part of GraphState, so it survives langgraph's schema filtering and the
  NODE_END frame's error_message is populated in REAL runs (not only in fake graphs,
  which is where the old mechanism only ever worked).

No gRPC server, Postgres, Redis or LLM is required - the servicer is built with
``__new__`` and its collaborators stubbed, exactly like the cancellation tests.
"""

import asyncio
import json
import types
import typing

import pytest

from app.clients.llm import LLMCredentialSet
from app.config import settings
from app.graph_engine.graph_utils import GraphState
from app.graph_engine.node_handlers import (
    LLMRefusalError,
    NodeHandler,
    TerminalNodeError,
    looks_like_data,
    refusal_message,
)
from app.services.execution_registry import ExecutionRegistry
from app.services.langgraph_servicer import LangGraphServiceServicer


# --- the model side -------------------------------------------------------------

class _RefusingLLM:
    """Answers with the incident's refusal, every time."""

    provider = "yandex"
    billing_owner = "service"

    def __init__(self, answer="Я не могу обсуждать эту тему. Давайте поговорим о чём-нибудь ещё."):
        self.answer = answer
        self.calls = 0

    async def generate(self, user_message, system_message="", credentials=None):
        self.calls += 1
        return types.SimpleNamespace(content=self.answer, usage={"total_tokens": 5})


def _handler():
    return NodeHandler(graph_loader=None)


def _budget(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "LLM_SERVICE_KEY_ENABLED", True)


def _data_state():
    return {"input": "go", "context": {}}


# --- _generate: what survives the retry budget ----------------------------------

def test_a_refusal_that_outlives_the_budget_raises(monkeypatch):
    _budget(monkeypatch)
    llm = _RefusingLLM()

    with pytest.raises(LLMRefusalError) as ei:
        asyncio.run(_handler()._generate("q", "sys", llm,
                                         credentials=LLMCredentialSet.empty()))

    exc = ei.value
    assert llm.calls == 2, "the retry budget was spent before giving up"
    assert exc.provider == "yandex"
    assert exc.attempts == 2
    assert "не могу обсуждать" in exc.answer
    assert exc.error_code == "llm_refusal:yandex"
    assert exc.llm_billing == "service"
    assert exc.total_tokens == 5


def test_an_empty_answer_after_the_budget_is_a_refusal_too(monkeypatch):
    _budget(monkeypatch)
    llm = _RefusingLLM(answer="")

    with pytest.raises(LLMRefusalError) as ei:
        asyncio.run(_handler()._generate("q", "sys", llm,
                                         credentials=LLMCredentialSet.empty()))
    assert ei.value.answer == ""
    assert ei.value.attempts == 2


def test_refusal_words_inside_a_json_payload_are_data_not_a_refusal(monkeypatch):
    """«Не могу подтвердить отчёт» is a TASK NAME: the payload must pass through,
    exactly as before - stopping here would reject perfectly good tracker data."""
    _budget(monkeypatch)
    payload = json.dumps([{"person": "Новиков", "task": "Не могу подтвердить отчёт",
                           "hours": 2}], ensure_ascii=False)
    llm = _RefusingLLM(answer=payload)

    response, billing = asyncio.run(_handler()._generate(
        "q", "sys", llm, credentials=LLMCredentialSet.empty()))

    assert str(getattr(response, "content", response)) == payload
    assert billing == "service"
    assert llm.calls == 2, "the marker still costs one retry, but never the run"


def test_looks_like_data_distinguishes_payloads_from_refusal_prose():
    assert looks_like_data('[{"person": "A"}]')
    assert looks_like_data('{"output": true}')
    assert looks_like_data('```json\n[{"a": 1}]\n```')
    assert looks_like_data("| task | hours |\n| --- | --- |\n| X | 2 |")
    assert not looks_like_data("Я не могу обсуждать эту тему.")
    assert not looks_like_data("")
    assert not looks_like_data("   \n  ")


# --- handlers: which nodes stop and which merely speak --------------------------

def test_a_data_node_turns_a_refusal_into_a_terminal_stop(monkeypatch):
    """The poisoning path of the incident: doc1 must never receive the refusal."""
    _budget(monkeypatch)
    meta = {"system_prompt": "разбери таймшит", "output_variable": "doc1",
            "label": "разобрать таймшит трекера"}

    with pytest.raises(LLMRefusalError) as ei:
        asyncio.run(_handler()._process_action(
            "node25", meta, _data_state(), llm=_RefusingLLM(),
            credentials=LLMCredentialSet.empty()))

    exc = ei.value
    assert exc.node_id == "node25"
    assert exc.node_label == "разобрать таймшит трекера"
    # The message is what the chat will read: it names the provider, the node and the
    # model's own words, and says why stopping beats guessing.
    assert "yandex" in exc.user_message
    assert "разобрать таймшит трекера" in exc.user_message
    assert "Я не могу обсуждать" in exc.user_message
    assert "Граф остановлен" in exc.user_message
    assert "дальнейшие шаги выдали бы недостоверный результат" in exc.user_message


def test_a_prose_node_keeps_the_models_own_words_as_its_output(monkeypatch):
    """No output_variable: the answer IS the output. Wrapping it would swallow answers
    that merely QUOTE refusal phrases, so the historic pass-through stays."""
    _budget(monkeypatch)
    llm = _RefusingLLM()

    result = asyncio.run(_handler()._process_action(
        "node29", {"system_prompt": "напиши отчёт"}, _data_state(), llm=llm,
        credentials=LLMCredentialSet.empty()))

    assert result["output"] == llm.answer
    assert "error" not in result, "a pass-through is not a node failure"
    assert result["llm_billing"] == "service"
    assert result["history"][0]["output"] == llm.answer


def test_an_empty_answer_stops_even_a_prose_node(monkeypatch):
    """There is nothing to pass on: silence would end the run with an empty output."""
    _budget(monkeypatch)

    with pytest.raises(LLMRefusalError) as ei:
        asyncio.run(_handler()._process_action(
            "node29", {"system_prompt": "напиши отчёт"}, _data_state(),
            llm=_RefusingLLM(answer=""), credentials=LLMCredentialSet.empty()))
    assert "пустой ответ" in ei.value.user_message


def test_a_router_refusal_is_terminal_because_no_branch_keys_can_exist(monkeypatch):
    _budget(monkeypatch)

    with pytest.raises(LLMRefusalError) as ei:
        asyncio.run(_handler()._process_router(
            "r1", {"system_prompt": "route", "label": "намерение"}, {"output"},
            {"input": "hi", "variables": {}}, llm=_RefusingLLM(),
            credentials=LLMCredentialSet.empty()))

    exc = ei.value
    assert exc.node_id == "r1"
    assert exc.node_label == "намерение"
    assert exc.user_message, "the servicer turns this into the run's output"


def test_a_refusal_inside_a_subgraph_is_relabelled_to_the_outer_node():
    """The inner graph aborts (ainvoke raises); the frame must name the OUTER node,
    while the message keeps the inner label - it says which nested step refused."""
    inner_exc = LLMRefusalError(provider="yandex", model="", attempts=2,
                                answer="Не могу обсуждать это.", llm_billing="service")
    inner_exc.node_id = "inner_parse"
    inner_exc.node_label = "внутренний разбор"
    inner_exc.user_message = refusal_message(inner_exc)

    class _Inner:
        async def aget_state(self, cfg):
            return types.SimpleNamespace(next=[])

        async def ainvoke(self, state, config=None):
            raise inner_exc

    async def _load(_gid):
        return _Inner()

    handler = NodeHandler(graph_loader=_load)
    state = {"input": "go", "variables": {}, "history": []}
    config = {"configurable": {"thread_id": "t"}}

    with pytest.raises(LLMRefusalError) as ei:
        asyncio.run(handler._process_subgraph(
            "gnode", {"label": "ГРАФ", "subgraph_id": "sg1"}, state, config))

    assert ei.value is inner_exc
    assert ei.value.node_id == "gnode"
    assert ei.value.node_label == "внутренний разбор"


# --- compare: unparseable data stops, missing data still walks past -------------

def test_unparseable_compare_data_stops_with_an_honest_message():
    state = {"input": "go",
             "variables": {"doc1": "просто текст, а не массив",
                           "doc2": json.dumps([{"person": "A", "date": "2026-10-02",
                                                "task": "T", "hours": 1}])}}
    meta = {"label": "Сопоставить", "output_variable": "matched",
            "compare": {"left": "doc1", "right": "doc2"}}

    with pytest.raises(TerminalNodeError) as ei:
        asyncio.run(_handler()._process_action("n", meta, state))

    exc = ei.value
    assert not isinstance(exc, LLMRefusalError)
    assert exc.error_code.startswith("unparseable_arrays:")
    assert exc.node_id == "n"
    assert "не удалось разобрать" in exc.user_message
    assert "Сопоставить" in exc.user_message
    assert "недостоверный результат" in exc.user_message


def test_missing_compare_variables_still_walk_past_for_the_attachment_flow():
    """The no-file flow relies on the run continuing (the report re-asks politely):
    only GARBAGE data is terminal, absent data is not."""
    state = {"input": "go",
             "variables": {"doc1": json.dumps([{"person": "A", "date": "2026-10-02",
                                                "task": "T", "hours": 1}])}}
    meta = {"label": "Сопоставить", "output_variable": "matched",
            "compare": {"left": "doc1", "right": "doc2"}}

    result = asyncio.run(_handler()._process_action("n", meta, state))
    assert result["error"].startswith("missing_variables:doc2")
    assert "matched" not in result.get("variables", {})


# --- servicer: the abort becomes NODE_END + END, and nothing downstream runs -----

def _build_servicer(graph):
    servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)
    servicer._active_runs = ExecutionRegistry()
    servicer._document_ref_keys = {}

    async def _load(_graph_id):
        return graph

    servicer._load_and_compile_graph = _load
    return servicer


def _request():
    return types.SimpleNamespace(graph_id="g1", thread_id="t1", input="hi", context={})


def _grpc_context():
    async def _abort(code, details):  # pragma: no cover - only hit on the error paths
        raise AssertionError(f"unexpected abort: {code} {details}")

    return types.SimpleNamespace(abort=_abort)


async def _collect(generator, events):
    async for response in generator:
        events.append(response)


def _run_through_servicer(graph):
    async def scenario():
        servicer = _build_servicer(graph)
        events = []
        await asyncio.wait_for(
            _collect(servicer.ExecuteGraph(_request(), _grpc_context()), events),
            timeout=10)
        return events

    return asyncio.run(scenario())


def _compiled(build):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import StateGraph

    workflow = StateGraph(GraphState)
    build(workflow)
    return workflow.compile(checkpointer=MemorySaver())


def test_a_refusal_ends_the_run_with_the_message_and_never_reaches_downstream():
    """The whole point: real langgraph, real servicer - the refusing node aborts,
    NODE_END carries the error, END carries the message, and the node AFTER it
    (the one that used to invent «Расхождений не найдено») never executes."""
    downstream_ran = []

    def build(workflow):
        async def refusing(state):
            exc = LLMRefusalError(provider="yandex", model="yandexgpt-lite",
                                  attempts=2,
                                  answer="Я не могу обсуждать эту тему.",
                                  llm_billing="service", total_tokens=7)
            exc.node_id = "node_parse"
            exc.node_label = "разобрать таймшит трекера"
            exc.user_message = refusal_message(exc)
            raise exc

        async def report(state):
            downstream_ran.append(True)
            return {"last_node": "node_report", "output": "Расхождений не найдено"}

        workflow.add_node("node_parse", refusing)
        workflow.add_node("node_report", report)
        workflow.set_entry_point("node_parse")
        workflow.add_edge("node_parse", "node_report")

    events = _run_through_servicer(_compiled(build))

    assert downstream_ran == [], "the report node must never run on refusal data"
    assert all(event.event_type != "ERROR" for event in events), \
        "a refusal is an ending, not a failed turn"

    frame = next(event for event in events if event.event_type == "NODE_END")
    assert frame.node_id == "node_parse"
    assert frame.error_message == "llm_refusal:yandex"
    assert "разобрать таймшит трекера" in frame.output

    end = events[-1]
    assert end.event_type == "END"
    assert "Я не могу обсуждать" in end.output
    assert "Граф остановлен" in end.output
    assert end.llm_billing == "service"
    # No committed node ever ran, so the total is the refusal's own usage.
    assert end.total_tokens == 7


def test_the_error_key_survives_real_langgraph_and_stays_per_node():
    """Before `error` was declared in GraphState, langgraph silently dropped it from
    the update event: NODE_END.error_message was ALWAYS empty in production (only the
    fake-graph tests ever saw it). This pins the real path - and that the error is the
    FAILING node's own, not a stale value leaking into the next node's frame."""
    frames = {}

    def build(workflow):
        async def failing(state):
            return {"last_node": "node_doc", "output": "no ref",
                    "error": "missing_context:doc1_ref"}

        async def clean(state):
            return {"last_node": "node_ok", "output": "fine"}

        workflow.add_node("node_doc", failing)
        workflow.add_node("node_ok", clean)
        workflow.set_entry_point("node_doc")
        workflow.add_edge("node_doc", "node_ok")

    events = _run_through_servicer(_compiled(build))
    for event in events:
        if event.event_type == "NODE_END":
            frames[event.node_id] = event.error_message

    assert frames["node_doc"] == "missing_context:doc1_ref"
    assert frames["node_ok"] == "", "a clean node must not inherit the previous error"
    assert events[-1].event_type == "END", "missing_context still walks past (by design)"


def test_graphstate_declares_the_error_channel():
    assert "error" in typing.get_type_hints(GraphState)




