import functools
import json
import logging
import operator
import time
from typing import Dict, Any, Callable, TypedDict, List, Annotated

import grpc
from langchain_core.runnables import Runnable
from langgraph.constants import END
from langgraph.graph import StateGraph

from app.clients.yandex_client import YandexGPTClient
from app.config import settings
from app.generated import langgraph_pb2_grpc, langgraph_pb2
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


def _parse_condition(condition_str: str) -> tuple:
    """
    Safely parses a condition string like "key == 'value'".
    Returns (key, expected_value).
    """
    if "==" not in condition_str:
        return None, None

    key_part, val_part = condition_str.split("==", 1)

    # Clean key: strip whitespace
    key = key_part.strip()

    # Clean value: strip whitespace and surrounding quotes
    val = val_part.strip()
    if (val.startswith("'") and val.endswith("'")) or \
            (val.startswith('"') and val.endswith('"')):
        val = val[1:-1]

    return key, val


# --- State Definition ---
class GraphState(TypedDict):
    input: str
    context: Dict[str, Any]
    timestamp: float
    last_node: str
    output: str
    # Automatically append new history items
    variables: Dict[str, Any]
    history: Annotated[List[Dict[str, Any]], operator.add]


# --- Servicer ---
#TODO: that's a service layer which manages there graphs are saved. Provide a repository for Redis/In-memory persistence control
class LangGraphServiceServicer(langgraph_pb2_grpc.LangGraphServiceServicer):
    """Async gRPC service implementation for LangGraph operations."""

    def __init__(self, store: GraphStore, checkpointer: Any):
        self.store = store
        # Cache for compiled graphs. In production, consider LRU cache or Redis.
        self._compiled_graphs: Dict[str, Any] = {}

        self._checkpointer = checkpointer

        # Initialize our custom client
        self._llm_client = YandexGPTClient(
            api_key=settings.YC_API_KEY,
            folder_id=settings.YC_FOLDER_ID
        )

    def _build_langgraph(self, graph_data: GraphDefinition) -> Runnable:
        """Internal method to build and compile the graph."""
        # Use GraphState TypedDict for type safety
        workflow = StateGraph(GraphState)
        llm = self._llm_client  # Access instance client

        # --- 1. Pre-process Edges to handle Conditions ---
        from collections import defaultdict

        # --- 1. Pre-process Edges ---
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
                    key, _ = _parse_condition(cond_str)
                    if key:
                        expected_keys.add(str(key))

            # Use default arguments in closure to capture loop variables safely.
            # Without `n_id=node_id`, closures might reference the *last* node_id in the loop.
            def create_node_handler(
                    n_id: str,
                    n_meta: Dict[str, Any],
                    is_router_node: bool,
                    router_keys: set):
                async def handler(state: GraphState) -> Dict[str, Any]:
                    logger.info(f"Executing node: {n_id}")

                    user_input = state.get("input") or ""
                    # Utilize context by injecting it into the prompt.
                    context_data = state.get("context") or {}
                    context_str = ""
                    if context_data:
                        try:
                            # default=str ensures non-serializable objects don't crash the graph.
                            context_str = f"\n\nAdditional Context:\n{json.dumps(context_data, indent=2, default=str)}"
                        except Exception as e:
                            logger.warning(f"Failed to serialize context: {e}")
                            context_str = f"\n\nContext: {context_data}"

                    # Dynamic History (Conversation memory)
                    # This is CRITICAL. Without this, the LLM doesn't know what happened before.
                    history = state.get("history") or []
                    history_str = ""
                    if history:
                        # Format: "Node Name: Output"
                        history_str = "Conversation History:\n" + "\n".join(
                            [f"- {h.get('node')}: {h.get('output')}" for h in history if h.get('output')]
                        )

                    # Default system prompt
                    system_prompt = n_meta.get("system_prompt")

                    # If it's not a router and has no prompt, DO NOT CALL LLM.
                    # This prevents 'entry' and 'wait' nodes from burning tokens.
                    if not is_router_node and not system_prompt:
                        logger.info(f"Node {n_id} is a pass-through node (no prompt). Skipping LLM.")
                        # Return empty update (or just pass input along if needed)
                        return {"last_node": n_id, "output": user_input}

                    # Check for Empty Input EARLY
                    # If the node is a router, it NEEDS an input to classify.
                    # If the node is an action, it usually NEEDS input.
                    # If input is missing, we should NOT call the LLM.
                    if not user_input.strip():
                        logger.warning(f"Node {n_id}: Input is empty. Skipping LLM call.")
                        # Return a logical default response
                        return {
                            "last_node": n_id,
                            "output": "No input provided.",  # Or use a specific fallback message
                            # Do not update variables or history significantly
                        }

                    # --- Router Logic ---
                    # If it's a router node, we instruct the LLM to classify the intent
                    if is_router_node:
                        # Instruct the LLM to output specific JSON keys based on edge conditions
                        # Extract the keys expected by the conditions
                        # E.g., from "ticket_type == 'technical'" -> we want "ticket_type"
                        # This is a simple heuristic to make the LLM output the right JSON
                        # We dynamically construct the prompt using the first available key for the example.                        example_key = list(router_keys)[0] if router_keys else 'key'
                        if not system_prompt:
                            example_key = list(router_keys)[0] if router_keys else 'key'
                            system_prompt = (
                                f"You are a classifier. Analyze the user input and determine the intent. "
                                f"Respond with a single JSON object containing the classification key. "
                                f"DO NOT output any other text.\n"
                                f"Example Output: {{'{example_key}': 'value'}}"
                            )
                        final_user_input = user_input
                    else:
                        # ACTION / HUMAN / END LOGIC: Needs full context
                        # We combine History + Static Context + Current Input
                        parts = []
                        if history_str:
                            parts.append(history_str)
                        if context_str:
                            parts.append(context_str)

                        # Add the current user input clearly
                        parts.append(f"Current User Input: {user_input}")

                        final_user_input = "\n\n".join(parts)

                        if not system_prompt:
                            system_prompt = "You are a helpful assistant and a pro developer."


                    try:
                        # Call our custom async client
                        logger.debug(f"Calling YandexGPT for node {n_id}...")

                        # Add context to the LLM call
                        response_text = await llm.generate(
                            user_message=final_user_input,
                            system_message=system_prompt,
                            # context=context_data # Uncomment when yandex client will support it, GraphQL already has it in the contract
                        )
                        logger.info(f"Node {n_id} response received.")

                        # Try to parse JSON from router nodes to update state variables
                        if is_router_node:
                            import re

                            try:
                                # Robust Markdown Stripping
                                # Remove ```json and ``` wrappers explicitly
                                clean_response = response_text.strip()
                                if clean_response.startswith("```"):
                                    # Remove first line (```json) and last line (```)
                                    lines = clean_response.splitlines()
                                    if len(lines) > 2:
                                        clean_response = "\n".join(lines[1:-1])

                                # Looks for text between { and }
                                json_match = re.search(r'\{.*}', clean_response, re.DOTALL)

                                if json_match:
                                    json_str = json_match.group(0)
                                    raw_data = json.loads(json_str)
                                    parsed_data = {str(k): v for k, v in raw_data.items()}
                                    logger.info(f"Router {n_id} extracted data: {parsed_data}")
                                    current_vars = dict(state.get("variables", {}) or {})
                                    merged_vars = {**current_vars, **parsed_data}
                                    return {
                                        "last_node": n_id,
                                        "output": response_text,
                                        "variables": merged_vars
                                    }
                                else:
                                    logger.warning(f"Router {n_id}: No JSON found in response.")

                            except json.JSONDecodeError:
                                logger.error(f"Router {n_id} failed to parse JSON.")
                                # Return state unchanged on error to avoid corruption
                                return {
                                    "last_node": n_id,
                                    "output": response_text,
                                    "error": "JSON parse error"
                                }

                        # --- Regular Node Logic ---
                        return {
                            "last_node": n_id,
                            "output": response_text,
                            # Append to history safely
                            "history": [{"node": n_id, "output": response_text}]
                        }

                    except Exception as e:
                        logger.error(f"Node execution failed: {e}", exc_info=True)
                        response_text = f"Error: {str(e)}"

                        return {
                            "last_node": n_id,
                            "output": response_text,
                            "history": state.get("history", []) + [{"node": n_id, "output": response_text}]
                        }

                return handler

            workflow.add_node(
                node_id,
                create_node_handler(node_id, metadata, is_router, expected_keys)  # type: ignore[arg-type]
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
                        parsed_key, expected_val = _parse_condition(condition_str)
                        if not parsed_key:
                            continue

                        # Look up value in state 'variables' (from routers) or top-level state
                        vars_dict = state.get("variables", {}) or {}
                        if parsed_key in vars_dict:
                            current_val = vars_dict.get(parsed_key)
                        else:
                            # fallback to top-level state
                            current_val = state.get(parsed_key)

                        logger.debug(
                            f"Router Check: Key='{parsed_key}', Expected='{expected_val}', Actual='{current_val}'")

                        if current_val is not None and str(current_val) == str(expected_val):
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


        config = {
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

            # After the stream finishes, check the state to see if we are paused
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
