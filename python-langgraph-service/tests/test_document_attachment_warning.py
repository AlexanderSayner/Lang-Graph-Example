"""A run that names a document no node in the graph will read.

An attachment reaches a model only through a DOCUMENT node. When the graph has none - or
none that reads the key the caller used - the upload, the ownership gate and the run itself
all succeed and only the reading step is missing, so the model answers that it has no
access to the file. That failure is silent by construction, which is what these tests pin:
the engine must say so, at the one place that knows both halves - the run's context and the
definition it is about to execute.

The guard warns rather than refuses: attaching now and using the file in a later message is
legitimate, and a warning a caller cannot see is not a reason to break their run.

No gRPC server, Postgres or Redis is required - the servicer is built with ``__new__`` and
only the collaborators ``ExecuteGraph`` uses are stubbed.
"""

import asyncio
import types

from app.models import EdgeDefinition, GraphDefinition, NodeDefinition
from app.services.execution_registry import ExecutionRegistry
from app.services.langgraph_servicer import (
    LangGraphServiceServicer,
    _split_run_context,
    document_ref_keys_of,
    document_slots_of,
)


def definition(*nodes):
    """A minimal definition from ``(node_id, node_type[, metadata])`` tuples."""
    parsed = []
    for node in nodes:
        node_id, node_type, *rest = node
        parsed.append(NodeDefinition(node_id=node_id, node_type=node_type,
                                     handler_name="h", metadata=rest[0] if rest else {}))
    return GraphDefinition(name="g", nodes=parsed,
                           edges=[EdgeDefinition(source="start", target="end")])


# --- The definition side -------------------------------------------------------------------


def test_a_graph_without_a_document_node_reads_nothing():
    graph = definition(("start", "START"), ("act", "ACTION"), ("end", "END"))

    assert document_ref_keys_of(graph) == set()


def test_a_document_node_reads_the_contract_default_key():
    graph = definition(("start", "START"), ("doc", "DOCUMENT"))

    assert document_ref_keys_of(graph) == {"document_ref"}


def test_a_document_node_may_read_a_key_of_its_own():
    graph = definition(("doc", "DOCUMENT", {"document_ref_key": "invoice_file"}))

    assert document_ref_keys_of(graph) == {"invoice_file"}


def test_a_blank_key_name_falls_back_to_the_default():
    # The node itself treats a blank name as the default (an empty key could never match a
    # context entry), so this must report the same thing the node would read.
    graph = definition(("doc", "DOCUMENT", {"document_ref_key": "   "}))

    assert document_ref_keys_of(graph) == {"document_ref"}


def test_the_key_is_only_reported_for_nodes_that_actually_read_documents():
    # Metadata on a node of another type is not an invitation: only a DOCUMENT node reads
    # a document reference.
    graph = definition(("act", "ACTION", {"document_ref_key": "invoice_file"}))

    assert document_ref_keys_of(graph) == set()


def test_a_subgraph_makes_the_answer_unknown():
    # A GRAPH node runs a second definition, which may well hold the DOCUMENT node that
    # reads the key. "I cannot see it" is not "there is none", so the answer is None.
    graph = definition(("start", "START"), ("child", "GRAPH", {"subgraph_id": "inner"}))

    assert document_ref_keys_of(graph) is None


# --- The run side --------------------------------------------------------------------------


class _FakeGraph:
    """Stands in for a compiled graph: emits one node update and ends."""

    async def aget_state(self, config):
        return types.SimpleNamespace(next=[], values={})

    async def astream(self, stream_input, config, stream_mode):
        yield {"act": {"output": "ok", "total_tokens": 0}}


def _servicer(readable_keys, graph_id="g1"):
    """A servicer with only what ``ExecuteGraph`` touches, plus the guard's own cache."""
    servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)
    servicer._active_runs = ExecutionRegistry()
    servicer._document_ref_keys = {graph_id: readable_keys}

    async def _load(_graph_id):
        return _FakeGraph()

    servicer._load_and_compile_graph = _load
    return servicer


