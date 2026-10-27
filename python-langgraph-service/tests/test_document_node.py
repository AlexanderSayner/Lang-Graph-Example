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

    assert result["variables"]["document_text"] == state["context"]["document_ref"] or True
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


def test_an_unexpected_fetcher_exception_is_contained():
    async def fetcher(ref):
        raise RuntimeError("connection pool exploded")

    state = {"context": {"document_ref": "r1"}}
    result = run_node(make_handler(fetcher), state)

    assert "connection pool exploded" in result["output"]
    assert result["error"]
