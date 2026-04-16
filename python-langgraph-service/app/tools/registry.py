"""
Tool Integration Layer for LangGraph

This module provides tool wrappers for:
- RAG search (via gRPC to Java service)
- API calls
- Business context validation
- Approval workflow management
- Enterprise tool execution
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Callable
import logging
import json

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ToolResult(BaseModel):
    """Standardized tool execution result."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseLangGraphTool(ABC):
    """Abstract base class for LangGraph tools."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name."""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description for the LLM."""
        pass
    
    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool."""
        pass
    
    def to_langchain_tool(self) -> BaseTool:
        """Convert to LangChain tool format."""
        
        @tool
        async def wrapper(**kwargs) -> str:
            result = await self.execute(**kwargs)
            if result.success:
                return json.dumps(result.data) if not isinstance(result.data, str) else result.data
            else:
                return f"Error: {result.error}"
        
        wrapper.name = self.name
        wrapper.description = self.description
        return wrapper


class RAGSearchTool(BaseLangGraphTool):
    """
    RAG Search Tool - Calls Java service for semantic search.
    
    This tool delegates to the Java layer's RAG search service via gRPC.
    """
    
    def __init__(self, grpc_stub=None):
        self.grpc_stub = grpc_stub
        self._search_function: Optional[Callable] = None
    
    def set_search_function(self, func: Callable):
        """Set the search function (injected from Java gRPC service)."""
        self._search_function = func
    
    @property
    def name(self) -> str:
        return "rag_search"
    
    @property
    def description(self) -> str:
        return (
            "Search knowledge base using semantic similarity. "
            "Use this when you need to find relevant documents or information. "
            "Input should be a search query string."
        )
    
    async def execute(self, query: str, top_k: int = 5, **kwargs) -> ToolResult:
        """Execute RAG search."""
        try:
            if self._search_function:
                results = await self._search_function(query=query, top_k=top_k)
                return ToolResult(
                    success=True,
                    data=results,
                    metadata={"query": query, "top_k": top_k}
                )
            else:
                # Fallback for testing
                logger.warning("RAG search function not configured, returning mock results")
                return ToolResult(
                    success=True,
                    data=[
                        {"content": f"Mock result {i} for query: {query}", "score": 0.9 - i * 0.1}
                        for i in range(min(top_k, 3))
                    ],
                    metadata={"query": query, "top_k": top_k, "mock": True}
                )
        except Exception as e:
            logger.error(f"RAG search failed: {e}")
            return ToolResult(success=False, error=str(e))


class APIcallTool(BaseLangGraphTool):
    """
    Generic API Call Tool for external service integration.
    
    Can be configured to call any REST/gRPC endpoint.
    """
    
    def __init__(
        self,
        name: str = "api_call",
        description: str = "Make an API call to an external service",
        base_url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None
    ):
        self._name = name
        self._description = description
        self.base_url = base_url
        self.headers = headers or {}
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def description(self) -> str:
        return self._description
    
    async def execute(
        self,
        endpoint: str,
        method: str = "GET",
        body: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> ToolResult:
        """Execute API call."""
        import httpx
        
        try:
            url = f"{self.base_url}/{endpoint}" if self.base_url else endpoint
            
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method=method.upper(),
                    url=url,
                    json=body,
                    headers=self.headers,
                    timeout=30.0
                )
                response.raise_for_status()
                
                return ToolResult(
                    success=True,
                    data=response.json() if response.content else {"status": response.status_code},
                    metadata={"url": url, "method": method, "status": response.status_code}
                )
        except Exception as e:
            logger.error(f"API call failed: {e}")
            return ToolResult(success=False, error=str(e))


