"""Tests for the DOCUMENT node's behaviour inside the engine.

The parser itself is covered separately; what is pinned here is the node's CONTRACT with the
rest of the graph: where the reference comes from, what it writes into state, and what it
does with the three ways a document can fail. The most important assertion is a negative
one - the raw bytes must never reach GraphState, or they would be checkpointed to Postgres
on every step and rendered wholesale into the next node's prompt.
"""

import io

import grpc
import openpyxl
import pytest

from app.graph_engine.node_handlers import NodeHandler


def make_xlsx_bytes(rows, headers=("col_a", "col_b")):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def make_handler(fetcher=None):
    """A handler with no LLM and no channels - this node must never touch either."""
    return NodeHandler(graph_loader=None, document_fetcher=fetcher)


def run_node(handler, state, metadata=None):
    import asyncio

    async def _run():
        return await handler._process_document("doc1", metadata or {}, state)

    return asyncio.run(_run())


# --- The happy path -------------------------------------------------------------------


def test_extracted_text_lands_in_variables_not_in_the_raw_state():
    payload = make_xlsx_bytes([("alice", 1), ("bob", 2)])

    async def fetcher(ref):
        return payload, "people.xlsx", "application/octet-stream"

    state = {"input": "summarise", "context": {"document_ref": "tenant:abc-123"}}
    result = run_node(make_handler(fetcher), state)

    # The extracted TEXT is stored, never the opaque id the run named: `variables` is what a
    # downstream prompt sees, so copying the reference in there would be useless at best. The
    # assertion below used to read `... == state["context"]["document_ref"] or True`, which is
    # a comparison that can never fail - it pinned nothing at all.
    assert result["variables"]["document_text"] != state["context"]["document_ref"]
    assert "alice" in result["variables"]["document_text"]
    meta = result["variables"]["document_meta"]
    assert meta["document_ref"] == "tenant:abc-123"
    assert meta["filename"] == "people.xlsx"
    assert meta["kind"] == "xlsx"
    assert meta["truncated"] is False
    assert meta["stats"]["rows_read"] == 3  # header + alice + bob
    # The node calls no model, so it must not claim a token spend.
    assert result["total_tokens"] == 0
    assert result["llm_billing"] == ""


def test_the_node_never_writes_the_document_bytes_into_state():
    payload = make_xlsx_bytes([("secret", 1)])

    async def fetcher(ref):
        return payload, "s.xlsx", "application/octet-stream"

    state = {"input": "go", "context": {"document_ref": "r1"}}
    result = run_node(make_handler(fetcher), state)

    # The ZIP magic ("PK") identifies the raw document; it must not appear anywhere in the
    # update, which is what gets merged into GraphState and checkpointed.
    assert payload[:2] == b"PK"  # the fixture really is a zip
    assert "PK" not in repr(result)
    assert "content" not in result["variables"]["document_meta"]


def test_the_reference_key_is_configurable_per_node():
    payload = make_xlsx_bytes([("x", 1)])

    async def fetcher(ref):
        assert ref == "custom-ref-value"
        return payload, "a.xlsx", "application/octet-stream"

    state = {"context": {"invoice_file": "custom-ref-value"}}
    result = run_node(make_handler(fetcher), state, {"document_ref_key": "invoice_file"})

    assert result["variables"]["document_meta"]["document_ref"] == "custom-ref-value"


def test_node_metadata_overrides_the_size_caps():
    payload = make_xlsx_bytes([(f"row{i}", i) for i in range(30)])

    async def fetcher(ref):
        return payload, "big.xlsx", "application/octet-stream"

    state = {"context": {"document_ref": "r1"}}
    result = run_node(
        make_handler(fetcher), state, {"max_rows_per_sheet": 3, "max_cell_chars": 4})

    meta = result["variables"]["document_meta"]
    assert meta["truncated"] is True
    assert meta["stats"]["rows_read"] == 3
    assert "row" in result["variables"]["document_text"]  # capped to "row"


# --- Failure paths --------------------------------------------------------------------


def test_a_missing_reference_is_a_node_error_not_a_crash():
    result = run_node(make_handler(), {"input": "go", "context": {}})

    assert result["error"] == "missing_context:document_ref"
    assert "no 'document_ref'" in result["output"]


def test_an_absent_context_map_is_treated_as_missing_too():
    result = run_node(make_handler(), {"input": "go"})

    assert result["error"] == "missing_context:document_ref"


def test_an_unconfigured_fetcher_fails_loudly():
    state = {"context": {"document_ref": "r1"}}

    result = run_node(make_handler(None), state)

    assert result["error"] == "Document Service Unavailable"


def test_a_grpc_fetch_failure_is_reported_with_its_code():
    async def failing(ref):
        raise grpc.aio.AioRpcError(grpc.StatusCode.NOT_FOUND, b"details", "doc gone")

    state = {"context": {"document_ref": "r1"}}
    result = run_node(make_handler(failing), state)

    assert "NOT_FOUND" in result["output"]
    assert result["error"]


