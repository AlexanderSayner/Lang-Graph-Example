"""End-to-end tests for conditional-edge routing.

They drive the real graph built by ``LangGraphServiceServicer._build_langgraph`` with the real
``NodeHandler``, stubbing only the model, so the whole path a run takes is exercised: LLM answer
-> JSON extraction -> variables merge -> router -> which END node runs.

The graph mirrors a quiz: START asks "2+2", the HUMAN node (which is also the router) judges the
answer, and two END nodes take over on ``output == true`` / ``output == false`` - the structure
that produced the original bug, where neither edge matched and the run ended without a branch.
"""

import asyncio
import logging
import types

import grpc
import pytest

from cachetools import LRUCache
from langgraph.checkpoint.memory import MemorySaver

from app.clients.llm import LLMResponse
from app.graph_engine.node_handlers import NodeHandler
from app.models import GraphDefinition
from app.services.langgraph_servicer import LangGraphServiceServicer

QUIZ_GRAPH = {
    "name": "quiz",
    "nodes": [
        {"node_id": "start", "node_type": "START", "handler_name": "startHandler",
         "metadata": {"system_prompt": "Спроси у пользователя сколько будет 2+2"}},
        {"node_id": "judge", "node_type": "HUMAN", "handler_name": "judgeHandler",
         "metadata": {"system_prompt": "Определи, правильно ли ответил пользователь. Верни с типом boolean"}},
        {"node_id": "congrats", "node_type": "END", "handler_name": "congratsHandler",
         "metadata": {"system_prompt": "Поздравь пользователя с правильным ответом"}},
        {"node_id": "textbook", "node_type": "END", "handler_name": "textbookHandler",
         "metadata": {"system_prompt": "Отправь пользователя в учебник математики"}},
    ],
    "edges": [
        {"source": "start", "target": "judge", "condition": None},
        {"source": "judge", "target": "congrats", "condition": "output == true"},
        {"source": "judge", "target": "textbook", "condition": "output == false"},
    ],
    "config": {},
}


class _StubLLM:
    """Stands in for the model: JSON for the routing question, plain text for every other node."""
    provider = "yandex"

    def __init__(self, router_answer):
        self.router_answer = router_answer

    async def generate(self, user_message, system_message):
        if "routing classifier" in system_message:
            return LLMResponse(self.router_answer, usage={"total_tokens": 152},
                               provider="yandex", model="stub")
        return LLMResponse("Сколько будет два плюс два?", usage={"total_tokens": 50},
                           provider="yandex", model="stub")


class _StubRegistry:
    """Mirrors LLMClientRegistry.lazy() - resolve_llm_config still runs, no model is built."""

    def __init__(self, router_answer):
        self.answer = _StubLLM(router_answer)

    def lazy(self, _config):
        return self.answer


def _build_servicer(router_answer):
    """Servicer wired for a quiz run: real graph builder, stubbed model, in-memory checkpointer.

    NodeHandler opens a gRPC channel to the HTTP-request service in its constructor, but the channel
    is lazy and the quiz graph has no HTTP node, so no extra service is needed for these tests.
    """
    servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)
    servicer._llm_registry = _StubRegistry(router_answer)
    servicer._checkpointer = MemorySaver()
    servicer._compiled_graphs = LRUCache(maxsize=4)
    servicer.node_handler = NodeHandler(graph_loader=lambda _graph_id: None)
    return servicer


async def _run_quiz(router_answer, thread_id):
    """Compile the quiz graph, pause at the HUMAN node, resume with the answer, return the state."""
    servicer = _build_servicer(router_answer)
    graph = servicer._build_langgraph(GraphDefinition(**QUIZ_GRAPH))
    config = {"configurable": {"thread_id": thread_id}}

    await graph.ainvoke({"input": "4", "context": {}, "timestamp": 1.0}, config=config)
    paused = await graph.aget_state(config)
    assert list(paused.next) == ["judge"], "the run must wait for the HUMAN node's answer"

    final = await graph.ainvoke(None, config=config)
    assert list((await graph.aget_state(config)).next) == [], "the run must be finished"
    return final


def test_json_true_answer_reaches_the_congratulations_end_node():
    async def scenario():
        # Exactly the answers from the failing run: the model replied with a fenced JSON object.
        final = await _run_quiz('```\n{\n  "output": true\n}\n```', "t-true")
        assert final["last_node"] == "congrats"
        assert final["variables"]["output"] is True

    asyncio.run(scenario())


