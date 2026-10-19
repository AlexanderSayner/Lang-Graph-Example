import asyncio
import contextlib
import functools
import json
import logging
import time
from collections import defaultdict
from typing import Dict, Any, Callable

import grpc
import redis.asyncio as redis
from cachetools import LRUCache
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.constants import END
from langgraph.graph import StateGraph

from app.clients.java_client import JavaGraphClient
from app.clients.llm import LLMClientRegistry, resolve_llm_config
from app.clients.model_catalog import list_models
from app.config import settings
from app.copilot.copilot_agent import CopilotAgent
from app.generated import langgraph_pb2_grpc, langgraph_pb2
from app.graph_engine.graph_utils import parse_conditions, resolve_route, GraphState
from app.graph_engine.node_handlers import NodeHandler
from app.models import GraphDefinition
from app.services.execution_registry import ExecutionRegistry, run_key

logger = logging.getLogger(__name__)

# Placed on the event queue by the producer when the graph run is over. Distinct from any
# protobuf message so the consumer can tell "done" from a real event.
_STREAM_DONE = object()


# --- Decorators ---

def handle_grpc_errors(func: Callable):
    """Decorator to catch exceptions and map them to gRPC status codes."""

    @functools.wraps(func)
    async def wrapper(self, request, context):
        try:
            return await func(self, request, context)
        except ValueError as e:
            logger.warning(f"Validation error: {e}")
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        except KeyError as e:
            logger.warning(f"Resource not found: {e}")
            await context.abort(grpc.StatusCode.NOT_FOUND, str(e))
        except Exception as e:
            logger.error(f"Internal error in {func.__name__}: {e}", exc_info=True)
            await context.abort(grpc.StatusCode.INTERNAL, f"Internal server error: {e}")

    return wrapper


# --- Helpers ---

def _serialize_state(state: Dict[str, Any]) -> Dict[str, str]:
    """Converts state values to strings for Protobuf map<string, string> compatibility."""

    def _to_proto_str(v):
        if v is None:
            return "null"
        if isinstance(v, bool):  # ⚠️ Check BEFORE int (bool is subclass of int in Python)
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, str):
            return v
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    return {str(k): _to_proto_str(v) for k, v in state.items()}


