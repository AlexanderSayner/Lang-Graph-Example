import functools
import json
import logging
import time
from typing import Dict, Any, Callable

import grpc
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.constants import END
from langgraph.graph import StateGraph

from app.clients.yandex_client import YandexGPTClient
from app.config import settings
from app.generated import langgraph_pb2_grpc, langgraph_pb2
from app.graph_engine.graph_utils import parse_condition, GraphState
from app.graph_engine.node_handlers import NodeHandler
from app.services.graph_store import GraphStore, GraphDefinition

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
    """Converts all values in the state dict to strings for Protobuf compatibility."""
    serialized = {}
    for k, v in state.items():
        if isinstance(v, (dict, list)):
            # Convert complex objects to JSON strings
            serialized[k] = json.dumps(v)
        else:
            # Convert primitives (int, float, bool) to string
            serialized[k] = str(v)
    return serialized


# --- Servicer ---
# TODO: that's a service layer which manages there graphs are saved. Provide a repository for Redis/In-memory persistence control
class LangGraphServiceServicer(langgraph_pb2_grpc.LangGraphServiceServicer):
    """Async gRPC service implementation for LangGraph operations."""

    def __init__(self, store: GraphStore, checkpointer: Any):
        self.store = store
        # Cache for compiled graphs. In production, consider LRU cache or Redis.
        self._compiled_graphs: Dict[str, Any] = {}

        self._checkpointer = checkpointer

        self._llm_client = YandexGPTClient(
            api_key=settings.YC_API_KEY,
            folder_id=settings.YC_FOLDER_ID
        )

        async def graph_loader(graph_id: str):
            if graph_id in self._compiled_graphs:
                return self._compiled_graphs[graph_id]

            data = await self.store.get_graph(graph_id)
            if not data:
                raise ValueError(f"Subgraph not found: {graph_id}")

            validated = GraphDefinition(**data)
            compiled = self._build_langgraph(validated)
            self._compiled_graphs[graph_id] = compiled
            return compiled

        self.node_handler = NodeHandler(self._llm_client, graph_loader)

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
                    logger.info(f"Executing node: {n_id}")
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
                        current_val = vars_dict.get(parsed_key) or state.get(parsed_key)

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
            workflow.set_entry_point(graph_data.nodes[0].node_id)
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

        # Convert proto to dict for Pydantic validation in store
        nodes = [{"node_id": n.node_id, "node_type": n.node_type,
                  "handler_name": n.handler_name, "metadata": dict(n.metadata)} for n in request.nodes]
        edges = [{"source": e.source, "target": e.target, "condition": e.condition or None}
                 for e in request.edges]

        # 1. Prepare the dictionary
        graph_dict = {
            "name": request.graph_name,
            "nodes": nodes,
            "edges": edges,
            "config": dict(request.config)
        }

        # 2. Save the Dictionary to Store (Redis)
        await self.store.add_graph(graph_id, graph_dict)

        # 3. Convert Dict -> Pydantic Model for the internal builder
        validated_graph_data = GraphDefinition(**graph_dict)

        # 4. Build the graph using the validated model
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

        # 1. Basic Validation
        if not graph_id:
            # immediate error message then stop the stream
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "graph_id is required")
            return  # defensive

        # 2. Check Cache
        compiled_graph = self._compiled_graphs.get(graph_id)

        if compiled_graph is None:
            logger.info(f"Graph {graph_id} not in memory. Attempting to load from store...")

            stored_data = await self.store.get_graph(graph_id)

            if not stored_data:
                logger.error(f"Graph {graph_id} not found in store either.")
                await context.abort(grpc.StatusCode.NOT_FOUND, f"Graph {graph_id} not found")
                return

            try:
                # Handle if stored_data is a Pydantic model or a Dictionary
                if hasattr(stored_data, 'model_dump'):
                    graph_dict = stored_data.model_dump()
                elif hasattr(stored_data, 'dict'):
                    graph_dict = stored_data.dict()
                else:
                    graph_dict = stored_data

                # Validate and Build
                validated_graph_data = GraphDefinition(**graph_dict)
                compiled_graph = self._build_langgraph(validated_graph_data)

                # Cache it for future requests
                self._compiled_graphs[graph_id] = compiled_graph
                logger.info(f"Graph {graph_id} successfully rebuilt and cached.")
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
            # stream_mode="values" yields the state after each node
            async for event in compiled_graph.astream(stream_input, config=config, stream_mode="values"):
                # In 'values' mode, event is the state dict after a node run
                # event is expected to be a dict-like state after each node
                # Defensive extraction with fallbacks
                last_node = event.get("last_node") if isinstance(event, dict) \
                    else getattr(event, "last_node", "unknown")

                if isinstance(event, dict):
                    output_content = str(event.get("output", "") or "")
                    serialized_state = _serialize_state(event)
                else:
                    # snapshot-like object with .values
                    values = getattr(event, "values", {}) or {}
                    output_content = str(values.get("output", "") or "")
                    serialized_state = _serialize_state(values)

                yield langgraph_pb2.ExecuteGraphResponse(
                    event_type="NODE_END",
                    node_id=last_node,
                    output=output_content,
                    state=serialized_state,
                    timestamp=int(time.time() * 1000)
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
                    timestamp=int(time.time() * 1000)
                )

        except Exception as exec_error:
            # If GraphInterrupt bubbled all the way here (unlikely with astream but possible)
            from langgraph.errors import GraphInterrupt
            if isinstance(exec_error, GraphInterrupt):
                logger.warning("Graph Interrupted (Pause). Checking state for WAITING event.")

                # Fetch the state to find out WHERE we are waiting
                snapshot = await compiled_graph.aget_state(config)
                next_nodes = getattr(snapshot, "next", None) or []
                values = getattr(snapshot, "values", {}) or {}

                if next_nodes:
                    waiting_node = next_nodes[0] if isinstance(next_nodes, (list, tuple)) else next_nodes
                    logger.info(f"Graph paused at node: {waiting_node}")

                    # YIELD the waiting event so the client knows to pause
                    yield langgraph_pb2.ExecuteGraphResponse(
                        event_type="WAITING_FOR_INPUT",
                        node_id=waiting_node,
                        output="Action required. Waiting for user input.",
                        state=_serialize_state(values),
                        timestamp=int(time.time() * 1000)
                    )

                return

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

        # Update the state
        # as_node="human_input" is optional but recommended to mark where the update came from
        await compiled_graph.aupdate_state(
            config,
            dict(request.state_updates),
            as_node="human_input"
        )

        # Get the updated state to return
        updated_snapshot = await compiled_graph.aget_state(config)

        return langgraph_pb2.UpdateGraphStateResponse(
            success=True,
            updated_state=_serialize_state(updated_snapshot.values)
        )

    @handle_grpc_errors
    async def ListGraphs(self, request, context):
        page_size = request.page_size if request.page_size > 0 else 10
        graphs, next_token = await self.store.list_graphs(page_size, request.page_token)

        graph_infos = []
        for gid, graph_obj in graphs:
            graph_infos.append(langgraph_pb2.GraphInfo(
                graph_id=gid,
                graph_name=graph_obj.get("name", "Unknown"),
                node_count=len(graph_obj.get("nodes", [])),
                created_at=graph_obj.get("created_at", ""),
                status=graph_obj.get("status", "active")
            ))

        return langgraph_pb2.ListGraphsResponse(graphs=graph_infos, next_page_token=next_token)

    @handle_grpc_errors
    async def DeleteGraph(self, request, context):
        success = await self.store.delete_graph(request.graph_id)
        if request.graph_id in self._compiled_graphs:
            del self._compiled_graphs[request.graph_id]

        return langgraph_pb2.DeleteGraphResponse(
            success=success,
            message=f"Graph {request.graph_id} deleted" if success else "Not found"
        )

    @handle_grpc_errors
    async def GetExecutionHistory(self, request, context):
        graph_id = request.graph_id
        thread_id = request.thread_id or "default"

        # 1. Retrieve the compiled graph
        compiled_graph = self._compiled_graphs.get(graph_id)

        # 2. If not in memory, load from store and compile (Same logic as ExecuteGraph)
        if compiled_graph is None:
            logger.info(f"Graph {graph_id} not in memory. Loading for history...")
            stored_data = await self.store.get_graph(graph_id)

            if not stored_data:
                raise ValueError(f"Graph {graph_id} not found")

            try:
                # Handle dict vs pydantic model
                if hasattr(stored_data, 'model_dump'):
                    graph_dict = stored_data.model_dump()
                elif hasattr(stored_data, 'dict'):
                    graph_dict = stored_data.dict()
                else:
                    graph_dict = stored_data

                validated_graph_data = GraphDefinition(**graph_dict)
                compiled_graph = self._build_langgraph(validated_graph_data)

                # Cache it for future use
                self._compiled_graphs[graph_id] = compiled_graph
            except Exception as e:
                logger.error(f"Failed to rebuild graph {graph_id}: {e}", exc_info=True)
                raise ValueError(f"Failed to rebuild graph: {e}")

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
                    node_id = f"Pending: {snapshot.next}"
                elif not node_id:
                    node_id = "start"

                # Create the Proto message
                history_list.append(langgraph_pb2.StateSnapshot(
                    node_id=str(node_id),
                    state_json=json.dumps(state_values, default=str),
                    timestamp=str(snapshot.created_at)
                ))

            return langgraph_pb2.GraphHistoryResponse(history=history_list)

        except Exception as e:
            logger.error(f"History fetch error: {e}", exc_info=True)
            # Raising the exception lets the @handle_grpc_errors decorator handle the gRPC response
            raise e
