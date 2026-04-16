import functools
import logging
import time
import operator
import json
from typing import Dict, Any, Callable, TypedDict, List, Annotated, Optional

import grpc
from langchain_core.runnables import Runnable
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.memory import MemorySaver

from app.llm_providers.factory import LLMProviderFactory
from app.tools import ToolRegistry, RAGSearchTool, BusinessContextTool, ApprovalTool, SubgraphTool
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
            serialized[k] = json.dumps(v)
        else:
            serialized[k] = str(v)
    return serialized


# --- State Definition ---
class GraphState(TypedDict):
    """Enhanced graph state with support for all LangGraph features."""
    input: str
    context: Dict[str, Any]
    timestamp: float
    last_node: str
    output: str
    messages: Annotated[List[Dict[str, Any]], operator.add]
    history: Annotated[List[Dict[str, Any]], operator.add]
    # Human-in-the-loop
    pending_approval: bool
    approval_result: Optional[Dict[str, Any]]
    # Subgraph support
    subgraph_output: Optional[Dict[str, Any]]
    # Tool results
    tool_results: Annotated[List[Dict[str, Any]], operator.add]


# --- Servicer ---

class LangGraphServiceServicer(langgraph_pb2_grpc.LangGraphServiceServicer):
    """
    Enhanced Async gRPC service implementation for LangGraph operations.
    
    Supports:
    - Multi-LLM provider switching (OpenAI, Anthropic, Ollama, Mistral, Groq, Yandex, etc.)
    - Conditional edges for dynamic routing
    - Tool integration (RAG, API calls, business validation)
    - Human-in-the-loop approvals
    - Subgraphs for nested operations
    - Observability with LangSmith tracing
    """

    def __init__(self, store: GraphStore):
        self.store = store
        self._compiled_graphs: Dict[str, CompiledStateGraph] = {}
        
        # Initialize gRPC channel for Java backend communication (if needed)
        self._java_channel = None
        
        # Initialize tool registry
        self._tool_registry = ToolRegistry()
        
        # LLM provider cache (supports multiple providers per graph)
        self._llm_providers: Dict[str, Any] = {}

    def _get_or_create_llm_provider(self, provider_type: str, config: Dict[str, Any]):
        """Get or create an LLM provider instance."""
        key = f"{provider_type}:{config.get('model_name', 'default')}"
        if key not in self._llm_providers:
            self._llm_providers[key] = LLMProviderFactory.create_provider(provider_type, config)
        return self._llm_providers[key]

    def _setup_tools(self, graph_config: Dict[str, Any]):
        """Initialize tools based on graph configuration."""
        # Only initialize Java-dependent tools if channel is available
        if self._java_channel:
            if graph_config.get("enable_rag", False):
                self._tool_registry.register(RAGSearchTool(self._java_channel))
            if graph_config.get("enable_business_validation", False):
                self._tool_registry.register(BusinessContextTool(self._java_channel))
            if graph_config.get("enable_approvals", False):
                self._tool_registry.register(ApprovalTool(self._java_channel, self.store))
        
        # Subgraph tool is always available
        self._tool_registry.register(SubgraphTool(self._execute_subgraph))

    def _build_langgraph(self, graph_data: GraphDefinition) -> Runnable:
        """
        Build and compile a LangGraph with full feature support.
        
        Features:
        - Multi-LLM support per node
        - Conditional edges
        - Tool integration
        - Human-in-the-loop checkpoints
        - Subgraph nesting
        """
        # Setup memory saver for checkpointing (human-in-the-loop)
        memory = MemorySaver()
        
        # Use enhanced GraphState
        workflow = StateGraph(GraphState)
        
        # Extract graph-level configuration
        graph_config = graph_data.config or {}
        default_provider = graph_config.get("llm_provider", "yandex")
        default_model_config = graph_config.get("llm_config", {})

        # Register tools if enabled
        self._setup_tools(graph_config)

        # Build nodes
        for node in graph_data.nodes:
            node_id = node.node_id
            metadata = node.metadata
            
            # Node-specific LLM configuration
            node_provider = metadata.get("llm_provider", default_provider)
            node_model_config = metadata.get("llm_config", default_model_config)
            
            # Get or create the LLM provider for this node
            llm_provider = self._get_or_create_llm_provider(node_provider, node_model_config)

            def create_node_handler(n_id: str, n_meta: Dict[str, Any], llm: Any):
                async def handler(state: GraphState) -> Dict[str, Any]:
                    logger.info(f"Executing node: {n_id}")

                    user_input = state.get("input", "")
                    system_prompt = n_meta.get("system_prompt", "You are a helpful assistant.")
                    
                    # Check for pending approval (human-in-the-loop)
                    if state.get("pending_approval", False):
                        logger.info(f"Node {n_id} waiting for approval")
                        return {"last_node": n_id}

                    # Check if tools are requested
                    tools_enabled = n_meta.get("enable_tools", False)
                    tool_results = []
                    
                    if tools_enabled:
                        requested_tools = n_meta.get("tools", [])
                        for tool_name in requested_tools:
                            tool = self._tool_registry.get_tool(tool_name)
                            if tool:
                                tool_params = n_meta.get(f"tool_params_{tool_name}", {})
                                result = await tool.execute(**tool_params)
                                tool_results.append({"tool": tool_name, "result": result})
                                # Augment user input with tool results
                                if result:
                                    user_input = f"{user_input}\n\nContext from {tool_name}: {result}"

                    try:
                        # Generate response using the node's LLM provider
                        logger.debug(f"Calling {node_provider} LLM for node {n_id}...")
                        response_text = await llm.generate(
                            user_message=user_input,
                            system_message=system_prompt,
                            **n_meta.get("llm_params", {})
                        )
                        logger.info(f"Node {n_id} response received from {node_provider}")

                    except Exception as e:
                        logger.error(f"Node execution failed: {e}")
                        response_text = f"Error: {str(e)}"

                    return {
                        "last_node": n_id,
                        "output": response_text,
                        "messages": [{"role": "assistant", "content": response_text}],
                        "history": [{"node": n_id, "output": response_text, "provider": node_provider}],
                        "tool_results": tool_results
                    }

                return handler

            workflow.add_node(node_id, create_node_handler(node_id, metadata, llm_provider))

        # Handle Edges (including conditional edges)
        for edge in graph_data.edges:
            if edge.condition:
                # Conditional edge support
                logger.info(f"Adding conditional edge from {edge.source}")
                condition_func = self._create_condition_function(edge.condition)
                workflow.add_conditional_edges(
                    edge.source,
                    condition_func,
                    {edge.target: edge.target}  # Map condition result to target
                )
            else:
                workflow.add_edge(edge.source, edge.target)

        # Set Entry Point
        if graph_data.nodes:
            workflow.set_entry_point(graph_data.nodes[0].node_id)

        # Compile with checkpointing for human-in-the-loop
        return workflow.compile(checkpointer=memory)

    def _create_condition_function(self, condition: str) -> Callable:
        """
        Create a conditional routing function based on condition string.
        
        Supported conditions:
        - "if_approval_needed": Route to approval node if pending
        - "if_tool_required": Route to tool execution
        - Custom lambda-like expressions
        """
        def condition_func(state: GraphState) -> str:
            # Simple condition evaluation
            if condition == "if_approval_needed":
                return "approval_node" if state.get("pending_approval", False) else END
            
            if condition == "if_has_tool_results":
                return "process_tools" if state.get("tool_results", []) else END
            
            # Default: continue to next node
            return END
        
        return condition_func

    async def _execute_subgraph(
        self,
        graph_id: str,
        input_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Execute a subgraph (nested graph operation)."""
        if graph_id not in self._compiled_graphs:
            raise KeyError(f"Subgraph {graph_id} not found")
        
        compiled_subgraph = self._compiled_graphs[graph_id]
        initial_state = {
            "input": input_data.get("input", ""),
            "context": context or {},
            "timestamp": time.time(),
            "messages": [],
            "history": [],
            "pending_approval": False,
            "approval_result": None,
            "subgraph_output": None,
            "tool_results": []
        }
        
        result = await compiled_subgraph.ainvoke(initial_state)
        return result.get("output", "")

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

        compiled_graph = self._build_langgraph(stored_graph.data)
        self._compiled_graphs[graph_id] = compiled_graph

        return langgraph_pb2.BuildGraphResponse(
            success=True,
            graph_id=graph_id,
            message=f"Graph '{request.graph_name}' built successfully with {len(nodes)} nodes"
        )

    async def ExecuteGraph(self, request, context):
        graph_id = request.graph_id

        # 1. Define internal state (keep types as they are for LangGraph)
        internal_state = {
            "input": request.input,
            "context": dict(request.context),
            "timestamp": time.time()
        }

        # 2. Yield START event (Serialize before yielding)
        yield langgraph_pb2.ExecuteGraphResponse(
            event_type="START",
            timestamp=int(time.time() * 1000),
            state=_serialize_state(internal_state)  # <--- Fix
        )

        try:
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
                state=_serialize_state(internal_state)
            )

            # Use astream to get events as they happen
            # stream_mode="values" yields the state after each node
            async for event in compiled_graph.astream(initial_state, stream_mode="values"):
                # In 'values' mode, event is the state dict after a node run
                last_node = event.get("last_node", "unknown")
                yield langgraph_pb2.ExecuteGraphResponse(
                    event_type="NODE_END",
                    node_id=last_node,
                    output=str(event.get("output", "")),  # Adjusted to 'output' based on your handler
                    state=_serialize_state(event),  # <--- Fix
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