def test_an_unparseable_document_is_a_node_error():
    async def fetcher(ref):
        return b"this is not a spreadsheet", "broken.xlsx", "application/octet-stream"

    state = {"context": {"document_ref": "r1"}}
    result = run_node(make_handler(fetcher), state)

    assert result["error"]
    assert "not a readable .xlsx or .docx" in result["output"]
    # A parse failure must not leave a bogus empty text for the next node to reason over.
    assert "variables" not in result


def test_an_unknown_document_reference_is_reported_readably():
    async def fetcher(ref):
        raise KeyError("Document b2b-abc not found in Java service")

    state = {"context": {"document_ref": "b2b-abc"}}
    result = run_node(make_handler(fetcher), state)

    assert result["error"] == "document_not_found:b2b-abc"
    assert "no longer available" in result["output"]


def test_an_unexpected_fetcher_exception_is_contained():
    async def fetcher(ref):
        raise RuntimeError("connection pool exploded")

    state = {"context": {"document_ref": "r1"}}
    result = run_node(make_handler(fetcher), state)

    assert "connection pool exploded" in result["output"]
    assert result["error"]


# --- Build-time guarantee ------------------------------------------------------------


def test_a_document_node_never_gets_an_llm_client():
    """The DOCUMENT node must not be able to bill a model, whatever metadata it carries.

    The whole feature is documented as "deterministic extraction, no tokens spent", so the
    guarantee has to hold even for a node somebody gave a `system_prompt` by copy-pasting an
    ACTION node: `uses_llm` must exclude DOCUMENT, exactly like HTTP_REQUEST and GRAPH.

    Asserted through the real `_build_langgraph`, because that is where the client is
    resolved - a test on `NodeHandler` alone would never see the decision at all.
    """
    from app.models import GraphDefinition, EdgeDefinition, NodeDefinition
    from app.services.langgraph_servicer import LangGraphServiceServicer

    resolved = []

    class RecordingRegistry:
        def lazy(self, config):
            resolved.append(config)
            return f"client-for-{config}"

    # __new__ skips __init__ (which wants a checkpointer, a channel and Redis); only the two
    # collaborators this path touches are needed.
    servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)
    servicer._llm_registry = RecordingRegistry()
    servicer._checkpointer = None

    graph = GraphDefinition(
        name="Invoice agent",
        nodes=[
            NodeDefinition(node_id="start", node_type="START", handler_name="h"),
            NodeDefinition(node_id="attach", node_type="DOCUMENT", handler_name="h",
                           metadata={"system_prompt": "MUST NOT REACH A MODEL"}),
            NodeDefinition(node_id="act", node_type="ACTION", handler_name="h",
                           metadata={"system_prompt": "summarise"}),
            NodeDefinition(node_id="end", node_type="END", handler_name="h"),
        ],
        edges=[
            EdgeDefinition(source="start", target="attach"),
            EdgeDefinition(source="attach", target="act"),
            EdgeDefinition(source="act", target="end"),
        ],
    )

    compiled = servicer._build_langgraph(graph)

    # A DOCUMENT node is a known type, so a graph containing one builds at all...
    assert compiled is not None
    # ...and exactly ONE client was resolved - the ACTION node's. The DOCUMENT node's own
    # system_prompt must not have bought it a model.
    assert len(resolved) == 1, (
        "only the ACTION node should resolve an LLM client; "
        f"got {len(resolved)} ({resolved})"
    )


# --- What a downstream model sees --------------------------------------------------------
#
# A DOCUMENT node's whole point is to put a file's TEXT in front of a model. What is pinned
# here is both halves of that: the text must arrive, and the opaque handle the run named
# must not. A model shown only the handle answers that it cannot open the file - which is
# the exact failure this feature exists to prevent.


def test_a_downstream_node_sees_the_text_and_never_the_handle():
    import asyncio
    import types

    payload = make_xlsx_bytes([("alice", 1)])
    seen = {}

    async def fetcher(ref):
        return payload, "people.xlsx", "application/octet-stream"

    class RecordingClient:
        """Stands in for the provider: records the prompt it was handed, answers plainly."""

        provider = "test"

        async def generate(self, user_message, system_message):
            seen["user"] = user_message
            seen["system"] = system_message
            return types.SimpleNamespace(content="alice has 1", usage={"total_tokens": 1})

    state = {
        "input": "explain the attachment",
        "context": {"document_ref": "ui-9aa20ce6-abc"},
        "variables": {},
        "history": [],
    }

    # The DOCUMENT node runs first, and its update is merged into the state exactly the way
    # the merge_dicts reducer does it, so the ACTION node below sees a real downstream state.
    state.update(run_node(make_handler(fetcher), state))

    handler = make_handler(fetcher)
    result = asyncio.run(handler._process_action(
        "act", {"system_prompt": "Describe the file."}, state, llm=RecordingClient()))

    assert result["output"] == "alice has 1"
    assert "alice" in seen["user"]  # the extracted text reached the model
    # ...and the handle did not, in either the context block or the variables block.
    assert "ui-9aa20ce6-abc" not in seen["user"]


