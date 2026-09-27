import asyncio
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
from app.clients.yandex_client import YandexGPTClient
from app.config import settings
from app.copilot.copilot_agent import CopilotAgent
from app.generated import langgraph_pb2_grpc, langgraph_pb2
from app.graph_engine.graph_utils import parse_condition, GraphState
from app.graph_engine.node_handlers import NodeHandler
from app.models import GraphDefinition

logger = logging.getLogger(__name__)


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
        self._llm_client = YandexGPTClient(api_key=settings.YC_API_KEY, folder_id=settings.YC_FOLDER_ID)
        self.copilot = CopilotAgent(self._llm_client)

        # Pass the unified loader to NodeHandler for subgraph resolution
        self.node_handler = NodeHandler(self._llm_client, self._load_and_compile_graph)

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

        interrupt_nodes = [n.node_id for n in graph_data.nodes if n.node_type == "HUMAN"]

        # --- 2. Build Nodes ---
        for node in graph_data.nodes:
            node_id = node.node_id
            metadata = dict(node.metadata) if hasattr(node.metadata, 'items') else (node.metadata or {})
            is_router = node_id in conditional_map

            expected_keys = set()
            if is_router:
                for _, cond_str in conditional_map[node_id]:
                    key, _ = parse_condition(cond_str)
                    if key:
                        expected_keys.add(str(key))

            # Factory function to safely capture loop variables (avoids late-binding closure issues)
            def make_handler(n_id=node_id, n_meta=metadata, n_type=node.node_type,
                             is_router_node=is_router, router_keys=expected_keys):
                async def handler(state: GraphState, config: RunnableConfig) -> Dict[str, Any]:
                    logger.info(f"Executing node: {n_id} Type: {n_type}")
                    return await self.node_handler.process(
                        n_id=n_id,
                        n_meta=n_meta,
                        node_type=n_type,
                        is_router_node=is_router_node,
                        router_keys=router_keys,
                        state=state,
                        config=config
                    )

                return handler

            workflow.add_node(node_id, make_handler())

        # --- 3. Add Unconditional Edges ---
        for edge in unconditional_edges:
            workflow.add_edge(edge.source, edge.target)

        # --- 4. Add Conditional Edges ---
        for source, conditions in conditional_map.items():
            def make_router(condition_list=conditions, src=source):
                def router(state: GraphState) -> str:
                    logger.info(f"Routing from {src}...")
                    for target, condition_str in condition_list:
                        parsed_key, expected_val = parse_condition(condition_str)
                        if not parsed_key:
                            continue

                        vars_dict = state.get("variables", {}) or {}
                        current_val = vars_dict.get(parsed_key)
                        if current_val is None and parsed_key in state:
                            current_val = state[parsed_key]

                        if isinstance(expected_val, (int, float)) and isinstance(current_val, (int, float)):
                            match = current_val == expected_val
                        else:
                            match = str(current_val).strip() == str(expected_val).strip()

                        logger.debug(
                            f"Router Check: Key='{parsed_key}', Expected='{expected_val}', Actual='{current_val}', Match='{match}'")
                        if match:
                            return target

                    logger.warning(f"No condition matched for node {src}. Ending.")
                    return END

                return router

            path_map = {target: target for target, _ in conditions}
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
            else:
                stream_input = {
                    "input": request.input,
                    "context": dict(request.context) if hasattr(request, "context") else {},
                    "timestamp": time.time()
                }
                yield langgraph_pb2.ExecuteGraphResponse(
                    event_type="START",
                    timestamp=int(time.time() * 1000),
                    state=_serialize_state(stream_input)
                )

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

                    yield langgraph_pb2.ExecuteGraphResponse(
                        event_type="NODE_END",
                        node_id=node_name,
                        output=output_content,
                        state=serialized_state,
                        timestamp=int(time.time() * 1000),
                        total_tokens=node_tokens
                    )

            final_snapshot = await compiled_graph.aget_state(config)
            final_next = getattr(final_snapshot, "next", None) or []
            final_values = getattr(final_snapshot, "values", {}) or {}

            if final_next:
                waiting_node = final_next[0] if isinstance(final_next, (list, tuple)) and final_next else final_next
                logger.info(f"Graph paused waiting for input at node: {waiting_node}")
                yield langgraph_pb2.ExecuteGraphResponse(
                    event_type="WAITING_FOR_INPUT",
                    node_id=waiting_node,
                    output="Action required. Waiting for user input.",
                    state=_serialize_state(final_values),
                    timestamp=int(time.time() * 1000)
                )
            else:
                yield langgraph_pb2.ExecuteGraphResponse(
                    event_type="END",
                    output=str(final_values.get("output", "")),
                    state=_serialize_state(final_values),
                    timestamp=int(time.time() * 1000),
                    total_tokens=final_values.get("total_tokens", 0)
                )

        except Exception as exec_error:
            logger.error(f"Execution error: {exec_error}", exc_info=True)
            yield langgraph_pb2.ExecuteGraphResponse(
                event_type="ERROR",
                error_message=str(exec_error),
                timestamp=int(time.time() * 1000)
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
        ai_response = await self.copilot.ask(
            user_message=request.user_message,
            graph_context_json=request.graph_context_json,
            execution_history_json=request.execution_history_json,
            chat_history_json=request.copilot_chat_history_json,
            selected_node_id=request.selected_node_id,
            selected_edge_json=request.selected_edge_json
        )

        return langgraph_pb2.CopilotResponse(
            success=True,
            ai_response=ai_response,
            total_tokens=ai_response.usage.get('total_tokens', -1) if hasattr(ai_response, 'usage') else -1
        )

    @property
    def compiled_graphs(self):
        return self._compiled_graphs
