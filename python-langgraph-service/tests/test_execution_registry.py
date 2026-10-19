"""Unit tests for the live-execution registry that backs graph cancellation.

Pure asyncio unit tests - no gRPC, Postgres, Redis, LLM or pytest-asyncio needed. They pin
down the behaviour the servicer relies on: a cancel reaches a live run, is remembered as a
user request, and a finished/unknown run reports "nothing to cancel" instead of raising.
The async bodies are driven with asyncio.run() to match the sync-only style of the rest of
the suite (pytest-asyncio is not a dependency).
"""

import asyncio

from app.services.execution_registry import ExecutionRegistry, run_key


def test_run_key_uses_thread_id_and_defaults():
    assert run_key("g1", "t1") == "g1:t1"
    # The servicer falls back to "default_session" for a missing thread id; the registry
    # key must match so a cancel for the default thread still finds the live run.
    assert run_key("g1", None) == "g1:default_session"
    assert run_key("g1", "") == "g1:default_session"


def test_request_cancel_stops_a_live_run():
    async def scenario():
        registry = ExecutionRegistry()
        started = asyncio.Event()

        async def _run():
            started.set()
            await asyncio.sleep(60)

        task = asyncio.create_task(_run())
        registry.register(run_key("g1", "t1"), task)
        await started.wait()

        assert registry.is_cancelled("g1:t1") is False
        assert registry.request_cancel("g1:t1") is True
        # request_cancel both flags it and cancels the underlying task.
        assert registry.is_cancelled("g1:t1") is True
        await asyncio.sleep(0)  # let the cancellation propagate
        assert task.cancelled()

    asyncio.run(scenario())


def test_request_cancel_for_unknown_key_is_false():
    async def scenario():
        registry = ExecutionRegistry()
        assert registry.request_cancel("missing:thread") is False
        # A failed cancel must not mark the key as cancelled.
        assert registry.is_cancelled("missing:thread") is False

    asyncio.run(scenario())


def test_request_cancel_for_finished_run_is_false():
    async def scenario():
        registry = ExecutionRegistry()

        async def _done():
            return "ok"

        task = asyncio.create_task(_done())
        await task
        registry.register(run_key("g1", "t1"), task)

        assert registry.request_cancel("g1:t1") is False
        assert registry.is_cancelled("g1:t1") is False

    asyncio.run(scenario())


def test_register_replaces_a_stale_run_for_the_same_key():
    async def scenario():
        registry = ExecutionRegistry()
        first_started = asyncio.Event()

        async def _first():
            first_started.set()
            await asyncio.sleep(60)

        async def _second():
            await asyncio.sleep(60)

        stale = asyncio.create_task(_first())
        registry.register(run_key("g1", "t1"), stale)
        await first_started.wait()

        fresh = asyncio.create_task(_second())
        registry.register(run_key("g1", "t1"), fresh)

        # The stale run is cancelled rather than leaked, and the fresh one is now live.
        await asyncio.sleep(0)
        assert stale.cancelled()
        assert registry.request_cancel("g1:t1") is True

        fresh.cancel()

    asyncio.run(scenario())


def test_unregister_forgets_the_run_and_flag():
    async def scenario():
        registry = ExecutionRegistry()

        async def _run():
            await asyncio.sleep(60)

        task = asyncio.create_task(_run())
        key = run_key("g1", "t1")
        registry.register(key, task)
        registry.request_cancel(key)

        registry.unregister(key)
        assert registry.is_cancelled(key) is False
        assert registry.request_cancel(key) is False

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