# --- Slots: several documents in one run -------------------------------------------------
#
# One run may carry more than one file, and "which is which" has to survive the extraction.
# The mechanism is the node itself: each DOCUMENT node names its own reference key AND its own
# output variable, so the second extraction lands beside the first instead of overwriting it.
# These tests pin that contract, and the one rule it must never break - a node that declares
# nothing still writes exactly what it always wrote.


def test_a_slot_writes_where_it_was_told_to():
    payload = make_xlsx_bytes([("alice", 1)])

    async def fetcher(ref):
        assert ref == "r-timesheet"
        return payload, "timesheet.xlsx", "application/octet-stream"

    state = {"input": "go", "context": {"timesheets_ref": "r-timesheet"}}
    result = run_node(make_handler(fetcher), state, {
        "document_ref_key": "timesheets_ref",
        "output_variable": "timesheets_text",
    })

    assert "alice" in result["variables"]["timesheets_text"]
    assert "document_text" not in result["variables"], (
        "a renamed slot must not ALSO write the default, or a two-file graph would look "
        "like it had three"
    )
    # The metadata follows the text to a name of its own: `<output_variable>_meta` by default,
    # so a reader can tell which file a variable describes without a lookup table.
    meta = result["variables"]["timesheets_text_meta"]
    assert meta["document_ref"] == "r-timesheet"
    assert meta["filename"] == "timesheet.xlsx"
    assert "document_meta" not in result["variables"]


def test_a_meta_variable_may_be_named_explicitly():
    payload = make_xlsx_bytes([("bob", 2)])

    async def fetcher(ref):
        return payload, "b.xlsx", "application/octet-stream"

    state = {"input": "go", "context": {"document_ref": "r1"}}
    result = run_node(make_handler(fetcher), state, {"meta_variable": "report_meta"})

    assert "bob" in result["variables"]["document_text"]
    assert result["variables"]["report_meta"]["filename"] == "b.xlsx"


def test_two_slots_coexist_in_one_run():
    """The whole feature: two files, two variables, neither clobbers the other."""
    sheets = make_xlsx_bytes([("alice", 1)], headers=("name", "hours"))
    reports = make_xlsx_bytes([("alice", 3)], headers=("name", "hours"))

    async def fetcher(ref):
        return (sheets, "timesheet.xlsx", "application/octet-stream") if ref == "r-a" \
            else (reports, "report.xlsx", "application/octet-stream")

    handler = make_handler(fetcher)
    state = {"input": "find the discrepancy", "context": {"a_ref": "r-a", "b_ref": "r-b"}}

    def apply_slot(metadata):
        """Merge a node's update the way the merge_dicts reducer does - key by key."""
        update = run_node(handler, state, metadata)
        state["variables"] = {**state.get("variables", {}), **update["variables"]}

    apply_slot({"document_ref_key": "a_ref", "output_variable": "a_text"})
    apply_slot({"document_ref_key": "b_ref", "output_variable": "b_text"})

    assert "| alice | 1 |" in state["variables"]["a_text"]
    assert "| alice | 3 |" in state["variables"]["b_text"]
    assert state["variables"]["a_text_meta"]["filename"] == "timesheet.xlsx"
    assert state["variables"]["b_text_meta"]["filename"] == "report.xlsx"


def test_the_default_slot_is_unchanged():
    """Back-compat: a node that declares nothing writes the historical names, byte for byte."""
    payload = make_xlsx_bytes([("alice", 1)])

    async def fetcher(ref):
        return payload, "p.xlsx", "application/octet-stream"

    result = run_node(make_handler(fetcher), {"input": "go", "context": {"document_ref": "r1"}})

    assert set(result["variables"]) == {"document_text", "document_meta"}
    # The parser opens each sheet with its own heading; what must hold is the table itself,
    # byte for byte, because this is the layout every graph built before slots existed reads.
    assert "| col_a | col_b |\n| --- | --- |\n| alice | 1 |" in result["variables"]["document_text"]


def test_a_label_titles_its_own_text():
    payload = make_xlsx_bytes([("alice", 1)])

    async def fetcher(ref):
        return payload, "timesheet.xlsx", "application/octet-stream"

    state = {"input": "go", "context": {"timesheets_ref": "r-a"}}
    result = run_node(make_handler(fetcher), state, {
        "document_ref_key": "timesheets_ref",
        "output_variable": "timesheets_text",
        "label": "Time sheet (as submitted)",
    })

    text = result["variables"]["timesheets_text"]
    assert text.startswith("# Time sheet (as submitted)\n\n")
    assert "alice" in text
    # `chars` is what the PARSER produced, not the labelled text - the caps report the parse,
    # and a reader must not think a label changed the budget.
    assert result["variables"]["timesheets_text_meta"]["label"] == "Time sheet (as submitted)"
    assert result["variables"]["timesheets_text_meta"]["chars"] < len(text)
    assert "as 'Time sheet (as submitted)'" in result["output"]
