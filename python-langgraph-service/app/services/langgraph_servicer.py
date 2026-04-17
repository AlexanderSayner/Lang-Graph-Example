import functools
import json
import logging
import operator
import time
from typing import Dict, Any, Callable, TypedDict, List, Annotated

import grpc
from langchain_core.runnables import Runnable
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


# --- State Definition ---
class GraphState(TypedDict):
    input: str
    context: Dict[str, Any]
    timestamp: float
    last_node: str
    output: str
    # Automatically append new history items
    history: Annotated[List[Dict[str, Any]], operator.add]


# --- Servicer ---

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

        for node in graph_data.nodes:
            node_id = node.node_id
            metadata = node.metadata

            def create_node_handler(n_id: str, n_meta: Dict[str, Any]):
                async def handler(state: GraphState) -> Dict[str, Any]:
                    logger.info(f"Executing node: {n_id}")

                    user_input = state.get("input", "")
                    system_prompt = n_meta.get("system_prompt", "You are a helpful assistant and a pro developer.")

                    try:
                        # Call our custom async client
                        logger.debug(f"Calling YandexGPT for node {n_id}...")
                        response_text = await llm.generate(
                            user_message=user_input,
                            system_message=system_prompt
                        )
                        logger.info(f"Node {n_id} response received.")

                    except Exception as e:
                        logger.error(f"Node execution failed: {e}")
                        response_text = f"Error: {str(e)}"

                    return {
                        "last_node": n_id,
                        "output": response_text,
                        "history": [{"node": n_id, "output": response_text}]
                    }

                return handler

            workflow.add_node(node_id, create_node_handler(node_id, metadata))

        # Handle Edges
        for edge in graph_data.edges:
            if edge.condition:
                logger.warning(f"Conditional edge from {edge.source} ignored (requires custom routing)")
            else:
                workflow.add_edge(edge.source, edge.target)

        # Set Entry Point
        if graph_data.nodes:
            workflow.set_entry_point(graph_data.nodes[0].node_id)

        return workflow.compile(checkpointer=self._checkpointer)

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
        stored_graph_data = await self.store.get_graph(graph_id)

        if not stored_graph_data:
            raise ValueError("Failed to retrieve stored graph")

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
        graph_id = request.graph_id

        if graph_id not in self._compiled_graphs:
            # Yield only error if graph not found, or let gRPC abort handle it
            yield langgraph_pb2.ExecuteGraphResponse(
                event_type="ERROR",
                error_message=f"Graph {graph_id} not found",
                timestamp=int(time.time() * 1000)
            )
            raise KeyError(f"Graph {graph_id} not found")

        compiled_graph = self._compiled_graphs[graph_id]

        # Define internal state (keep types as they are for LangGraph)
        initial_state = {
            "input": request.input,
            "context": dict(request.context),
            "timestamp": time.time()
        }

        # Yield START event (Serialize before yielding)
        yield langgraph_pb2.ExecuteGraphResponse(
            event_type="START",
            timestamp=int(time.time() * 1000),
            state=_serialize_state(initial_state)
        )

        config = {
            "configurable": {
                "thread_id": request.thread_id if request.thread_id else "default_session"
            }
        }

        try:

            # Use astream to get events as they happen
            # stream_mode="values" yields the state after each node
            async for event in compiled_graph.astream(initial_state, config=config, stream_mode="values"):
                # In 'values' mode, event is the state dict after a node run
                last_node = event.get("last_node", "unknown")

                yield langgraph_pb2.ExecuteGraphResponse(
                    event_type="NODE_END",
                    node_id=last_node,
                    output=str(event.get("output", "")),  # Adjusted to 'output' based on your handler
                    state=_serialize_state(event),
                    timestamp=int(time.time() * 1000)
                )

            # Yield END event
            yield langgraph_pb2.ExecuteGraphResponse(
                event_type="END",
                output=str(event.get("output", "")),
                state=_serialize_state(event),
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
