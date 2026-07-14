import asyncio
import functools
import json
import logging
import time
from typing import Dict, Any, Callable

import grpc
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


# --- Helper ---

def _serialize_state(state: Dict[str, Any]) -> Dict[str, str]:
    """
    Converts state values to strings for Protobuf map<string, string> compatibility.
    Preserves JSON semantics: True -> "true", None -> "null", etc.
    """

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


# --- Servicer ---
class LangGraphServiceServicer(langgraph_pb2_grpc.LangGraphServiceServicer):
    """Async gRPC service implementation for LangGraph operations."""

    def __init__(self, checkpointer: Any, java_channel: grpc.aio.Channel):
        # Initialize Java Client instead of Redis Store
        self.java_client = JavaGraphClient(java_channel)

        # LRU Cache for compiled graphs
        max_cache_size = getattr(settings, "MAX_CACHED_GRAPHS", 100)
        self._compiled_graphs = LRUCache(maxsize=max_cache_size)
        self._compile_lock = asyncio.Lock()

        self._checkpointer = checkpointer
        self._llm_client = YandexGPTClient(api_key=settings.YC_API_KEY, folder_id=settings.YC_FOLDER_ID)
        self.copilot = CopilotAgent(self._llm_client)

        async def graph_loader(graph_id: str):
            if graph_id in self._compiled_graphs:
                return self._compiled_graphs[graph_id]

            data = await self.java_client.get_graph_definition(graph_id)
            if not data:
                raise ValueError(f"Subgraph not found: {graph_id}")

            validated = GraphDefinition(**data)
            compiled = self._build_langgraph(validated)
            self._compiled_graphs[graph_id] = compiled
            return compiled

        self.node_handler = NodeHandler(self._llm_client, graph_loader)

    async def _get_compiled_graph(self, graph_id: str) -> Runnable:
        # Check in-memory cache first
        async with self._compile_lock:
            if graph_id in self._compiled_graphs:
                return self._compiled_graphs[graph_id]

            # Fetch from Java (Source of Truth)
            graph_dict = await self.java_client.get_graph_definition(graph_id)
            if not graph_dict:
                raise KeyError(f"Graph {graph_id} not found")

            validated = GraphDefinition(**graph_dict)
            compiled = self._build_langgraph(validated)

            # Cache for next time
            self._compiled_graphs[graph_id] = compiled
            logger.info(f"Graph {graph_id} compiled and cached")
            return compiled

    def _build_langgraph(self, graph_data: GraphDefinition) -> Runnable:
        """Internal method to build and compile the graph."""
        # Use GraphState TypedDict for type safety
        workflow = StateGraph(GraphState)

        from collections import defaultdict

        # --- 1. Pre-process Edges to handle Conditions ---
        # We separate conditional and unconditional edges for cleaner logic.
        conditional_map = defaultdict(list)
        unconditional_edges = []

        for edge in graph_data.edges:
            if getattr(edge, "condition", None):
                # Store condition: (source, target, condition_string)
                conditional_map[edge.source].append((edge.target, edge.condition))
            else:
                unconditional_edges.append(edge)

        # Identify nodes that require human input (Pause points)
        # We assume if node_type is 'HUMAN', we should pause before executing it.
        interrupt_nodes = [
            n.node_id for n in graph_data.nodes if n.node_type == "HUMAN"
        ]

        # --- 2. Build Nodes ---
        for node in graph_data.nodes:
            node_id = node.node_id
            # Ensure metadata is a dict (handle potential protobuf MapComposite)
            metadata = dict(node.metadata) if hasattr(node.metadata, 'items') else node.metadata

            # Check if this node is a "Router" node (has conditional outgoing edges)
            is_router = node_id in conditional_map

            # Extract expected keys for Router Nodes to improve LLM prompting.
            # This allows the LLM to know exactly what JSON keys to output.
            expected_keys = set()
            if is_router:
                for _, cond_str in conditional_map[node_id]:
                    key, _ = parse_condition(cond_str)
                    if key:
                        expected_keys.add(str(key))

            node_type = node.node_type

            # Use default arguments in closure to capture loop variables safely.
            # Without `n_id=node_id`, closures might reference the *last* node_id in the loop.
            def create_node_handler(
                    n_id: str,
                    n_meta: Dict[str, Any],
                    n_type: str,
                    is_router_node: bool,
                    router_keys: set,
                    processor: NodeHandler):
                async def handler(state: GraphState, config: RunnableConfig) -> Dict[str, Any]:
                    logger.info(f"Executing node: {n_id} Type: {n_type})")
                    return await processor.process(
                        n_id=n_id,
                        n_meta=n_meta,
                        node_type=n_type,
                        is_router_node=is_router_node,
                        router_keys=router_keys,
                        state=state,
                        config=config)

                return handler

            workflow.add_node(
                node_id,
                create_node_handler(node_id, metadata, node_type, is_router, expected_keys, self.node_handler)
            )

        # --- 3. Add Unconditional Edges ---
        for edge in unconditional_edges:
            workflow.add_edge(edge.source, edge.target)

        # --- 4. Add Conditional Edges (Grouped by Source) ---
        for source, conditions in conditional_map.items():
            # Create the routing function
            def make_router(condition_list, src=source):
                def router(state: GraphState) -> str:
                    logger.info(f"Routing from {src}...")

                    # Check each condition
                    for target, condition_str in condition_list:
                        parsed_key, expected_val = parse_condition(condition_str)
                        if not parsed_key:
                            continue

                        # Look up value in state 'variables' (from routers) or top-level state
                        vars_dict = state.get("variables", {}) or {}
                        # Check variables first, then top level
                        current_val = vars_dict.get(parsed_key)
                        if current_val is None and parsed_key in state:
                            current_val = state[parsed_key]

                        # For numeric conditions, avoid string comparison
                        if isinstance(expected_val, (int, float)) and isinstance(current_val, (int, float)):
                            match = current_val == expected_val
                        else:
                            match = str(current_val).strip() == str(expected_val).strip()

                        logger.debug(
                            f"Router Check: Key='{parsed_key}', Expected='{expected_val}', Actual='{current_val}', Match='{match}'")

                        # if current_val is not None and str(current_val) == str(expected_val):
                        if match:
                            return target

                    # If no condition matches, go to END (or a default fallback)
                    logger.warning(f"No condition matched for node {source}. Ending.")
                    return END

                return router

            # Map possible return values to node names
            # LangGraph needs to know possible paths
            path_map = {target: target for target, _ in conditions}
            path_map[END] = END

            workflow.add_conditional_edges(source, make_router(conditions), path_map)

        # Set Entry Point
        if graph_data.nodes:
            # --- Generally find the start node somewhere in an array ---
            start_node = next((n for n in graph_data.nodes if n.node_type == "START"), None)
            if not start_node:
                raise ValueError("Graph definition has no node with type 'START'.")

            workflow.set_entry_point(start_node.node_id)
        else:
            raise ValueError("Graph definition has no nodes.")

        # Compile with checkpointer and interrupts
        compiled = workflow.compile(
            checkpointer=self._checkpointer,
            # This tells LangGraph to STOP before executing any node in this list
            interrupt_before=interrupt_nodes
        )

        return compiled

    @handle_grpc_errors
    async def BuildGraph(self, request, context):
        graph_id = request.graph_id

        # Evict old compiled version if it exists
        if graph_id in self._compiled_graphs:
            del self._compiled_graphs[graph_id]
            logger.debug(f"Evicted stale compiled graph: {graph_id}")

        # Convert proto to dict
        nodes = [{"node_id": n.node_id, "node_type": n.node_type,
                  "handler_name": n.handler_name, "metadata": dict(n.metadata)} for n in request.nodes]
        edges = [{"source": e.source, "target": e.target, "condition": e.condition or None}
                 for e in request.edges]

        graph_dict = {
            "name": request.graph_name,
            "nodes": nodes,
            "edges": edges,
            "config": dict(request.config)
        }

        # Compile and cache in memory
        validated_graph_data = GraphDefinition(**graph_dict)
        compiled_graph = self._build_langgraph(validated_graph_data)
        self._compiled_graphs[graph_id] = compiled_graph

        return langgraph_pb2.BuildGraphResponse(
            success=True,
            graph_id=graph_id,
            message=f"Graph '{request.graph_name}' built successfully with {len(validated_graph_data.nodes)} nodes"
        )

    async def ExecuteGraph(self, request, context):
        """
                Streaming RPC: yields ExecuteGraphResponse messages as the graph runs.
                NOTE: Not wrapped with handle_grpc_errors because this is an async generator;
                we handle errors inline to ensure proper streaming semantics.
                """
        graph_id = request.graph_id

        if not graph_id:
            # immediate error message then stop the stream
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "graph_id is required")
            return  # defensive

        try:
            compiled_graph = await self._get_compiled_graph(graph_id)
        except KeyError:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"Graph {graph_id} not found")
            return
        except Exception as e:
            logger.error(f"Failed to rebuild graph {graph_id}: {e}", exc_info=True)
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to rebuild graph: {e}")
            return

        config: RunnableConfig = {
            "configurable": {
                "thread_id": request.thread_id if getattr(request, "thread_id", None) else "default_session"
            }
        }

        # --- CHECK CURRENT STATE ---
        # Fetch the current snapshot (it may be paused or fresh)
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
            # If snapshot.next indicates a pause, we are resuming
            next_nodes = getattr(current_snapshot, "next", None) or getattr(current_snapshot, "next_nodes", None) or []

            if next_nodes:
                # --- RESUME SCENARIO ---
                # The graph is paused (e.g., waiting at 'verify_billing')
                logger.info(f"Resuming graph from node: {current_snapshot.next}")

                # Update the state with the new user input
                # We merge the new input into the existing state
                if getattr(request, "input", None):
                    await compiled_graph.aupdate_state(
                        config,
                        {"input": request.input},
                        # Since we are paused BEFORE this node,
                        # attributing the input to it is semantically incorrect.
                        # None applies the update to the global state.
                        as_node=None
                    )

                # Resume execution (pass None to continue from checkpoint)
                stream_input = None
            else:
                # --- NEW START SCENARIO ---
                stream_input = {
                    "input": request.input,
                    "context": dict(request.context),
                    "timestamp": time.time()
                }

                # Yield START event only for new runs
                yield langgraph_pb2.ExecuteGraphResponse(
                    event_type="START",
                    timestamp=int(time.time() * 1000),
                    state=_serialize_state(stream_input)
                )

            # Use astream to get events as they happen
            # stream_mode="updates" yields the state after each node
            async for event in compiled_graph.astream(stream_input, config=config, stream_mode="updates"):
                # In 'updates' mode, LangGraph yields a dictionary of only the changes
                # made by the node that just ran, with the node name as the key.
                # event looks like: {"node_name": {"output": "...", "variables": {...}}}
                for node_name, updates in event.items():
                    node_tokens = 0
                    if isinstance(updates, dict):
                        output_content = str(updates.get("output", ""))
                        serialized_state = _serialize_state(updates)
                        node_tokens = updates.get("total_tokens", 0)
                    else:
                        # Fallback if the node handler returned a tuple or other type
                        logger.warning(
                            f"Node '{node_name}' returned a non-dict update (type: {type(updates).__name__}). "
                            "Ensure your NodeHandler returns a dictionary.")
                        output_content = str(updates)
                        # 'updates' contains the actual state changes (e.g., {"output": "...", "variables": {...}})
                        serialized_state = _serialize_state({"raw_output": updates})

                    yield langgraph_pb2.ExecuteGraphResponse(
                        event_type="NODE_END",
                        node_id=node_name,
                        output=output_content,
                        state=serialized_state,
                        timestamp=int(time.time() * 1000),
                        total_tokens=node_tokens
                    )

            # --- POST-STREAM CHECK ---
            # After the stream finishes, check the state to see if we are paused
            # If the subgraph raised GraphInterrupt, the loop above finishes.
            # We must check the state to see if we are paused.
            final_snapshot = await compiled_graph.aget_state(config)
            final_next = getattr(final_snapshot, "next", None) or []
            final_values = getattr(final_snapshot, "values", {}) or {}

            if final_next:
                # If snapshot.next is not empty, the graph is paused at a node
                # Graph is paused waiting for input
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
                # Otherwise, the graph is truly finished
                # Retrieve final output from the last event in the loop,
                # or fetch from snapshot.values if loop was empty (edge case)
                yield langgraph_pb2.ExecuteGraphResponse(
                    event_type="END",
                    output=str(final_values.get("output", "")),
                    state=_serialize_state(final_values),
                    timestamp=int(time.time() * 1000),
                    total_tokens=final_values.get("total_tokens", 0)
                )

        except Exception as exec_error:
            # GraphInterrupt is handled internally by astream and the post-stream check.
            logger.error(f"Execution error: {exec_error}", exc_info=True)

            yield langgraph_pb2.ExecuteGraphResponse(
                event_type="ERROR",
                error_message=str(exec_error),
                timestamp=int(time.time() * 1000)
            )

    @handle_grpc_errors
    async def GetGraphState(self, request, context):
        graph_id = request.graph_id
        thread_id = request.thread_id or "default"

        if graph_id not in self._compiled_graphs:
            raise KeyError(f"Graph {graph_id} not found")

        compiled_graph = self._compiled_graphs[graph_id]

        # Use the checkpointer-aware method to get state
        config = {"configurable": {"thread_id": thread_id}}
        current_state = await compiled_graph.aget_state(config)

        # current_state is a StateSnapshot object
        # We convert it to the proto response format
        return langgraph_pb2.GetGraphStateResponse(
            success=True,
            # current_state.values contains the actual dict state
            state=_serialize_state(current_state.values),
            current_node=current_state.next[0] if current_state.next else "",
            node_history=[]  # Logic to parse history can be added here
        )

    @handle_grpc_errors
    async def UpdateGraphState(self, request, context):
        graph_id = request.graph_id
        thread_id = request.thread_id or "default"

        if graph_id not in self._compiled_graphs:
            raise KeyError(f"Graph {graph_id} not found")

        compiled_graph = self._compiled_graphs[graph_id]
        config = {"configurable": {"thread_id": thread_id}}

        as_node = request.as_node if request.as_node else None

        # Update the state
        await compiled_graph.aupdate_state(
            config,
            dict(request.state_updates),
            as_node=as_node
        )

        # Get the updated state to return
        updated_snapshot = await compiled_graph.aget_state(config)

        return langgraph_pb2.UpdateGraphStateResponse(
            success=True,
            updated_state=_serialize_state(updated_snapshot.values)
        )

    @handle_grpc_errors
    async def GetExecutionHistory(self, request, context):
        graph_id = request.graph_id
        thread_id = request.thread_id or "default"

        try:
            compiled_graph = await self._get_compiled_graph(graph_id)
        except KeyError:
            raise ValueError(f"Graph {graph_id} not found")  # Let decorator handle gRPC response
        except Exception as e:
            logger.error(f"Failed to load graph {graph_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to load graph: {e}")

        config = {"configurable": {"thread_id": thread_id}}
        history_list = []

        try:
            # 3. Iterate through history (This works now because compiled_graph is a Pregel object)
            async for snapshot in compiled_graph.aget_state_history(config):

                # Extract state variables safely
                state_values = snapshot.values or {}

                # Determine node_id
                # We look for 'last_node' which your nodes are setting, or fallback to 'next'
                node_id = state_values.get("last_node")
                if not node_id and snapshot.next:
                    node_id = f"Pending: {snapshot.next[0]}"
                elif not node_id:
                    node_id = "start"

                # Extract per-node tokens from the history list
                history_items = state_values.get("history", [])
                tokens_used = 0
                if history_items and isinstance(history_items, list):
                    last_item = history_items[-1]
                    if isinstance(last_item, dict) and last_item.get("node") == node_id:
                        tokens_used = last_item.get("tokens_used", 0)

                # Extract cumulative tokens from the state
                total_tokens = state_values.get("total_tokens", 0)

                # Create the Proto message
                history_list.append(langgraph_pb2.StateSnapshot(
                    node_id=str(node_id),
                    state_json=json.dumps(state_values, default=str),
                    timestamp=str(snapshot.created_at) if snapshot.created_at else "",
                    tokens_used=tokens_used,
                    total_tokens=total_tokens
                ))

            history_list.reverse()
            return langgraph_pb2.GraphHistoryResponse(history=history_list)

        except Exception as e:
            logger.error(f"History fetch error: {e}", exc_info=True)
            # Raising the exception lets the @handle_grpc_errors decorator handle the gRPC response
            raise e

    @handle_grpc_errors
    async def RewindGraph(self, request, context):
        graph_id = request.graph_id
        thread_id = request.thread_id
        target_state_json = request.target_state_json

        try:
            compiled_graph = await self._get_compiled_graph(graph_id)
        except KeyError:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Graph '{graph_id}' not found")
            return langgraph_pb2.RewindGraphPayload(success=False, message=f"Graph '{graph_id}' not found")
        except Exception as e:
            logger.error(f"Failed to load graph {graph_id}: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Failed to load graph: {e}")
            return langgraph_pb2.RewindGraphPayload(success=False, message=f"Build error: {e}")

        config = {"configurable": {"thread_id": thread_id}}

        try:
            target_values = json.loads(target_state_json)

            await compiled_graph.aupdate_state(
                config,
                values=target_values,
                as_node=request.target_node_id or None
            )

            logger.info(f"Graph {graph_id} rewound successfully for thread {thread_id}")
            return langgraph_pb2.RewindGraphPayload(
                success=True,
                message="State rewound. Send a new message to continue."
            )

        except json.JSONDecodeError as e:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(f"Invalid state JSON: {e}")
            return langgraph_pb2.RewindGraphPayload(success=False, message=f"Invalid state format: {e}")

        except Exception as e:
            logger.exception(f"Rewind failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return langgraph_pb2.RewindGraphPayload(success=False, message=f"Rewind error: {e}")

    @handle_grpc_errors
    async def AskCopilot(self, request, context):
        """
        Handles Copilot chat requests
        """
        logger.info(f"Copilot request received.")

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
            total_tokens=ai_response.usage.get('total_tokens', -1)
        )
