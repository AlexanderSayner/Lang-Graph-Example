import asyncio
import functools
import logging
import time
from typing import Dict, Any, Callable

import grpc
from langgraph.graph import StateGraph

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

def _map_node_result_to_response(node_name: str, result: Dict[str, Any]) -> langgraph_pb2.ExecuteGraphResponse:
    """Maps LangGraph state to Protobuf response object."""
    return langgraph_pb2.ExecuteGraphResponse(
        event_type="NODE_END",
        node_id=node_name,
        output=str(result.get("input", "")),
        state=result,
        timestamp=int(time.time() * 1000)
    )


# --- Servicer ---

def _build_langgraph(graph_data: GraphDefinition) -> StateGraph:
    workflow = StateGraph(dict)

    for node in graph_data.nodes:
        # Simple closure to capture handler name
        def create_handler(name: str):
            async def handler(state: Dict[str, Any]) -> Dict[str, Any]:
                logger.info(f"Executing node: {name}")
                # Simulate async work
                await asyncio.sleep(0.01)
                return {"last_node": name, "processed": True}

            return handler

        workflow.add_node(node.node_id, create_handler(node.handler_name))

    # Handle Edges
    for edge in graph_data.edges:
        if edge.condition:
            # Conditional edges require specific implementation logic, logging warning for now
            logger.warning(f"Conditional edge from {edge.source} ignored (requires custom routing)")
        else:
            workflow.add_edge(edge.source, edge.target)

    # Set Entry Point
    if graph_data.nodes:
        # LangGraph v2 uses START constant
        workflow.set_entry_point(graph_data.nodes[0].node_id)

    return workflow.compile()


class LangGraphServiceServicer(langgraph_pb2_grpc.LangGraphServiceServicer):
    """Async gRPC service implementation for LangGraph operations."""

    def __init__(self, store: GraphStore):
        self.store = store
        # Cache for compiled graphs. In production, consider LRU cache or Redis.
        self._compiled_graphs: Dict[str, Any] = {}

    @handle_grpc_errors
    async def BuildGraph(self, request, context):
        graph_id = request.graph_id

        # Convert proto to dict for Pydantic validation in store
        nodes = [{"node_id": n.node_id, "node_type": n.node_type,
                  "handler_name": n.handler_name, "metadata": dict(n.metadata)} for n in request.nodes]
        edges = [{"source": e.source, "target": e.target, "condition": e.condition or None}
                 for e in request.edges]

        graph_data = {
            "name": request.graph_name,
            "nodes": nodes,
            "edges": edges,
            "config": dict(request.config)
        }

        self.store.add_graph(graph_id, graph_data)
        stored_graph = self.store.get_graph(graph_id)

        if not stored_graph:
            raise ValueError("Failed to retrieve stored graph")

        # Build and compile the LangGraph
        compiled_graph = _build_langgraph(stored_graph.data)
        self._compiled_graphs[graph_id] = compiled_graph

        return langgraph_pb2.BuildGraphResponse(
            success=True,
            graph_id=graph_id,
            message=f"Graph '{request.graph_name}' built successfully with {len(nodes)} nodes"
        )

    @handle_grpc_errors
    async def ExecuteGraph(self, request, context):
        graph_id = request.graph_id

        if graph_id not in self._compiled_graphs:
            raise KeyError(f"Graph {graph_id} not found")

        compiled_graph = self._compiled_graphs[graph_id]
        initial_state = {
            "input": request.input,
            "context": dict(request.context),
            "timestamp": time.time()
        }

        # Yield START event
        yield langgraph_pb2.ExecuteGraphResponse(
            event_type="START",
            timestamp=int(time.time() * 1000),
            state=initial_state
        )

        try:
            # Use astream to get events as they happen
            # stream_mode="values" yields the state after each node
            async for event in compiled_graph.astream(initial_state, stream_mode="values"):
                # In 'values' mode, event is the state dict after a node run
                last_node = event.get("last_node", "unknown")

                yield _map_node_result_to_response(last_node, event)

            # Yield END event
            yield langgraph_pb2.ExecuteGraphResponse(
                event_type="END",
                output=str(event.get("input", "")),  # event holds final state here
                state=event,
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
        state = self.store.get_state(request.graph_id, request.thread_id or "default")
        return langgraph_pb2.GetGraphStateResponse(success=True, state=state)

    @handle_grpc_errors
    async def UpdateGraphState(self, request, context):
        updated = self.store.update_state(
            request.graph_id,
            request.thread_id or "default",
            dict(request.state_updates)
        )
        return langgraph_pb2.UpdateGraphStateResponse(success=True, updated_state=updated)

    @handle_grpc_errors
    async def ListGraphs(self, request, context):
        page_size = request.page_size if request.page_size > 0 else 10
        graphs, next_token = self.store.list_graphs(page_size, request.page_token)

        graph_infos = []
        for gid, graph_obj in graphs:
            # graph_obj is a Pydantic model now
            graph_infos.append(langgraph_pb2.GraphInfo(
                graph_id=gid,
                graph_name=graph_obj.data.name,
                node_count=len(graph_obj.data.nodes),
                created_at=graph_obj.created_at,
                status=graph_obj.status
            ))

        return langgraph_pb2.ListGraphsResponse(graphs=graph_infos, next_page_token=next_token)

    @handle_grpc_errors
    async def DeleteGraph(self, request, context):
        success = self.store.delete_graph(request.graph_id)
        if request.graph_id in self._compiled_graphs:
            del self._compiled_graphs[request.graph_id]

        return langgraph_pb2.DeleteGraphResponse(
            success=success,
            message=f"Graph {request.graph_id} deleted" if success else "Not found"
        )