class ApprovalWorkflowTool(BaseLangGraphTool):
    """
    Human-in-the-loop Approval Tool.
    
    Delegates to Java service for approval workflow management.
    """
    
    def __init__(self, grpc_stub=None):
        self.grpc_stub = grpc_stub
        self._approval_function: Optional[Callable] = None
    
    def set_approval_function(self, func: Callable):
        """Set the approval function (injected from Java gRPC service)."""
        self._approval_function = func
    
    @property
    def name(self) -> str:
        return "request_approval"
    
    @property
    def description(self) -> str:
        return (
            "Request human approval for an action or decision. "
            "Use this when you need authorization before proceeding. "
            "Input should include action_type and details."
        )
    
    async def execute(
        self,
        action_type: str,
        details: str,
        priority: str = "normal",
        **kwargs
    ) -> ToolResult:
        """Request approval."""
        try:
            if self._approval_function:
                approval_id = await self._approval_function(
                    action_type=action_type,
                    details=details,
                    priority=priority
                )
                return ToolResult(
                    success=True,
                    data={"approval_id": approval_id, "status": "pending"},
                    metadata={"action_type": action_type, "priority": priority}
                )
            else:
                # Fallback for testing
                logger.warning("Approval function not configured, returning mock approval ID")
                return ToolResult(
                    success=True,
                    data={
                        "approval_id": f"approval-{hash(details) % 10000}",
                        "status": "pending"
                    },
                    metadata={"action_type": action_type, "priority": priority, "mock": True}
                )
        except Exception as e:
            logger.error(f"Approval request failed: {e}")
            return ToolResult(success=False, error=str(e))


class BusinessValidationTool(BaseLangGraphTool):
    """
    Business Context Validation Tool.
    
    Delegates to Java service for business rule validation.
    """
    
    def __init__(self, grpc_stub=None):
        self.grpc_stub = grpc_stub
        self._validation_function: Optional[Callable] = None
    
    def set_validation_function(self, func: Callable):
        """Set the validation function (injected from Java gRPC service)."""
        self._validation_function = func
    
    @property
    def name(self) -> str:
        return "validate_business_context"
    
    @property
    def description(self) -> str:
        return (
            "Validate business context and rules. "
            "Use this to ensure actions comply with business policies. "
            "Input should include context_type and context_data."
        )
    
    async def execute(
        self,
        context_type: str,
        context_data: Dict[str, Any],
        **kwargs
    ) -> ToolResult:
        """Validate business context."""
        try:
            if self._validation_function:
                is_valid, message = await self._validation_function(
                    context_type=context_type,
                    context_data=context_data
                )
                return ToolResult(
                    success=is_valid,
                    data={"valid": is_valid, "message": message},
                    metadata={"context_type": context_type}
                )
            else:
                # Fallback for testing
                logger.warning("Validation function not configured, assuming valid")
                return ToolResult(
                    success=True,
                    data={"valid": True, "message": "No validation errors (mock)"},
                    metadata={"context_type": context_type, "mock": True}
                )
        except Exception as e:
            logger.error(f"Business validation failed: {e}")
            return ToolResult(success=False, error=str(e))


class ToolRegistry:
    """
    Registry for managing available tools.
    
    Allows dynamic registration and retrieval of tools.
    """
    
    def __init__(self):
        self._tools: Dict[str, BaseLangGraphTool] = {}
    
    def register(self, tool: BaseLangGraphTool):
        """Register a tool."""
        self._tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name}")
    
    def get(self, name: str) -> Optional[BaseLangGraphTool]:
        """Get a tool by name."""
        return self._tools.get(name)
    
    def get_all(self) -> List[BaseLangGraphTool]:
        """Get all registered tools."""
        return list(self._tools.values())
    
    def get_langchain_tools(self) -> List[BaseTool]:
        """Get all tools as LangChain tools."""
        return [tool.to_langchain_tool() for tool in self.get_all()]
    
    def create_default_registry(self, grpc_stub=None) -> 'ToolRegistry':
        """Create a registry with default tools."""
        registry = ToolRegistry()
        
        # Register default tools
        registry.register(RAGSearchTool(grpc_stub=grpc_stub))
        registry.register(APIcallTool())
        registry.register(ApprovalWorkflowTool(grpc_stub=grpc_stub))
        registry.register(BusinessValidationTool(grpc_stub=grpc_stub))
        
        return registry
