import functools
import logging
import time
import json
from typing import Dict, Any, Callable, Optional

import grpc

from app.config import settings
from app.generated import langgraph_pb2_grpc, langgraph_pb2
from app.services.graph_store import GraphStore, GraphDefinition
from app.llm.providers import create_llm_provider, LLMProvider
from app.routers.graph_builder import LangGraphBuilder, GraphState
from app.tools.registry import ToolRegistry

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

class LangGraphServiceServicer(langgraph_pb2_grpc.LangGraphServiceServicer):
    """
    Async gRPC service implementation for LangGraph operations.
    
    Features:
    - Multi-LLM provider support (OpenAI, Anthropic, Ollama, Mistral, Groq, Yandex, etc.)
    - Conditional edges for dynamic routing
    - Tool integration (RAG, API calls, approvals)
    - Human-in-the-loop capabilities
    - Subgraphs for nested operations
    - LangSmith tracing for observability
    """

    def __init__(self, store: GraphStore):
        self.store = store
        # Cache for compiled graphs
        self._compiled_graphs: Dict[str, Any] = {}
        
        # Initialize default LLM provider
        self._default_llm = self._initialize_default_llm()
        
        # Initialize tool registry
        self._tool_registry = ToolRegistry().create_default_registry()
        
        # Java gRPC stubs (for tool integration)
        self._java_grpc_stub = None
        
        logger.info("LangGraphServiceServicer initialized with multi-LLM support")

    def _initialize_default_llm(self) -> Optional[LLMProvider]:
        """Initialize the default LLM provider based on configuration."""
        try:
            provider_name = settings.DEFAULT_LLM_PROVIDER
            config = settings.get_llm_config(provider_name)
            
            if not config.get('api_key') and not config.get('folder_id'):
                logger.warning(f"No API key configured for {provider_name}, using mock mode")
                return None
            
            llm = create_llm_provider(provider_name, config)
            logger.info(f"Initialized default LLM provider: {provider_name}")
            return llm
        except Exception as e:
            logger.error(f"Failed to initialize default LLM: {e}")
            return None

    def _build_langgraph(self, graph_data: GraphDefinition) -> Any:
        """
        Build and compile a LangGraph workflow from graph definition.
        
        Supports:
        - Multiple LLM providers per node
        - Conditional edges
        - Tool integration
        - Human-in-the-loop workflows
        """
        try:
            builder = LangGraphBuilder(
                default_llm=self._default_llm,
                tool_registry=self._tool_registry,
                enable_tracing=settings.LANGCHAIN_TRACING_V2
            )
            
            builder.create_workflow()
            
            # Build LLM provider cache for nodes
            llm_cache: Dict[str, LLMProvider] = {}
            
            # Add nodes
            for node in graph_data.nodes:
                node_id = node.node_id
                metadata = dict(node.metadata)
                
                # Determine LLM provider for this node
                llm_provider_name = metadata.get('llm_provider', settings.DEFAULT_LLM_PROVIDER)
                
                if llm_provider_name not in llm_cache:
                    config = settings.get_llm_config(llm_provider_name)
                    if config:
                        try:
                            llm_cache[llm_provider_name] = create_llm_provider(llm_provider_name, config)
                        except Exception as e:
                            logger.warning(f"Failed to initialize LLM {llm_provider_name}: {e}")
                            llm_cache[llm_provider_name] = None
                
                llm = llm_cache.get(llm_provider_name)
                
                # Determine tools for this node
                tools = None
                if 'tools' in metadata:
                    tool_names = metadata['tools'].split(',') if isinstance(metadata['tools'], str) else metadata['tools']
                    tools = [self._tool_registry.get(t.strip()) for t in tool_names if self._tool_registry.get(t.strip())]
                
                system_prompt = metadata.get('system_prompt', "You are a helpful assistant.")
                
                builder.add_node(
                    node_id=node_id,
                    llm_provider=llm,
                    system_prompt=system_prompt,
                    tools=tools,
                    metadata=metadata
                )
            
            # Add edges (including conditional)
            for edge in graph_data.edges:
                source = edge.source
                target = edge.target
                
                if edge.condition:
                    # Conditional edge - parse condition from metadata
                    logger.info(f"Adding conditional edge from {source} to {target}")
                    
                    # Default router based on condition field
                    def make_router(condition_field: str, condition_map: Dict[str, str]):
                        def router(state: Dict[str, Any]) -> str:
                            value = state.get(condition_field, '')
                            return condition_map.get(str(value), target)
                        return router
                    
                    # Parse condition configuration
                    try:
                        condition_config = json.loads(edge.condition) if edge.condition.startswith('{') else {}
                        condition_field = condition_config.get('field', 'output')
                        edge_map = condition_config.get('map', {target: target})
                        
                        builder.add_conditional_edges(
                            source=source,
                            condition_fn=make_router(condition_field, edge_map),
                            edge_map=edge_map
                        )
                    except Exception as e:
                        logger.warning(f"Failed to parse condition, using simple edge: {e}")
                        builder.add_edge(source, target)
                else:
                    builder.add_edge(source, target)
            
            # Set entry point
            if graph_data.nodes:
                builder.set_entry_point(graph_data.nodes[0].node_id)
            
            # Compile with checkpointing for memory persistence
            compiled_graph = builder.compile(checkpointer=True)
            logger.info(f"Successfully compiled graph with {len(graph_data.nodes)} nodes")
            return compiled_graph
            
        except Exception as e:
            logger.error(f"Failed to build graph: {e}", exc_info=True)
            raise ValueError(f"Graph compilation failed: {e}")

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
