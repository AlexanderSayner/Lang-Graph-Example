import logging
import time
from typing import Dict, Any

import grpc
from langgraph.graph import StateGraph, END

from app.generated import langgraph_pb2
from app.generated import langgraph_pb2_grpc
from app.services.graph_store import GraphStore

logger = logging.getLogger(__name__)


class LangGraphServiceServicer(langgraph_pb2_grpc.LangGraphServiceServicer):
    """gRPC service implementation for LangGraph operations."""

    def __init__(self):
        self.store = GraphStore()
        self._compiled_graphs: Dict[str, Any] = {}

    def BuildGraph(self, request, context):
        try:
            graph_id = request.graph_id
            graph_name = request.graph_name

            nodes = [{
                "node_id": n.node_id,
                "node_type": n.node_type,
                "handler_name": n.handler_name,
                "metadata": dict(n.metadata)
            } for n in request.nodes]

            edges = [{
                "source": e.source,
                "target": e.target,
                "condition": e.condition if e.condition else None
            } for e in request.edges]

            graph_data = {
                "name": graph_name,
                "nodes": nodes,
                "edges": edges,
                "config": dict(request.config)
            }

            self.store.add_graph(graph_id, graph_data)

            # Build actual LangGraph
            compiled_graph = self._build_langgraph(graph_data)
            self._compiled_graphs[graph_id] = compiled_graph

            return langgraph_pb2.BuildGraphResponse(
                success=True,
                graph_id=graph_id,
                message=f"Graph '{graph_name}' built successfully with {len(nodes)} nodes"
            )

        except Exception as e:
            logger.error(f"Error building graph: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return langgraph_pb2.BuildGraphResponse(success=False, message=str(e))

    def _build_langgraph(self, graph_data: Dict[str, Any]) -> StateGraph:
        workflow = StateGraph(dict)

        for node in graph_data["nodes"]:
            handler_name = node["handler_name"]

            def create_handler(name: str):
                def handler(state: Dict[str, Any]) -> Dict[str, Any]:
                    logger.info(f"Executing node: {name}")
                    return {"last_node": name, "processed": True}

                return handler

            workflow.add_node(node["node_id"], create_handler(handler_name))

        for edge in graph_data["edges"]:
            if edge["condition"]:
                logger.warning("Conditional edges require custom implementation")
            else:
                workflow.add_edge(edge["source"], edge["target"])

        if graph_data["nodes"]:
            workflow.set_entry_point(graph_data["nodes"][0]["node_id"])

        return workflow.compile()

    def ExecuteGraph(self, request, context):
        graph_id = request.graph_id

        if graph_id not in self._compiled_graphs:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Graph {graph_id} not found")
            return

        compiled_graph = self._compiled_graphs[graph_id]
        initial_state = {
            "input": request.input,
            "context": dict(request.context),
            "timestamp": time.time()
        }

        yield langgraph_pb2.ExecuteGraphResponse(
            event_type="START",
            timestamp=int(time.time() * 1000),
            state=initial_state
        )

        try:
            result = compiled_graph.invoke(initial_state)

            yield langgraph_pb2.ExecuteGraphResponse(
                event_type="NODE_END",
                node_id="final",
                output=str(result.get("input", "")),
                state=result,
                timestamp=int(time.time() * 1000)
            )

            yield langgraph_pb2.ExecuteGraphResponse(
                event_type="END",
                output=str(result.get("input", "")),
                state=result,
                timestamp=int(time.time() * 1000)
            )

        except Exception as exec_error:
            logger.error(f"Execution error: {exec_error}", exc_info=True)
            yield langgraph_pb2.ExecuteGraphResponse(
                event_type="ERROR",
                error_message=str(exec_error),
                timestamp=int(time.time() * 1000)
            )

    def GetGraphState(self, request, context):
        try:
            state = self.store.get_state(request.graph_id, request.thread_id or "default")
            return langgraph_pb2.GetGraphStateResponse(success=True, state=state)
        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return langgraph_pb2.GetGraphStateResponse(success=False)

    def UpdateGraphState(self, request, context):
        try:
            updated = self.store.update_state(
                request.graph_id,
                request.thread_id or "default",
                dict(request.state_updates)
            )
            return langgraph_pb2.UpdateGraphStateResponse(success=True, updated_state=updated)
        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return langgraph_pb2.UpdateGraphStateResponse(success=False)

    def ListGraphs(self, request, context):
        try:
            page_size = request.page_size if request.page_size > 0 else 10
            graphs, next_token = self.store.list_graphs(page_size, request.page_token)

            graph_infos = []
            for gid, graph in graphs:
                data = graph.get("data", {})
                graph_infos.append(langgraph_pb2.GraphInfo(
                    graph_id=gid,
                    graph_name=data.get("name", "Unknown"),
                    node_count=len(data.get("nodes", [])),
                    created_at=graph.get("created_at", ""),
                    status=graph.get("status", "unknown")
                ))

            return langgraph_pb2.ListGraphsResponse(graphs=graph_infos, next_page_token=next_token)
        except Exception as e:
            logger.error(f"Error listing graphs: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return langgraph_pb2.ListGraphsResponse()

    def DeleteGraph(self, request, context):
        try:
            success = self.store.delete_graph(request.graph_id)
            if request.graph_id in self._compiled_graphs:
                del self._compiled_graphs[request.graph_id]
            return langgraph_pb2.DeleteGraphResponse(
                success=success,
                message=f"Graph {request.graph_id} deleted" if success else "Not found"
            )
        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return langgraph_pb2.DeleteGraphResponse(success=False, message=str(e))