async def _run(servicer, context):
    request = types.SimpleNamespace(graph_id="g1", thread_id="t1", input="go", context=context)

    async def _abort(code, details):  # pragma: no cover - only reached on a rejected run
        raise AssertionError(f"unexpected abort: {code} {details}")

    events = []
    async for event in servicer.ExecuteGraph(request, types.SimpleNamespace(abort=_abort)):
        events.append(event)
    return events


def test_a_reference_no_node_reads_is_warned_about(caplog):
    async def scenario():
        with caplog.at_level("WARNING", logger="app.services.langgraph_servicer"):
            await _run(_servicer({"invoice_file"}), {"document_ref": "ui-123"})

    asyncio.run(scenario())

    assert "no DOCUMENT node" in caplog.text
    assert "g1" in caplog.text


def test_a_run_that_reads_its_reference_says_nothing(caplog):
    async def scenario():
        with caplog.at_level("WARNING", logger="app.services.langgraph_servicer"):
            await _run(_servicer({"document_ref"}), {"document_ref": "ui-123"})

    asyncio.run(scenario())

    assert "no DOCUMENT node" not in caplog.text


def test_a_run_with_no_attachment_says_nothing(caplog):
    async def scenario():
        with caplog.at_level("WARNING", logger="app.services.langgraph_servicer"):
            await _run(_servicer(set()), {"tenant": "orchestra"})

    asyncio.run(scenario())

    assert "no DOCUMENT node" not in caplog.text


def test_an_unknown_definition_never_guesses(caplog):
    """The definition was evicted (or never built here): say nothing rather than lie."""
    servicer = _servicer({"document_ref"})
    servicer._document_ref_keys = {}

    async def scenario():
        with caplog.at_level("WARNING", logger="app.services.langgraph_servicer"):
            await _run(servicer, {"document_ref": "ui-123"})

    asyncio.run(scenario())

    assert "no DOCUMENT node" not in caplog.text


def test_the_run_still_completes_when_it_warns(caplog):
    """A warning, not a refusal - the caller may be attaching for a later message."""

    async def scenario():
        with caplog.at_level("WARNING", logger="app.services.langgraph_servicer"):
            return await _run(_servicer(set()), {"document_ref": "ui-123"})

    events = asyncio.run(scenario())

    assert any(getattr(event, "event_type", "") == "NODE_END" for event in events)
    assert not any(getattr(event, "event_type", "") == "ERROR" for event in events)


def test_a_graph_with_a_subgraph_never_warns(caplog):
    """The subgraph may hold the DOCUMENT node; not seeing it is not seeing its absence."""
    servicer = _servicer(None)  # document_ref_keys_of() answers None for a GRAPH node

    async def scenario():
        with caplog.at_level("WARNING", logger="app.services.langgraph_servicer"):
            await _run(servicer, {"document_ref": "ui-123"})

    asyncio.run(scenario())

    assert "no DOCUMENT node" not in caplog.text


# --- Several slots -----------------------------------------------------------------------
#
# A graph may read more than one document in one run. Then the guard has two jobs: report a
# reference under a key nothing reads, and report a slot the run never filled - the two-file
# case, where the second DOCUMENT node stops on missing_context and the run silently loses
# half its input.


def test_the_slots_of_a_graph_are_keyed_by_their_reference_key():
    graph = definition(
        ("start", "START"),
        ("doc_a", "DOCUMENT", {"document_ref_key": "timesheets_ref", "output_variable": "timesheets_text"}),
        ("doc_b", "DOCUMENT", {"document_ref_key": "report_ref", "output_variable": "report_text"}),
    )

    assert document_slots_of(graph) == {
        "timesheets_ref": "timesheets_text",
        "report_ref": "report_text",
    }


def test_a_graph_with_one_default_slot_is_reported_as_such():
    graph = definition(("doc", "DOCUMENT"))

    assert document_slots_of(graph) == {"document_ref": "document_text"}


