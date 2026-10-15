"""End-to-end tests for graph-execution cancellation.

These drive the real ``ExecuteGraph`` async generator against a fake compiled graph, then call
``CancelGraphExecution`` while the "node" (a stand-in for a huge LLM call) is still in flight.
They prove the two things the feature promises: the in-flight node is aborted, and the stream
reports a clean ``CANCELLED`` event instead of an ERROR or a crash.

No gRPC server, Postgres, Redis or LLM is required - the servicer is built with ``__new__`` and
only the two collaborators ``ExecuteGraph``/``CancelGraphExecution`` use are stubbed.
"""

import asyncio
import types

from app.services.execution_registry import ExecutionRegistry
from app.services.langgraph_servicer import LangGraphServiceServicer


class _FakeGraph:
    """Stands in for a compiled LangGraph graph.

    When ``block_forever`` is set, ``astream`` hangs on an await (a long-running LLM node)
    until it is cancelled, and records that the cancellation reached it - that is the signal
    that the token burn actually stopped. Otherwise it emits a single node update and ends,
    so the normal-completion path can be asserted.
    """

    def __init__(self, block_forever=False):
        self.block_forever = block_forever
        self.started = asyncio.Event()
        self.cancelled_inside_node = False

    async def aget_state(self, config):
        return types.SimpleNamespace(next=[], values={})

    async def astream(self, stream_input, config, stream_mode):
        self.started.set()
        if self.block_forever:
            try:
                await asyncio.sleep(60)  # a stand-in for a huge in-flight LLM call
            except asyncio.CancelledError:
                self.cancelled_inside_node = True
                raise
        yield {"node_a": {"output": "hello", "total_tokens": 5}}


def _build_servicer(graph):
    servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)
    servicer._active_runs = ExecutionRegistry()

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


def test_cancel_aborts_the_in_flight_node_and_reports_cancelled():
    async def scenario():
        graph = _FakeGraph(block_forever=True)
        servicer = _build_servicer(graph)
        events = []

        consumer = asyncio.create_task(
            _collect(servicer.ExecuteGraph(_request(), _grpc_context()), events)
        )
        await asyncio.wait_for(graph.started.wait(), timeout=5)

        # Cancel while the node is still "thinking".
        result = await servicer.CancelGraphExecution(
            types.SimpleNamespace(graph_id="g1", thread_id="t1"), _grpc_context()
        )
        assert result.cancelled is True

        await asyncio.wait_for(consumer, timeout=5)

        # The stream ends with a clean CANCELLED event, not an ERROR, and the cancellation
        # propagated into the node (i.e. the LLM call was aborted, not left running).
        assert [r.event_type for r in events] == ["START", "CANCELLED"]
        assert graph.cancelled_inside_node is True

    asyncio.run(scenario())


def test_cancel_for_an_unknown_thread_is_not_an_error():
    async def scenario():
        servicer = _build_servicer(_FakeGraph())
        result = await servicer.CancelGraphExecution(
            types.SimpleNamespace(graph_id="g1", thread_id="nope"), _grpc_context()
        )
        assert result.cancelled is False

    asyncio.run(scenario())


def test_completed_run_reports_end_and_is_unregistered():
    async def scenario():
        graph = _FakeGraph(block_forever=False)
        servicer = _build_servicer(graph)
        events = []

        await asyncio.wait_for(
            _collect(servicer.ExecuteGraph(_request(), _grpc_context()), events), timeout=5
        )

        assert [r.event_type for r in events] == ["START", "NODE_END", "END"]
        # Once finished the run is gone, so a late cancel reports nothing to stop.
        late = await servicer.CancelGraphExecution(
            types.SimpleNamespace(graph_id="g1", thread_id="t1"), _grpc_context()
        )
        assert late.cancelled is False

    asyncio.run(scenario())


def test_client_disconnect_still_cancels_the_producer():
    """A browser abort (no explicit cancel RPC) must still stop the in-flight node.

    When the client goes away, gRPC closes the stream, which runs ExecuteGraph's ``finally``.
    That cleanup has to cancel the producer task so a huge LLM call does not keep burning
    tokens with nobody listening.
    """
    async def scenario():
        graph = _FakeGraph(block_forever=True)
        servicer = _build_servicer(graph)
        events = []

        stream = servicer.ExecuteGraph(_request(), _grpc_context())
        consumer = asyncio.create_task(_collect(stream, events))
        await asyncio.wait_for(graph.started.wait(), timeout=5)

        # Simulate the client disconnecting: gRPC stops iterating the stream, which throws
        # CancelledError into the generator's suspension point (queue.get). That must run the
        # finally block that cancels the producer.
        consumer.cancel()
        try:
            await asyncio.wait_for(consumer, timeout=5)
        except asyncio.CancelledError:
            pass

        # Cleanup cancelled the node, and the run is no longer registered.
        assert graph.cancelled_inside_node is True
        assert servicer._active_runs.request_cancel("g1:t1") is False

    asyncio.run(scenario())