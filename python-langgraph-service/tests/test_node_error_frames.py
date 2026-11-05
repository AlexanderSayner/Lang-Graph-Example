"""A graph node that reports an error of its own must say so on its NODE_END frame.

A DOCUMENT node whose slot the run never filled returns ``{"error": "missing_context:doc1_ref"}``
and the graph walks straight past it - the run keeps going and ends with whatever fallback the
last node produces. The caller therefore must be able to see the node's own failure on the frame
itself, not only buried in the state blob, or a fallback answer reads like a real one.

No gRPC server, Postgres, Redis or LLM is required - the servicer is built with ``__new__`` and
the collaborators ``ExecuteGraph`` uses are stubbed, exactly like the cancellation tests.
"""

import asyncio
import types

from app.services.execution_registry import ExecutionRegistry
from app.services.langgraph_servicer import LangGraphServiceServicer


class _FakeGraph:
    """Yields the node updates the scenario needs, then ends the run."""

    def __init__(self, node_updates):
        self._node_updates = node_updates

    async def aget_state(self, config):
        return types.SimpleNamespace(next=[], values={})

    async def astream(self, stream_input, config, stream_mode):
        yield self._node_updates


def _build_servicer(graph):
    servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)
    servicer._active_runs = ExecutionRegistry()
    # Read by the attachment guard in ExecuteGraph. Empty means "this process has built
    # no definition", which the guard treats as "the answer is unknown" and stays quiet.
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


def _run(node_updates):
    async def scenario():
        servicer = _build_servicer(_FakeGraph(node_updates))
        events = []
        await asyncio.wait_for(
            _collect(servicer.ExecuteGraph(_request(), _grpc_context()), events), timeout=5
        )
        return events

    return asyncio.run(scenario())


def test_a_node_error_rides_its_node_end_frame():
    events = _run({
        "node_doc": {
            "output": "Document node found no 'doc1_ref' in the run context.",
            "error": "missing_context:doc1_ref",
            "total_tokens": 0,
        }
    })

    frame = next(event for event in events if event.event_type == "NODE_END")
    assert frame.node_id == "node_doc"
    assert frame.error_message == "missing_context:doc1_ref"
    # The stream itself still ends cleanly: the error is reported, not raised.
    assert [r.event_type for r in events][-1] == "END"


def test_a_clean_node_frame_carries_no_error():
    events = _run({"node_a": {"output": "hello", "total_tokens": 5}})

    frame = next(event for event in events if event.event_type == "NODE_END")
    assert frame.error_message == ""
