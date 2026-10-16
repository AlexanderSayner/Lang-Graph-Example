"""Registry of live graph executions, so a running graph can be cancelled.

A single Python replica can serve many concurrent ``ExecuteGraph`` streams. Every live
stream registers the task that drives the LangGraph run under a ``graph_id:thread_id``
key, and ``CancelGraphExecution`` flips that key to cancelled and cancels the task.

Cancelling the driver task is what actually stops the token burn: the ``CancelledError``
propagates into ``compiled_graph.astream(...)``, which aborts the in-flight node and,
through it, the awaiting LLM HTTP call. Nothing here needs a lock - gRPC's asyncio
server runs every handler on a single event loop, so all mutations happen cooperatively.

Keyed on ``graph_id:thread_id`` because that pair identifies a single logical
conversation/thread; running ``ExecuteGraph`` again for the same pair replaces the
previous registration (the engine serialises a given thread through its checkpointer).
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# The servicer falls back to this thread id when a request carries none; the registry
# mirrors that so a cancel for the "default" thread still reaches the running stream.
DEFAULT_THREAD_ID = "default_session"


def run_key(graph_id: str, thread_id: str | None) -> str:
    """Build the registry key for a (graph, thread) pair, matching the servicer's default."""
    return f"{graph_id}:{thread_id or DEFAULT_THREAD_ID}"


class ExecutionRegistry:
    """Tracks live ``ExecuteGraph`` runs by ``graph_id:thread_id``.

    ``register`` stores the driver task, ``request_cancel`` cancels it and remembers that
    the stop was user-requested (so the stream can report a clean CANCELLED event instead
    of a generic error), and ``unregister`` clears everything once the stream is done.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()

    def register(self, key: str, task: asyncio.Task) -> None:
        """Track ``task`` as the live run for ``key`` (replacing any older run for it)."""
        previous = self._tasks.get(key)
        if previous is not None and previous is not task and not previous.done():
            # Two streams for one thread should not overlap (the checkpointer serialises a
            # thread), but if they do, stop the stale one rather than leak it.
            logger.warning("Replacing an in-flight run for %s", key)
            previous.cancel()
        self._tasks[key] = task
        self._cancelled.discard(key)

    def request_cancel(self, key: str) -> bool:
        """Ask the live run for ``key`` to stop.

        Returns True when a live run was found and signalled, False when there was nothing
        to cancel (already finished, never started, or a duplicate click).
        """
        task = self._tasks.get(key)
        if task is None or task.done():
            logger.info("No live execution to cancel for %s", key)
            return False

        self._cancelled.add(key)
        task.cancel()
        logger.info("Cancellation requested for %s", key)
        return True

    def is_cancelled(self, key: str) -> bool:
        """Whether a cancel was requested for ``key`` during this run."""
        return key in self._cancelled

    def unregister(self, key: str) -> None:
        """Forget ``key`` once its stream has finished (or was cancelled)."""
        self._tasks.pop(key, None)
        self._cancelled.discard(key)