def test_json_false_answer_reaches_the_textbook_end_node():
    async def scenario():
        final = await _run_quiz('{"output": false}', "t-false")
        assert final["last_node"] == "textbook"
        assert final["variables"]["output"] is False

    asyncio.run(scenario())


def test_bare_true_answer_still_reaches_the_congratulations_node():
    """No JSON braces at all: the single-key router accepts the scalar and routes on it."""
    async def scenario():
        final = await _run_quiz("true", "t-bare")
        assert final["last_node"] == "congrats"
        assert final["variables"]["output"] is True

    asyncio.run(scenario())


def test_an_unmatched_router_ends_the_run_without_executing_a_branch():
    """A value no condition names must not hang the run - it ends, loudly, with no END node run."""
    async def scenario():
        final = await _run_quiz('{"output": "maybe"}', "t-nomatch")
        assert final["last_node"] == "judge"       # neither congrats nor textbook ran
        assert final["variables"]["output"] == "maybe"

    asyncio.run(scenario())


def test_a_malformed_edge_condition_fails_the_build_naming_the_node():
    """An edge whose condition has no == can never route. Skipping it at runtime turned a typo into
    a graph that quietly ended without running any branch node; the build rejects it, naming the
    node, the offending condition and the expected shape - which the gateway then surfaces."""
    servicer = _build_servicer('{"output": true}')
    broken = {**QUIZ_GRAPH, "edges": [*QUIZ_GRAPH["edges"],
                                       {"source": "judge", "target": "congrats",
                                        "condition": "output = true"}]}

    with pytest.raises(ValueError) as excinfo:
        servicer._build_langgraph(GraphDefinition(**broken))

    message = str(excinfo.value)
    assert "Malformed condition" in message
    assert "judge" in message
    assert "output = true" in message


def test_a_rejected_definition_surfaces_the_reason_as_invalid_argument():
    """Executing a stored graph the engine now rejects must report WHY: the gateway maps
    INVALID_ARGUMENT to a readable message, while the generic INTERNAL abort would hide the reason
    behind an internal-error page."""
    async def scenario():
        servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)

        async def _load(_graph_id):
            raise ValueError("Malformed condition 'output = true' on node 'judge'")

        servicer._load_and_compile_graph = _load
        recorded = {}

        async def _abort(code, details):
            recorded["code"] = code
            recorded["details"] = details

        request = types.SimpleNamespace(graph_id="g1", thread_id="t1", input="4", context={})
        async for _ in servicer.ExecuteGraph(request, types.SimpleNamespace(abort=_abort)):
            pass

        assert recorded["code"] == grpc.StatusCode.INVALID_ARGUMENT
        assert "Malformed condition" in recorded["details"]
        assert "judge" in recorded["details"]

    asyncio.run(scenario())


def test_an_unmatched_router_logs_the_conditions_and_the_variables(caplog):
    """The plan promised the no-match failure would be diagnosable from the service log alone, so
    pin it: warning level, the node, the parsed key/literal pairs and the actual variables."""
    async def scenario():
        with caplog.at_level(logging.WARNING, logger="app.services.langgraph_servicer"):
            final = await _run_quiz('{"output": "maybe"}', "t-nomatch-log")

        assert final["last_node"] == "judge"
        message = next(r.getMessage() for r in caplog.records
                       if "No condition matched" in r.getMessage())
        assert "judge" in message
        assert "'output', True" in message          # the parsed conditions, in order
        assert "maybe" in message                   # the value they were checked against

    asyncio.run(scenario())


def test_a_router_answer_missing_the_expected_key_is_flagged(caplog):
    """Valid JSON that does not answer the conditioned key is the remaining silent-end family:
    everything parses, nothing matches. The router now says so at the moment it happens."""
    async def scenario():
        with caplog.at_level(logging.WARNING, logger="app.graph_engine.node_handlers"):
            final = await _run_quiz('{"answer": true}', "t-missing-key")

        assert final["last_node"] == "judge"
        flagged = [r.getMessage() for r in caplog.records if "without the expected key" in r.getMessage()]
        assert flagged, "the missing expected key must be logged"
        assert "output" in flagged[0] and "answer" in flagged[0]

    asyncio.run(scenario())