def test_a_reference_under_an_unknown_key_is_still_caught(caplog):
    """A real handle under a key the graph does not read: exactly the silent failure."""
    # Shaped the way DocumentService mints ids, so the shape test in the guard sees a handle.
    real_id = "ui-9aa20ce6-" + "a" * 32

    async def scenario():
        with caplog.at_level("WARNING", logger="app.services.langgraph_servicer"):
            await _run(_servicer({"document_ref"}), {"timesheets_ref": real_id})

    asyncio.run(scenario())

    assert "no DOCUMENT node" in caplog.text
    assert "timesheets_ref" in caplog.text


def test_a_half_filled_graph_is_warned_about(caplog):
    """The run attached one file to a graph that reads two - the second node will stop."""
    servicer = _servicer({"timesheets_ref": "timesheets_text",
                          "report_ref": "report_text"})

    async def scenario():
        with caplog.at_level("WARNING", logger="app.services.langgraph_servicer"):
            await _run(servicer, {"timesheets_ref": "ui-123"})

    asyncio.run(scenario())

    assert "report_ref" in caplog.text
    assert "missing_context" in caplog.text


def test_a_fully_filled_graph_is_silent(caplog):
    servicer = _servicer({"timesheets_ref": "timesheets_text",
                          "report_ref": "report_text"})

    async def scenario():
        with caplog.at_level("WARNING", logger="app.services.langgraph_servicer"):
            await _run(servicer, {"timesheets_ref": "ui-a", "report_ref": "ui-b"})

    asyncio.run(scenario())

    assert "missing_context" not in caplog.text


def test_an_ordinary_context_value_is_not_mistaken_for_a_reference(caplog):
    """A graph that passes its own context keys must never be warned about."""
    servicer = _servicer(set())  # no DOCUMENT nodes at all

    async def scenario():
        with caplog.at_level("WARNING", logger="app.services.langgraph_servicer"):
            await _run(servicer, {"tenant": "orchestra", "period": "2026-Q1"})

    asyncio.run(scenario())

    assert "no DOCUMENT node" not in caplog.text


def test_an_attachment_free_run_of_a_slot_graph_says_nothing(caplog):
    """Attaching later is legitimate; a run that names nothing is not a mistake."""
    servicer = _servicer({"timesheets_ref": "timesheets_text"})

    async def scenario():
        with caplog.at_level("WARNING", logger="app.services.langgraph_servicer"):
            await _run(servicer, {"tenant": "orchestra"})

    asyncio.run(scenario())

    assert "missing_context" not in caplog.text


# --- The keys a prompt must hide -----------------------------------------------------------
#
# A slot may name its reference key whatever it likes. Hiding only the default spelling would
# put the custom one straight into `=== STATIC CONTEXT ===`, where the model quotes it back in
# an answer about not being able to open the file.


def test_a_client_cannot_widen_what_a_prompt_hides():
    """The set is resolved from the definition, so it is a run key and never a client one."""
    state_context, configurable = _split_run_context({
        "tenant": "orchestra",
        "document_ref_keys": "llm_credentials_json,anything",
    })

    assert "document_ref_keys" not in state_context
    assert "document_ref_keys" not in configurable


def test_a_custom_slot_key_is_hidden_from_the_prompt():
    """The whole point of the run-level key set: a renamed handle is as useless as the default."""
    from app.graph_engine.node_handlers import (
        document_ref_keys_from_config,
        scrub_prompt_context,
    )

    config = {"configurable": {"document_ref_keys": "timesheets_ref,report_ref"}}
    hidden = document_ref_keys_from_config(config)

    scrubbed = scrub_prompt_context(
        {"timesheets_ref": "ui-aaaa1111-aaa", "report_ref": "ui-bbbb2222-bbb",
         "tenant": "orchestra"},
        hidden,
    )

    assert scrubbed == {"tenant": "orchestra"}


def test_an_unknown_run_still_hides_the_default_key():
    """A caller with no annotation (a test, a direct call) must not become leaky."""
    from app.graph_engine.node_handlers import (
        document_ref_keys_from_config,
        scrub_prompt_context,
    )

    assert document_ref_keys_from_config(None) == {"document_ref"}
    assert scrub_prompt_context({"document_ref": "ui-1", "tenant": "x"}, None) == {"tenant": "x"}

