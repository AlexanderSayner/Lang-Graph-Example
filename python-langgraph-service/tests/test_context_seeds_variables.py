"""A caller's ordinary context values must also be usable as graph VARIABLES.

`context` reaches a prompt and nothing else: the HTTP executor resolves `{{user_id}}` out of
`state_json` (which is `variables`, see `HttpRequestGrpcService`), and `format_state_context`
renders the variables table to every LLM node. A fan-out that hands each member's graph run a
member id and a period therefore had to have a model COPY those values into a URL by hand - and
a URL assembled from a quoted copy is how a per-member run fetches somebody else's rows.

The rule pinned here: on a **fresh** run the caller's ordinary values are seeded into
`variables` together with `context`; on a **resume** they are merged in beside the context the
checkpoint already holds (variables has a merge reducer, so nothing earlier is erased); blank
values and the document references a DOCUMENT node reads stay out of `variables`.
"""

import asyncio
import types

from app.services.execution_registry import ExecutionRegistry
from app.services.langgraph_servicer import LangGraphServiceServicer, seedable_variables


class _RecordingGraph:
    """A graph that records the state it was driven with; paused or finished on demand."""

    def __init__(self, next_nodes=(), existing=None):
        self.existing = dict(existing or {})
        self.updates = []
        self.stream_inputs = []
        self._snapshots = [
            types.SimpleNamespace(next=list(next_nodes), values=dict(self.existing)),
            types.SimpleNamespace(next=[], values={**self.existing, "output": "done"}),
        ]

    async def aget_state(self, config):
        return self._snapshots.pop(0) if len(self._snapshots) > 1 else self._snapshots[0]

    async def aupdate_state(self, config, updates, as_node=None):
        self.updates.append(dict(updates))

    async def astream(self, stream_input, config, stream_mode):
        self.stream_inputs.append(stream_input)
        yield {"node_1": {"output": "done", "total_tokens": 1}}


def _build_servicer(graph, document_ref_keys=None):
    servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)
    servicer._active_runs = ExecutionRegistry()
    servicer._document_ref_keys = {"g1": set(document_ref_keys or set())}

    async def _load(_graph_id):
        return graph

    servicer._load_and_compile_graph = _load
    return servicer


def _request(context=None, input_text="go"):
    return types.SimpleNamespace(graph_id="g1", thread_id="t1", input=input_text,
                                 context=dict(context or {}))


def _grpc_context():
    async def _abort(code, details):  # pragma: no cover - only hit on the error paths
        raise AssertionError(f"unexpected abort: {code} {details}")

    return types.SimpleNamespace(abort=_abort)


async def _collect(generator, events):
    async for response in generator:
        events.append(response)


def test_a_fresh_run_seeds_the_callers_values_into_variables():
    graph = _RecordingGraph()
    servicer = _build_servicer(graph)
    events = []

    asyncio.run(_collect(
        servicer.ExecuteGraph(
            _request(context={"user_id": "3693", "date_begin": "2026-10-01"}),
            _grpc_context(),
        ),
        events,
    ))

    stream_input = graph.stream_inputs[0]
    assert stream_input["context"] == {"user_id": "3693", "date_begin": "2026-10-01"}
    # The same values, readable by an HTTP template and the variables table of a prompt.
    assert stream_input["variables"] == {"user_id": "3693", "date_begin": "2026-10-01"}


def test_a_fresh_run_keeps_document_references_out_of_variables():
    """A handle is not data: in `variables` it would be rendered into every prompt."""
    graph = _RecordingGraph()
    servicer = _build_servicer(graph, document_ref_keys={"uploaded_ref"})
    events = []

    asyncio.run(_collect(
        servicer.ExecuteGraph(
            _request(context={"uploaded_ref": "b2b-abc", "user_id": "3693"}),
            _grpc_context(),
        ),
        events,
    ))

    stream_input = graph.stream_inputs[0]
    # The DOCUMENT node still reads it from context...
    assert stream_input["context"] == {"uploaded_ref": "b2b-abc", "user_id": "3693"}
    # ...but it never reaches the variables a model is shown or a URL is built from.
    assert stream_input["variables"] == {"user_id": "3693"}


def test_a_resume_merges_the_callers_values_into_variables():
    graph = _RecordingGraph(next_nodes=("node_human",), existing={"variables": {"found": "1"}})
    servicer = _build_servicer(graph)
    events = []

    asyncio.run(_collect(
        servicer.ExecuteGraph(
            _request(context={"user_id": "3981", "blank": "  "}), _grpc_context(),
        ),
        events,
    ))

    assert len(graph.updates) == 1
    updates = graph.updates[0]
    assert updates["context"] == {"user_id": "3981", "blank": "  "}
    # Only the non-blank value is lifted; the reducer then keeps `found` from the paused turn.
    assert updates["variables"] == {"user_id": "3981"}


def test_a_resume_with_nothing_seedable_adds_no_variables_update():
    graph = _RecordingGraph(next_nodes=("node_human",))
    servicer = _build_servicer(graph, document_ref_keys={"uploaded_ref"})
    events = []

    asyncio.run(_collect(
        servicer.ExecuteGraph(_request(context={"uploaded_ref": "b2b-abc"}), _grpc_context()),
        events,
    ))

    updates = graph.updates[0]
    assert "variables" not in updates
    # The reference itself still travels in context - a paused graph reads its file next step.
    assert updates["context"] == {"uploaded_ref": "b2b-abc"}


def test_seedable_variables_drops_blanks_and_hidden_keys():
    assert seedable_variables({"a": "1", "b": "", "c": None, "d": "  ", "e": 0}) == {"a": "1", "e": 0}
    assert seedable_variables({"x": "1", "uploaded_ref": "h"}, {"uploaded_ref"}) == {"x": "1"}
    assert seedable_variables(None) == {}
    assert seedable_variables({"a": "x"}, None) == {"a": "x"}
