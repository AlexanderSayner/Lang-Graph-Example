"""
LangGraph gRPC Service Implementation

This module provides a gRPC server that exposes LangGraph functionality
for building and executing agent graphs.
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import grpc
from concurrent import futures

# Import generated gRPC code
import langgraph_pb2
import langgraph_pb2_grpc

# LangGraph imports
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GraphStore:
    """In-memory store for managing graph definitions and state."""
    
    def __init__(self):
        self._graphs: Dict[str, Dict[str, Any]] = {}
        self._states: Dict[str, Dict[str, Any]] = {}
        self._threads: Dict[str, Dict[str, Any]] = {}
    
    def add_graph(self, graph_id: str, graph_data: Dict[str, Any]) -> None:
        """Store a graph definition."""
        self._graphs[graph_id] = {
            "data": graph_data,
            "created_at": datetime.utcnow().isoformat(),
            "status": "active"
        }
        logger.info(f"Graph {graph_id} stored")
    
    def get_graph(self, graph_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a graph definition."""
        return self._graphs.get(graph_id)
    
    def delete_graph(self, graph_id: str) -> bool:
        """Delete a graph definition."""
        if graph_id in self._graphs:
            del self._graphs[graph_id]
            logger.info(f"Graph {graph_id} deleted")
            return True
        return False
    
    def list_graphs(self, page_size: int = 10, page_token: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all graphs with pagination."""
        graphs = list(self._graphs.values())
        start_idx = 0
        
        if page_token:
            try:
                start_idx = int(page_token)
            except ValueError:
                start_idx = 0
        
        end_idx = min(start_idx + page_size, len(graphs))
        next_token = str(end_idx) if end_idx < len(graphs) else ""
        
        return graphs[start_idx:end_idx], next_token
    
    def update_state(self, graph_id: str, thread_id: str, state_updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update the state for a graph execution thread."""
        key = f"{graph_id}:{thread_id}"
        if key not in self._states:
            self._states[key] = {}
        
        self._states[key].update(state_updates)
        return self._states[key]
    
    def get_state(self, graph_id: str, thread_id: str) -> Dict[str, Any]:
        """Get the current state for a graph execution thread."""
        key = f"{graph_id}:{thread_id}"
        return self._states.get(key, {})


class LangGraphServiceServicer(langgraph_pb2_grpc.LangGraphServiceServicer):
    """gRPC service implementation for LangGraph operations."""
    
    def __init__(self):
        self.store = GraphStore()
        self._compiled_graphs: Dict[str, Any] = {}
    
    def BuildGraph(self, request, context):
        """Build a new graph with the given configuration."""
        try:
            graph_id = request.graph_id
            graph_name = request.graph_name
            
            # Convert nodes and edges to LangGraph format
            nodes = []
            for node in request.nodes:
                nodes.append({
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "handler_name": node.handler_name,
                    "metadata": dict(node.metadata)
                })
            
            edges = []
            for edge in request.edges:
                edges.append({
                    "source": edge.source,
                    "target": edge.target,
                    "condition": edge.condition if edge.condition else None
                })
            
            graph_data = {
                "name": graph_name,
                "nodes": nodes,
                "edges": edges,
                "config": dict(request.config)
            }
            
            # Store the graph definition
            self.store.add_graph(graph_id, graph_data)
            
            # Build the actual LangGraph
            compiled_graph = self._build_langgraph(graph_data)
            self._compiled_graphs[graph_id] = compiled_graph
            
            response = langgraph_pb2.BuildGraphResponse(
                success=True,
                graph_id=graph_id,
                message=f"Graph '{graph_name}' built successfully with {len(nodes)} nodes"
            )
            
            logger.info(f"Built graph: {graph_id}")
            return response
            
        except Exception as e:
            logger.error(f"Error building graph: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return langgraph_pb2.BuildGraphResponse(
                success=False,
                message=f"Failed to build graph: {str(e)}"
            )
    
    def _build_langgraph(self, graph_data: Dict[str, Any]) -> StateGraph:
        """Build a LangGraph StateGraph from the configuration."""
        workflow = StateGraph(dict)
        
        # Add nodes
        for node in graph_data["nodes"]:
            handler_name = node["handler_name"]
            
            # Create a simple handler based on the handler name
            # In production, you would have actual LangChain/LangGraph handlers
            def create_handler(name: str):
                def handler(state: Dict[str, Any]) -> Dict[str, Any]:
                    logger.info(f"Executing node: {name}")
                    # Simulate processing - in production this would use LangChain
                    return {"last_node": name, "processed": True}
                return handler
            
            workflow.add_node(node["node_id"], create_handler(handler_name))
        
        # Add edges
        for edge in graph_data["edges"]:
            if edge["condition"]:
                # Conditional edge (not fully implemented in this example)
                logger.warning("Conditional edges require custom implementation")
            else:
                workflow.add_edge(edge["source"], edge["target"])
        
        # Set entry point if we have nodes
        if graph_data["nodes"]:
            workflow.set_entry_point(graph_data["nodes"][0]["node_id"])
        
        return workflow.compile()
    
    def ExecuteGraph(self, request, context):
        """Execute a graph with the given input."""
        try:
            graph_id = request.graph_id
            input_data = request.input
            stream_output = request.stream_output
            
            # Get the compiled graph
            if graph_id not in self._compiled_graphs:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Graph {graph_id} not found")
                return
            
            compiled_graph = self._compiled_graphs[graph_id]
            
            # Initial state
            initial_state = {
                "input": input_data,
                "context": dict(request.context),
                "timestamp": time.time()
            }
            
            # Send START event
            yield langgraph_pb2.ExecuteGraphResponse(
                event_type="START",
                timestamp=int(time.time() * 1000),
                state=initial_state
            )
            
            # Execute the graph
            try:
                # Run the graph
                result = compiled_graph.invoke(initial_state)
                
                # Send NODE_END events for each step (simplified)
                yield langgraph_pb2.ExecuteGraphResponse(
                    event_type="NODE_END",
                    node_id="final",
                    output=str(result.get("input", "")),
                    state=result,
                    timestamp=int(time.time() * 1000)
                )
                
                # Send END event
                yield langgraph_pb2.ExecuteGraphResponse(
                    event_type="END",
                    output=str(result.get("input", "")),
                    state=result,
                    timestamp=int(time.time() * 1000)
                )
                
            except Exception as exec_error:
                yield langgraph_pb2.ExecuteGraphResponse(
                    event_type="ERROR",
                    error_message=str(exec_error),
                    timestamp=int(time.time() * 1000)
                )
            
            logger.info(f"Executed graph: {graph_id}")
            
        except Exception as e:
            logger.error(f"Error executing graph: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
    
    def GetGraphState(self, request, context):
        """Get the current state of a graph."""
        try:
            graph_id = request.graph_id
            thread_id = request.thread_id or "default"
            
            state = self.store.get_state(graph_id, thread_id)
            
            response = langgraph_pb2.GetGraphStateResponse(
                success=True,
                state=state,
                current_node="",
                node_history=[]
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Error getting graph state: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return langgraph_pb2.GetGraphStateResponse(
                success=False,
                state={},
                current_node="",
                node_history=[]
            )
    
    def UpdateGraphState(self, request, context):
        """Update the state of a graph."""
        try:
            graph_id = request.graph_id
            thread_id = request.thread_id or "default"
            state_updates = dict(request.state_updates)
            
            updated_state = self.store.update_state(graph_id, thread_id, state_updates)
            
            response = langgraph_pb2.UpdateGraphStateResponse(
                success=True,
                updated_state=updated_state
            )
            
            logger.info(f"Updated state for graph: {graph_id}")
            return response
            
        except Exception as e:
            logger.error(f"Error updating graph state: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return langgraph_pb2.UpdateGraphStateResponse(
                success=False,
                updated_state={}
            )
    
    def ListGraphs(self, request, context):
        """List all available graphs."""
        try:
            page_size = request.page_size if request.page_size > 0 else 10
            page_token = request.page_token if request.page_token else None
            
            graphs, next_token = self.store.list_graphs(page_size, page_token)
            
            graph_infos = []
            for i, graph in enumerate(graphs):
                data = graph.get("data", {})
                graph_info = langgraph_pb2.GraphInfo(
                    graph_id=f"graph_{i}",  # In production, use actual graph_id
                    graph_name=data.get("name", "Unknown"),
                    node_count=len(data.get("nodes", [])),
                    created_at=graph.get("created_at", ""),
                    status=graph.get("status", "unknown")
                )
                graph_infos.append(graph_info)
            
            response = langgraph_pb2.ListGraphsResponse(
                graphs=graph_infos,
                next_page_token=next_token
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Error listing graphs: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return langgraph_pb2.ListGraphsResponse(graphs=[])
    
    def DeleteGraph(self, request, context):
        """Delete a graph."""
        try:
            graph_id = request.graph_id
            
            success = self.store.delete_graph(graph_id)
            
            # Also remove compiled graph
            if graph_id in self._compiled_graphs:
                del self._compiled_graphs[graph_id]
            
            response = langgraph_pb2.DeleteGraphResponse(
                success=success,
                message=f"Graph {graph_id} deleted" if success else f"Graph {graph_id} not found"
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Error deleting graph: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return langgraph_pb2.DeleteGraphResponse(
                success=False,
                message=f"Failed to delete graph: {str(e)}"
            )


async def serve(port: int = 50051):
    """Start the gRPC server."""
    server = grpc.aio.server(
        futures.ThreadPoolExecutor(max_workers=10),
        options=[
            ('grpc.max_send_message_length', 50 * 1024 * 1024),
            ('grpc.max_receive_message_length', 50 * 1024 * 1024),
        ]
    )
    
    langgraph_pb2_grpc.add_LangGraphServiceServicer_to_server(
        LangGraphServiceServicer(), server
    )
    
    server.add_insecure_port(f'[::]:{port}')
    
    await server.start()
    logger.info(f"LangGraph gRPC server started on port {port}")
    
    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        await server.stop(0)


if __name__ == '__main__':
    asyncio.run(serve())
