"""
LangGraph Builder with Full Feature Support

This module provides a comprehensive graph builder that supports:
- Conditional edges for dynamic routing
- Tool integration for RAG searches and API calls
- Human-in-the-loop capabilities for approvals
- Subgraphs for complex nested operations
- Multi-LLM provider support
- LangSmith tracing integration
"""

import logging
from typing import Dict, Any, List, Optional, Callable, Union
from functools import partial

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableLambda, RunnablePassthrough
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from app.llm.providers import LLMProvider
from app.tools.registry import ToolRegistry, BaseLangGraphTool

logger = logging.getLogger(__name__)


class GraphState(Dict[str, Any]):
    """Extended graph state supporting all LangGraph features."""
    
    def __init__(self):
        super().__init__(
            input="",
            context={},
            messages=[],
            output="",
            last_node="",
            history=[],
            current_step=0,
            approval_status=None,
            approval_id=None,
            tool_results=[],
            metadata={},
        )


class LangGraphBuilder:
    """
    Advanced LangGraph builder with full feature support.
    
    Features:
    - Multi-LLM provider switching per node
    - Conditional edges for dynamic routing
    - Tool integration (RAG, API calls, etc.)
    - Human-in-the-loop approval workflows
    - Subgraphs for nested operations
    - LangSmith tracing
    """
    
    def __init__(
        self,
        default_llm: Optional[LLMProvider] = None,
        tool_registry: Optional[ToolRegistry] = None,
        enable_tracing: bool = True,
        checkpoint_path: Optional[str] = None
    ):
        self.default_llm = default_llm
        self.tool_registry = tool_registry or ToolRegistry()
        self.enable_tracing = enable_tracing
        self.checkpoint_path = checkpoint_path
        
        # Initialize workflow
        self.workflow: Optional[StateGraph] = None
        self.compiled_graph: Optional[Runnable] = None
        
        if enable_tracing:
            self._setup_tracing()
    
    def _setup_tracing(self):
        """Setup LangSmith tracing for observability."""
        try:
            from langsmith import Client
            from langsmith.wrappers import wrap_openai
            
            logger.info("LangSmith tracing enabled")
            
            # Configure environment variables for LangSmith
            import os
            if not os.getenv("LANGCHAIN_API_KEY"):
                logger.warning(
                    "LANGCHAIN_API_KEY not set. Tracing will work but won't send data to LangSmith."
                )
            else:
                os.environ["LANGCHAIN_TRACING_V2"] = "true"
                os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGCHAIN_PROJECT", "langgraph-service")
                
        except ImportError:
            logger.warning("LangSmith not installed. Install with: pip install langsmith")
    
    def create_workflow(self) -> 'LangGraphBuilder':
        """Create a new StateGraph workflow."""
        self.workflow = StateGraph(GraphState)
        logger.info("Created new workflow")
        return self
    
    def add_node(
        self,
        node_id: str,
        llm_provider: Optional[LLMProvider] = None,
        system_prompt: str = "You are a helpful assistant.",
        tools: Optional[List[BaseLangGraphTool]] = None,
        handler: Optional[Callable] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> 'LangGraphBuilder':
        """
        Add a node to the workflow.
        
        Args:
            node_id: Unique identifier for the node
            llm_provider: LLM provider for this node (uses default if None)
            system_prompt: System prompt for the LLM
            tools: List of tools available to this node
            handler: Custom handler function (if None, uses LLM)
            metadata: Additional metadata
        """
        if self.workflow is None:
            raise ValueError("Must call create_workflow() first")
        
        llm = llm_provider or self.default_llm
        
        if handler:
            # Use custom handler
            node_handler = handler
        elif tools:
            # Create ReAct agent with tools
            langchain_tools = [t.to_langchain_tool() for t in tools]
            
            async def agent_handler(state: GraphState) -> Dict[str, Any]:
                if llm is None:
                    raise ValueError("LLM provider required for agent node")
                
                agent = create_react_agent(
                    model=llm.model if hasattr(llm, 'model') else llm,
                    tools=langchain_tools,
                    prompt=system_prompt
                )
                
                messages = state.get('messages', [])
                result = await agent.ainvoke({"messages": messages})
                
                return {
                    "messages": result.get("messages", []),
                    "output": result["messages"][-1].content if result.get("messages") else "",
                    "last_node": node_id,
                    "tool_results": [],
                }
            
            node_handler = agent_handler
        else:
            # Simple LLM call
            async def llm_handler(state: GraphState) -> Dict[str, Any]:
                if llm is None:
                    raise ValueError("LLM provider required for LLM node")
                
                user_input = state.get('input', '')
                messages = state.get('messages', [])
                
                # Combine messages with system prompt
                response = await llm.generate(
                    user_message=user_input,
                    system_message=system_prompt
                )
                
                return {
                    "output": response,
                    "last_node": node_id,
                    "messages": messages + [{"role": "assistant", "content": response}],
                }
            
            node_handler = llm_handler
        
        self.workflow.add_node(node_id, node_handler)
        logger.info(f"Added node: {node_id}")
        return self
    
    def add_conditional_edge(
        self,
        source: str,
        condition_fn: Callable[[Dict[str, Any]], str],
        edge_map: Dict[str, str]
    ) -> 'LangGraphBuilder':
        """
        Add a conditional edge for dynamic routing.
        
        Args:
            source: Source node ID
            condition_fn: Function that returns the next node ID based on state
            edge_map: Mapping of possible return values to node IDs
        """
        if self.workflow is None:
            raise ValueError("Must call create_workflow() first")
        
        def router(state: Dict[str, Any]) -> str:
            result = condition_fn(state)
            return edge_map.get(result, END)
        
        self.workflow.add_conditional_edges(source, router, edge_map)
        logger.info(f"Added conditional edge from {source}")
        return self
    
    def add_edge(self, source: str, target: str) -> 'LangGraphBuilder':
        """Add a simple edge between nodes."""
        if self.workflow is None:
            raise ValueError("Must call create_workflow() first")
        
        self.workflow.add_edge(source, target)
        logger.info(f"Added edge: {source} -> {target}")
        return self
    
    def set_entry_point(self, node_id: str) -> 'LangGraphBuilder':
        """Set the entry point of the workflow."""
        if self.workflow is None:
            raise ValueError("Must call create_workflow() first")
        
        self.workflow.set_entry_point(node_id)
        logger.info(f"Set entry point: {node_id}")
        return self
    
    def add_subgraph(
        self,
        subgraph_id: str,
        subgraph_builder: 'LangGraphBuilder'
    ) -> 'LangGraphBuilder':
        """
        Add a subgraph for nested operations.
        
        Args:
            subgraph_id: ID for the subgraph node
            subgraph_builder: Builder containing the subgraph
        """
        if self.workflow is None:
            raise ValueError("Must call create_workflow() first")
        
        if subgraph_builder.compiled_graph is None:
            subgraph_builder.compile()
        
        self.workflow.add_node(subgraph_id, subgraph_builder.compiled_graph)
        logger.info(f"Added subgraph: {subgraph_id}")
        return self
    
    def compile(self, checkpointer: bool = True) -> Runnable:
        """
        Compile the workflow into an executable graph.
        
        Args:
            checkpointer: Enable memory checkpointing for persistence
        """
        if self.workflow is None:
            raise ValueError("Must call create_workflow() first")
        
        if checkpointer:
            saver = MemorySaver()
            self.compiled_graph = self.workflow.compile(checkpointer=saver)
        else:
            self.compiled_graph = self.workflow.compile()
        
        logger.info("Compiled workflow successfully")
        return self.compiled_graph
    
    def build_approval_workflow(
        self,
        approval_node_id: str = "approval_required",
        approved_target: str = "approved_action",
        rejected_target: str = "rejected_action"
    ) -> 'LangGraphBuilder':
        """
        Build a human-in-the-loop approval workflow.
        
        This creates a pattern where execution pauses for human approval.
        """
        def approval_router(state: Dict[str, Any]) -> str:
            approval_status = state.get('approval_status')
            if approval_status == 'approved':
                return approved_target
            elif approval_status == 'rejected':
                return rejected_target
            else:
                return approval_node_id  # Stay in approval state
        
        # Add approval node
        async def approval_handler(state: GraphState) -> Dict[str, Any]:
            # In real implementation, this would wait for Java service callback
            return {
                "last_node": approval_node_id,
                "approval_status": state.get('approval_status', 'pending'),
            }
        
        self.workflow.add_node(approval_node_id, approval_handler)
        self.workflow.add_conditional_edges(
            approval_node_id,
            approval_router,
            {
                approved_target: approved_target,
                rejected_target: rejected_target,
                approval_node_id: approval_node_id
            }
        )
        
        logger.info(f"Built approval workflow: {approval_node_id}")
        return self


def create_multi_llm_graph(
    llm_configs: Dict[str, Dict[str, Any]],
    nodes_config: List[Dict[str, Any]],
    edges_config: List[Dict[str, Any]]
) -> Runnable:
    """
    Create a graph with multiple LLM providers.
    
    Args:
        llm_configs: Dict of LLM configurations keyed by provider name
        nodes_config: List of node configurations
        edges_config: List of edge configurations
        
    Example:
        >>> graph = create_multi_llm_graph(
        ...     llm_configs={
        ...         'openai': {'provider_type': 'openai', 'api_key': '...', 'model': 'gpt-4'},
        ...         'ollama': {'provider_type': 'ollama', 'model': 'llama2'},
        ...     },
        ...     nodes_config=[
        ...         {'id': 'research', 'llm': 'openai', 'tools': ['rag_search']},
        ...         {'id': 'draft', 'llm': 'ollama', 'system_prompt': 'You are a writer'},
        ...     ],
        ...     edges_config=[{'source': 'research', 'target': 'draft'}]
        ... )
    """
    from app.llm.providers import create_llm_provider
    
    # Initialize LLM providers
    llm_providers = {}
    for name, config in llm_configs.items():
        provider_type = config.pop('provider_type', name)
        llm_providers[name] = create_llm_provider(provider_type, config)
    
    # Initialize tool registry
    tool_registry = ToolRegistry().create_default_registry()
    
    # Build graph
    builder = LangGraphBuilder(
        default_llm=list(llm_providers.values())[0] if llm_providers else None,
        tool_registry=tool_registry
    )
    
    builder.create_workflow()
    
    # Add nodes
    for node_cfg in nodes_config:
        node_id = node_cfg['id']
        llm_name = node_cfg.get('llm')
        llm = llm_providers.get(llm_name) if llm_name else None
        
        tools = None
        if 'tools' in node_cfg:
            tools = [tool_registry.get(t) for t in node_cfg['tools'] if tool_registry.get(t)]
        
        builder.add_node(
            node_id=node_id,
            llm_provider=llm,
            system_prompt=node_cfg.get('system_prompt', "You are a helpful assistant."),
            tools=tools
        )
    
    # Add edges
    for edge_cfg in edges_config:
        source = edge_cfg['source']
        target = edge_cfg['target']
        
        if 'condition' in edge_cfg:
            # Conditional edge
            condition_fn = eval(edge_cfg['condition']) if isinstance(edge_cfg['condition'], str) else edge_cfg['condition']
            builder.add_conditional_edge(
                source=source,
                condition_fn=condition_fn,
                edge_map=edge_cfg.get('edge_map', {target: target})
            )
        else:
            builder.add_edge(source, target)
    
    # Set entry point
    if nodes_config:
        builder.set_entry_point(nodes_config[0]['id'])
    
    return builder.compile()
