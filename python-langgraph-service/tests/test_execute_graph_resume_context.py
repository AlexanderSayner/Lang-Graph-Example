"""A resumed turn may carry new context - which is the only way a document can arrive.

``ExecuteGraph`` has two entry shapes. A **fresh** run seeds ``context`` into the graph state; a
**resume** (the snapshot still names a next node, i.e. the graph paused at a ``HUMAN`` node) drives
the existing checkpoint instead. The resume used to apply only ``input`` and drop the caller's
``context`` entirely, so a graph that asks a person for two reports and then waits for them was
handed both handles on the resume and reported ``missing_context`` for files sitting in its own
request.

These three cases pin the rule: a resume **merges** what the caller sent into the state's existing
context, a resume with nothing to add leaves the context alone, and an input-less resume still
applies the documents.

No gRPC server, Postgres, Redis or LLM is required - the servicer is built with ``__new__`` and
only the collaborators ``ExecuteGraph`` uses are stubbed, exactly as in
``test_execute_graph_cancel.py``.
"""

import asyncio
import types

from app.services.execution_registry import ExecutionRegistry
from app.services.langgraph_servicer import LangGraphServiceServicer


class _PausedGraph:
    """A graph that is paused with a next node, and records every ``aupdate_state``."""

    def __init__(self, existing_context=None, next_nodes=("node_human",)):
        self.existing_context = dict(existing_context or {})
        self.next_nodes = list(next_nodes)
        self.updates = []
        self.stream_inputs = []
        # First read is the paused snapshot; every later read is the finished one, so the
        # "final snapshot" the producer takes after ``astream`` does not report the pause again.
        self._snapshots = [
            types.SimpleNamespace(next=list(self.next_nodes),
                                  values={"context": dict(self.existing_context), "input": "earlier"}),
            types.SimpleNamespace(next=[], values={"context": dict(self.existing_context),
                                                   "output": "done"}),
        ]

    async def aget_state(self, config):
        return self._snapshots.pop(0) if len(self._snapshots) > 1 else self._snapshots[0]

    async def aupdate_state(self, config, updates, as_node=None):
        self.updates.append((dict(updates), as_node))

    async def astream(self, stream_input, config, stream_mode):
        # A resume drives the checkpoint it already has: no fresh input is pushed in.
        self.stream_inputs.append(stream_input)
        yield {"node_after_human": {"output": "read the files", "total_tokens": 3}}


def _build_servicer(graph):
    servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)
    servicer._active_runs = ExecutionRegistry()
    # Read by the attachment guard in ExecuteGraph. Empty means "this process has built no
    # definition", which the guard treats as "the answer is unknown" and stays quiet.
    servicer._document_ref_keys = {}

    async def _load(_graph_id):
        return graph

    servicer._load_and_compile_graph = _load
    return servicer


def _request(context=None, input_text="hi"):
    return types.SimpleNamespace(graph_id="g1", thread_id="t1", input=input_text,
                                 context=dict(context or {}))


def _grpc_context():
    async def _abort(code, details):  # pragma: no cover - only hit on the error paths
        raise AssertionError(f"unexpected abort: {code} {details}")

    return types.SimpleNamespace(abort=_abort)


async def _collect(generator, events):
    async for response in generator:
        events.append(response)


def test_a_resume_merges_the_callers_documents_into_the_state():
    """The whole point: two handles arrive on the resume and both reach the graph's context."""
    existing = {"earlier_key": "kept"}
    graph = _PausedGraph(existing_context=existing)
    servicer = _build_servicer(graph)
    events = []

    asyncio.run(_collect(
        servicer.ExecuteGraph(
            _request(context={"doc1_ref": "handle-1", "doc2_ref": "handle-2"}),
            _grpc_context(),
        ),
        events,
    ))

    # A resume never re-seeds a fresh input; it drives the checkpoint.
    assert graph.stream_inputs == [None]
    assert len(graph.updates) == 1
    updates, as_node = graph.updates[0]
    assert as_node is None
    assert updates["input"] == "hi"
    # Merged, not replaced: the key from the earlier turn survives, and both new documents are in.
    assert updates["context"] == {
        "earlier_key": "kept",
        "doc1_ref": "handle-1",
        "doc2_ref": "handle-2",
    }
    # The turn itself completed, so the pause is gone.
    assert [r.event_type for r in events] == ["NODE_END", "END"]


def test_a_resume_with_nothing_to_add_leaves_the_context_alone():
    """A plain-text reply must not clear the documents an earlier turn put in the state."""
    existing = {"doc1_ref": "handle-1"}
    graph = _PausedGraph(existing_context=existing)
    servicer = _build_servicer(graph)
    events = []

    asyncio.run(_collect(
        servicer.ExecuteGraph(_request(context={}), _grpc_context()), events
    ))

    assert len(graph.updates) == 1
    updates, _ = graph.updates[0]
    # Only the input: `context` has no reducer, so assigning it would erase what is already there.
    assert updates == {"input": "hi"}
    assert "context" not in updates


def test_an_inputless_resume_still_applies_the_documents():
    """A reply that carries only a file (no words) still has to reach the graph."""
    graph = _PausedGraph()
    servicer = _build_servicer(graph)
    events = []

    asyncio.run(_collect(
        servicer.ExecuteGraph(
            _request(context={"doc1_ref": "handle-1"}, input_text=""),
            _grpc_context(),
        ),
        events,
    ))

    assert len(graph.updates) == 1
    updates, _ = graph.updates[0]
    assert "input" not in updates
    assert updates["context"] == {"doc1_ref": "handle-1"}