def _normalize_graph_definition(raw_graph_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Normalizes camelCase from Java/Proto to snake_case for Pydantic validation."""
    return {
        "name": raw_graph_dict.get("graphName") or raw_graph_dict.get("name", ""),
        "nodes": [
            {
                "node_id": n.get("nodeId") or n.get("node_id", ""),
                "node_type": n.get("nodeType") or n.get("node_type", ""),
                "handler_name": n.get("handlerName") or n.get("handler_name", ""),
                "metadata": n.get("metadata") or {}
            }
            for n in raw_graph_dict.get("nodes", [])
        ],
        "edges": [
            {
                "source": e.get("source", ""),
                "target": e.get("target", ""),
                "condition": e.get("condition")
            }
            for e in raw_graph_dict.get("edges", [])
        ],
        "config": raw_graph_dict.get("config") or {}
    }


# --- Servicer ---
class LangGraphServiceServicer(langgraph_pb2_grpc.LangGraphServiceServicer):
    """Async gRPC service implementation for LangGraph operations."""

    def __init__(self, checkpointer: Any, java_channel: grpc.aio.Channel, redis_client: redis.Redis):
        self.java_client = JavaGraphClient(java_channel)
        self.redis_client = redis_client

        max_cache_size = getattr(settings, "MAX_CACHED_GRAPHS", 100)
        self._compiled_graphs: LRUCache = LRUCache(maxsize=max_cache_size)
        self._compile_lock = asyncio.Lock()

        self._checkpointer = checkpointer

        # Multi-provider LLM layer (yandex / openai / groq / deepseek / openrouter / anthropic).
        # The registry builds and caches one client per distinct config. Clients are resolved
        # LAZILY (LazyLLMClient): nothing is constructed at startup or at graph compile time,
        # so a missing API key can never block the server or a build - it surfaces when the
        # node first calls the model.
        self._llm_registry = LLMClientRegistry()

        # The copilot is not a graph node, so it uses COPILOT_* or the service default.
        # CopilotAgent accepts a ready client; the lazy wrapper defers the provider build
        # until the first question.
        self.copilot = CopilotAgent(
            self._llm_registry.lazy(resolve_llm_config(
                {}, {"llm_provider": settings.COPILOT_PROVIDER, "llm_model": settings.COPILOT_MODEL}
            ))
        )

        # Pass the unified loader to NodeHandler for subgraph resolution. Nodes resolve their
        # own client at compile time (see _build_langgraph); this is only the fallback.
        self.node_handler = NodeHandler(
            self._load_and_compile_graph,
            default_llm_factory=lambda: self._llm_registry.lazy(resolve_llm_config({}, {})),
        )

        # Live ExecuteGraph streams register here so a runaway run can be cancelled
        # (CancelGraphExecution) without waiting for the node/LLM call to finish.
        self._active_runs = ExecutionRegistry()

    async def _load_and_compile_graph(self, graph_id: str) -> Runnable:
        """Unified method to fetch, normalize, and compile a graph (prevents thundering herd)."""
        if graph_id in self._compiled_graphs:
            return self._compiled_graphs[graph_id]

        async with self._compile_lock:
            # Double-check after acquiring lock
            if graph_id in self._compiled_graphs:
                return self._compiled_graphs[graph_id]

            # 1. Try Redis first
            cached_def = await self.redis_client.get(f"graph:def:{graph_id}")
            if cached_def:
                graph_dict = json.loads(cached_def)
            else:
                # 2. Fetch from Java (Source of Truth)
                raw_graph_dict = await self.java_client.get_graph_definition(graph_id)
                if not raw_graph_dict:
                    raise KeyError(f"Graph {graph_id} not found")

                graph_dict = _normalize_graph_definition(raw_graph_dict)

                # Cache in Redis for 1 hour
                await self.redis_client.setex(f"graph:def:{graph_id}", 3600, json.dumps(graph_dict))

            # 3. Validate and Compile
            validated = GraphDefinition(**graph_dict)
            compiled = self._build_langgraph(validated)

            # 4. Cache in memory
            self._compiled_graphs[graph_id] = compiled
            logger.info(f"Graph {graph_id} compiled and cached locally")
            return compiled

    def _build_langgraph(self, graph_data: GraphDefinition) -> Runnable:
        """Internal method to build and compile the graph."""
        workflow = StateGraph(GraphState)

        # --- 1. Pre-process Edges ---
        conditional_map = defaultdict(list)
        unconditional_edges = []

        for edge in graph_data.edges:
            if getattr(edge, "condition", None):
                conditional_map[edge.source].append((edge.target, edge.condition))
            else:
                unconditional_edges.append(edge)

        # --- 1b. Parse every condition once, at build time ---
        # A condition that cannot be parsed can never route, so it fails the build here (naming the
        # node) instead of being skipped on every evaluation where the only trace was a debug line.
        # Parsing once also means the router compares pre-parsed literals on every pass instead of
        # re-splitting condition strings while the graph is running.
        parsed_conditions = {
            source: parse_conditions(conditions, source)
            for source, conditions in conditional_map.items()
        }

        interrupt_nodes = [n.node_id for n in graph_data.nodes if n.node_type == "HUMAN"]

        # --- 2. Build Nodes ---
        for node in graph_data.nodes:
            node_id = node.node_id
            metadata = dict(node.metadata) if hasattr(node.metadata, 'items') else (node.metadata or {})
            is_router = node_id in conditional_map

            expected_keys = set()
            if is_router:
                # The same parse the router uses below - one build-time pass, one source of truth.
                for _, key, _ in parsed_conditions[node_id]:
                    expected_keys.add(str(key))

            # Resolve this node's LLM provider at compile time: node metadata beats graph
            # config beats the service default (app/clients/llm.py). resolve_llm_config runs
            # eagerly, so an unknown/typo'd provider still fails the build fast and loud.
            # The client itself is LAZY (LazyLLMClient): only nodes that really call the model
            # get one - router nodes always do, the rest only when they carry a system_prompt
            # (pass-through, HTTP_REQUEST and GRAPH nodes never touch the LLM) - and a missing API key
            # surfaces on the node's first model call instead of failing the whole build.
            node_type_val = node.node_type
            uses_llm = is_router or (
                node_type_val not in ("HTTP_REQUEST", "GRAPH") and bool(metadata.get("system_prompt"))
            )
            llm_client = (
                self._llm_registry.lazy(resolve_llm_config(metadata, graph_data.config))
                if uses_llm else None
            )

            # Factory function to safely capture loop variables (avoids late-binding closure issues)
            def make_handler(n_id=node_id, n_meta=metadata, n_type=node.node_type,
                             is_router_node=is_router, router_keys=expected_keys,
                             llm_client=llm_client):
                async def handler(state: GraphState, config: RunnableConfig) -> Dict[str, Any]:
                    logger.info(f"Executing node: {n_id} Type: {n_type}")
                    return await self.node_handler.process(
                        n_id=n_id,
                        n_meta=n_meta,
                        node_type=n_type,
                        is_router_node=is_router_node,
                        router_keys=router_keys,
                        state=state,
                        config=config,
                        llm=llm_client
                    )

                return handler

            workflow.add_node(node_id, make_handler())

        # --- 3. Add Unconditional Edges ---
        for edge in unconditional_edges:
            workflow.add_edge(edge.source, edge.target)

        # --- 4. Add Conditional Edges ---
        for source, conditions in parsed_conditions.items():
            def make_router(condition_list=conditions, src=source):
                def router(state: GraphState) -> str:
                    # %s formatting, not f-strings: this runs on every evaluation of every outgoing
                    # edge, so the message must not be built when nobody logs at INFO.
                    logger.info("Routing from %s...", src)
                    target = resolve_route(state.get("variables"), condition_list, state)
                    if target is not None:
                        logger.info("Router %s selected '%s'", src, target)
                        return target

                    # Ending the run rather than raising keeps the old contract (an undecided
                    # router must never hang a graph), but the failure is no longer silent: the
                    # conditions and the variables they were checked against go to the log, so a
                    # wrong condition can be spotted without diffing the final state by hand.
                    logger.warning(
                        f"No condition matched for node {src}. "
                        # Parsed key/literal pairs rather than the raw strings: this is exactly
                        # what the matcher compared against, so the log shows why it did not fire.
                        f"Conditions={[(key, expected) for _, key, expected in condition_list]}; "
                        f"variables={state.get('variables')}. "
                        f"Ending the run without executing a branch node."
                    )
                    return END

                return router

            path_map = {target: target for target, _, _ in conditions}
            path_map[END] = END
            workflow.add_conditional_edges(source, make_router(), path_map)

        # --- 5. Set Entry Point & Compile ---
        if not graph_data.nodes:
            raise ValueError("Graph definition has no nodes.")

        start_node = next((n for n in graph_data.nodes if n.node_type == "START"), None)
        if not start_node:
            raise ValueError("Graph definition has no node with type 'START'.")

        workflow.set_entry_point(start_node.node_id)

        return workflow.compile(
            checkpointer=self._checkpointer,
            interrupt_before=interrupt_nodes
        )

    @handle_grpc_errors
    async def BuildGraph(self, request, context):
        graph_id = request.graph_id

        nodes = [
            {
                "node_id": n.node_id,
                "node_type": n.node_type,
                "handler_name": n.handler_name,
                "metadata": dict(n.metadata) if hasattr(n.metadata, 'items') else {}
            }
            for n in request.nodes
        ]
        edges = [
            {"source": e.source, "target": e.target, "condition": e.condition or None}
            for e in request.edges
        ]

        graph_dict = {
            "name": request.graph_name,
            "nodes": nodes,
            "edges": edges,
            "config": dict(request.config) if hasattr(request.config, 'items') else {}
        }

        validated_graph_data = GraphDefinition(**graph_dict)
        compiled_graph = self._build_langgraph(validated_graph_data)

        # Update caches
        self._compiled_graphs[graph_id] = compiled_graph
        await self.redis_client.setex(f"graph:def:{graph_id}", 3600, json.dumps(graph_dict))
        await self.redis_client.publish("graph:invalidation", graph_id)

        return langgraph_pb2.BuildGraphResponse(
            success=True,
            graph_id=graph_id,
            message=f"Graph '{request.graph_name}' built successfully with {len(validated_graph_data.nodes)} nodes"
        )

    async def ExecuteGraph(self, request, context):
        """Streaming RPC: yields ExecuteGraphResponse messages as the graph runs."""
        graph_id = request.graph_id
        if not graph_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "graph_id is required")
            return  # Unreachable, but satisfies type checkers

        try:
            compiled_graph = await self._load_and_compile_graph(graph_id)
        except KeyError as e:
            await context.abort(grpc.StatusCode.NOT_FOUND, str(e))
            return
        except ValueError as e:
            # A stored definition the engine now rejects (e.g. a malformed edge condition).
            # INVALID_ARGUMENT carries the reason, which the gateway turns into a readable
            # message; INTERNAL would bury it behind a generic failure.
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
            return
        except Exception as e:
            logger.error(f"Failed to load graph {graph_id}: {e}", exc_info=True)
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to load graph: {e}")
            return

        config: RunnableConfig = {
            "configurable": {
                "thread_id": getattr(request, "thread_id", None) or "default_session"
            }
        }

        try:
            current_snapshot = await compiled_graph.aget_state(config)
        except Exception as e:
            logger.error("Failed to fetch graph state", exc_info=True)
            yield langgraph_pb2.ExecuteGraphResponse(
                event_type="ERROR",
                error_message=f"Failed to fetch state: {e}",
                timestamp=int(time.time() * 1000)
            )
            return

        try:
            next_nodes = getattr(current_snapshot, "next", None) or getattr(current_snapshot, "next_nodes", None) or []

            if next_nodes:
                logger.info(f"Resuming graph from node: {current_snapshot.next}")
                if getattr(request, "input", None):
                    await compiled_graph.aupdate_state(config, {"input": request.input}, as_node=None)
                stream_input = None
                start_event = None
            else:
                stream_input = {
                    "input": request.input,
                    "context": dict(request.context) if hasattr(request, "context") else {},
                    "timestamp": time.time()
                }
                start_event = langgraph_pb2.ExecuteGraphResponse(
                    event_type="START",
                    timestamp=int(time.time() * 1000),
                    state=_serialize_state(stream_input)
                )

            # --- Cancellation support ---
            # The LangGraph run is driven by a separate task that pushes events onto a queue.
            # Cancelling that task (see CancelGraphExecution) raises CancelledError inside
            # astream, which aborts the in-flight node and, through it, the awaiting LLM/HTTP
            # call - so a huge token burn stops immediately. The generator below then drains
            # the queue and reports a clean CANCELLED event instead of a crash.
            run_id = run_key(graph_id, config["configurable"]["thread_id"])
            queue: asyncio.Queue = asyncio.Queue()

            async def _produce():
                """Drive the graph and publish every event to the queue."""
                try:
                    if start_event is not None:
                        await queue.put(start_event)

                    async for event in compiled_graph.astream(stream_input, config=config, stream_mode="updates"):
                        for node_name, updates in event.items():
                            node_tokens = 0
                            if isinstance(updates, dict):
                                output_content = str(updates.get("output", ""))
                                serialized_state = _serialize_state(updates)
                                node_tokens = updates.get("total_tokens", 0)
                            else:
                                logger.warning(f"Node '{node_name}' returned non-dict update (type: {type(updates).__name__})")
                                output_content = str(updates)
                                serialized_state = _serialize_state({"raw_output": updates})

                            await queue.put(langgraph_pb2.ExecuteGraphResponse(
                                event_type="NODE_END",
                                node_id=node_name,
                                output=output_content,
                                state=serialized_state,
                                timestamp=int(time.time() * 1000),
                                total_tokens=node_tokens
                            ))

                    final_snapshot = await compiled_graph.aget_state(config)
                    final_next = getattr(final_snapshot, "next", None) or []
                    final_values = getattr(final_snapshot, "values", {}) or {}

                    if final_next:
                        waiting_node = final_next[0] if isinstance(final_next, (list, tuple)) and final_next else final_next
                        logger.info(f"Graph paused waiting for input at node: {waiting_node}")
                        await queue.put(langgraph_pb2.ExecuteGraphResponse(
                            event_type="WAITING_FOR_INPUT",
                            node_id=waiting_node,
                            output="Action required. Waiting for user input.",
                            state=_serialize_state(final_values),
                            timestamp=int(time.time() * 1000)
                        ))
                    else:
                        await queue.put(langgraph_pb2.ExecuteGraphResponse(
                            event_type="END",
                            output=str(final_values.get("output", "")),
                            state=_serialize_state(final_values),
                            timestamp=int(time.time() * 1000),
                            total_tokens=final_values.get("total_tokens", 0)
                        ))
                except asyncio.CancelledError:
                    logger.info(f"Graph run {run_id} was cancelled")
                    raise
                except Exception as exec_error:
                    logger.error(f"Execution error: {exec_error}", exc_info=True)
                    await queue.put(langgraph_pb2.ExecuteGraphResponse(
                        event_type="ERROR",
                        error_message=str(exec_error),
                        timestamp=int(time.time() * 1000)
                    ))
                finally:
                    # Always unblock the consumer, whatever ended the run.
                    queue.put_nowait(_STREAM_DONE)

            producer = asyncio.create_task(_produce())
            self._active_runs.register(run_id, producer)

            try:
                while True:
                    item = await queue.get()
                    if item is _STREAM_DONE:
                        break
                    yield item
            finally:
                cancelled = self._active_runs.is_cancelled(run_id)
                self._active_runs.unregister(run_id)
                if not producer.done():
                    producer.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await producer

            if cancelled:
                yield langgraph_pb2.ExecuteGraphResponse(
                    event_type="CANCELLED",
                    output="Execution cancelled by user.",
                    timestamp=int(time.time() * 1000)
                )

        except asyncio.CancelledError:
            # The RPC itself was torn down (e.g. the browser aborted the request). The inner
            # finally already cancelled the producer, so the run stops here too.
            raise
        except Exception as exec_error:
            logger.error(f"Execution error: {exec_error}", exc_info=True)
            yield langgraph_pb2.ExecuteGraphResponse(
                event_type="ERROR",
                error_message=str(exec_error),
                timestamp=int(time.time() * 1000)
            )

    @handle_grpc_errors
    async def CancelGraphExecution(self, request, context):
        """Ask the live ExecuteGraph stream for this (graph, thread) to stop immediately.

        Cancelling the driver task aborts the in-flight LangGraph run and, with it, the
        awaiting LLM/HTTP call, so token spend halts without waiting for the node to finish.
        The still-open ExecuteGraph stream then reports a clean CANCELLED event.
        """
        run_id = run_key(request.graph_id, request.thread_id)
        stopped = self._active_runs.request_cancel(run_id)

        if stopped:
            logger.info(f"Cancelled running graph {request.graph_id} (thread {request.thread_id})")
        else:
            logger.info(f"Cancel requested but no live run for {request.graph_id} (thread {request.thread_id})")

        return langgraph_pb2.CancelGraphExecutionResponse(
            cancelled=stopped,
            message="Execution cancelled" if stopped else "No active execution found for this graph/thread"
        )

    @handle_grpc_errors
    async def GetGraphState(self, request, context):
        compiled_graph = await self._load_and_compile_graph(request.graph_id)
        config = {"configurable": {"thread_id": request.thread_id or "default"}}
        current_state = await compiled_graph.aget_state(config)

        return langgraph_pb2.GetGraphStateResponse(
            success=True,
            state=_serialize_state(current_state.values),
            current_node=current_state.next[0] if current_state.next else "",
            node_history=[]
        )

    @handle_grpc_errors
    async def UpdateGraphState(self, request, context):
        compiled_graph = await self._load_and_compile_graph(request.graph_id)
        config = {"configurable": {"thread_id": request.thread_id or "default"}}

        await compiled_graph.aupdate_state(
            config,
            dict(request.state_updates),
            as_node=request.as_node or None
        )

        updated_snapshot = await compiled_graph.aget_state(config)
        return langgraph_pb2.UpdateGraphStateResponse(
            success=True,
            updated_state=_serialize_state(updated_snapshot.values)
        )

    @handle_grpc_errors
    async def GetExecutionHistory(self, request, context):
        compiled_graph = await self._load_and_compile_graph(request.graph_id)
        config = {"configurable": {"thread_id": request.thread_id or "default"}}
        history_list = []

        async for snapshot in compiled_graph.aget_state_history(config):
            state_values = snapshot.values or {}
            node_id = state_values.get("last_node")

            if not node_id and snapshot.next:
                node_id = f"Pending: {snapshot.next[0]}"
            elif not node_id:
                node_id = "start"

            history_items = state_values.get("history", [])
            tokens_used = 0
            if history_items and isinstance(history_items, list):
                last_item = history_items[-1]
                if isinstance(last_item, dict) and last_item.get("node") == node_id:
                    tokens_used = last_item.get("tokens_used", 0)

            history_list.append(langgraph_pb2.StateSnapshot(
                node_id=str(node_id),
                state_json=json.dumps(state_values, default=str),
                timestamp=str(snapshot.created_at) if snapshot.created_at else "",
                tokens_used=tokens_used,
                total_tokens=state_values.get("total_tokens", 0)
            ))

        history_list.reverse()
        return langgraph_pb2.GraphHistoryResponse(history=history_list)

    @handle_grpc_errors
    async def RewindGraph(self, request, context):
        compiled_graph = await self._load_and_compile_graph(request.graph_id)
        config = {"configurable": {"thread_id": request.thread_id}}

        try:
            target_values = json.loads(request.target_state_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid state JSON: {e}")

        await compiled_graph.aupdate_state(
            config,
            values=target_values,
            as_node=request.target_node_id or None
        )

        logger.info(f"Graph {request.graph_id} rewound successfully for thread {request.thread_id}")
        return langgraph_pb2.RewindGraphPayload(
            success=True,
            message="State rewound. Send a new message to continue."
        )

    @handle_grpc_errors
    async def AskCopilot(self, request, context):
        logger.info("Copilot request received.")
        # The UI can pick the copilot's model per request. An empty value means "inherit":
        # fall back to COPILOT_PROVIDER / COPILOT_MODEL, which themselves fall back to the
        # service-wide LLM_PROVIDER / LLM_MODEL default - the same chain the constructor used
        # to bake into the startup client. resolve_llm_config is cheap and the registry caches
        # one client per distinct config, so reusing providers across questions stays warm.
        client = self._llm_registry.lazy(resolve_llm_config(
            {},
            {"llm_provider": request.copilot_provider or settings.COPILOT_PROVIDER,
             "llm_model": request.copilot_model or settings.COPILOT_MODEL}
        ))
        ai_response = await self.copilot.ask(
            user_message=request.user_message,
            graph_context_json=request.graph_context_json,
            execution_history_json=request.execution_history_json,
            chat_history_json=request.copilot_chat_history_json,
            selected_node_id=request.selected_node_id,
            selected_edge_json=request.selected_edge_json,
            llm=client
        )

        return langgraph_pb2.CopilotResponse(
            success=True,
            ai_response=ai_response,
            total_tokens=ai_response.usage.get('total_tokens', -1) if hasattr(ai_response, 'usage') else -1
        )

    @handle_grpc_errors
    async def ListLlmModels(self, request, context):
        """List the models a provider offers, to populate the UI's model suggestions.

        Delegates to the model catalog, which never raises - an unknown provider, a missing
        key or a network error resolve to a curated fallback plus a message, so the UI can
        always render something and a user can still type an arbitrary model id by hand.
        """
        result = await list_models(request.provider)
        return langgraph_pb2.ListLlmModelsResponse(
            success=result.success,
            models=[langgraph_pb2.LlmModel(id=model_id, label=label) for model_id, label in result.models],
            source=result.source,
            error_message=result.error_message,
        )

    @property
    def compiled_graphs(self):
        return self._compiled_graphs
