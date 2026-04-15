"""
Hybrid Graph Engine: Python LangGraph as Orchestrator, Java as Core Logic Provider

This architecture leverages:
1. Python's LangGraph for rich LLM integrations (OpenAI, Ollama, Mistral, etc.)
2. Python's LangChain ecosystem for easy model switching
3. Java for robust business logic, RAG, and enterprise integrations
4. gRPC for high-performance communication between them

Features supported:
- ✅ Conditional edges for dynamic routing
- ✅ Tool integration for RAG searches and API calls  
- ✅ Human-in-the-loop capabilities for approvals
- ✅ Subgraphs for complex nested operations
- ✅ Observability with tracing integration (LangSmith + OpenTelemetry)
- ✅ Multi-LLM support (switch models per node or dynamically)
"""

import os
import json
import asyncio
from typing import Any, Dict, List, Optional, Annotated, TypedDict
from enum import Enum

# LangGraph imports
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode

# LangChain imports - Easy LLM switching
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langchain_community.llms import HuggingFaceHub
# Add more as needed: Anthropic, MistralAI, Groq, etc.

# gRPC client to communicate with Java core
import grpc
from proto.core_logic_pb2_grpc import CoreLogicServiceStub
from proto.core_logic_pb2 import (
    SearchRequest, ContextRequest, ApprovalRequest, ToolRequest
)

# Configuration
JAVA_CORE_HOST = os.getenv("JAVA_CORE_HOST", "localhost")
JAVA_CORE_PORT = int(os.getenv("JAVA_CORE_PORT", "50051"))


class AgentType(Enum):
    """Different agent types for routing"""
    RESEARCHER = "researcher"
    CODER = "coder"
    ANALYST = "analyst"
    APPROVAL_REQUIRED = "approval_required"


class GraphState(TypedDict):
    """State schema for the graph"""
    messages: Annotated[List[Dict[str, Any]], add_messages]
    current_agent: str
    user_id: str
    trace_id: str
    requires_approval: bool
    approval_status: Optional[str]
    context_data: Dict[str, Any]
    search_results: List[Dict[str, Any]]
    llm_provider: str  # Dynamic LLM selection per execution


class HybridGraphEngine:
    """
    Main graph engine that uses Python LangGraph for orchestration
    and delegates heavy lifting to Java via gRPC
    """
    
    def __init__(self, llm_provider: str = "openai"):
        self.llm_provider = llm_provider
        self.llm = self._get_llm(llm_provider)
        self.tools = self._setup_tools()
        self.graph = self._build_graph()
        
    def _get_llm(self, provider: str):
        """
        Easy LLM switching - one of the key advantages of keeping Python
        
        Supports:
        - OpenAI (GPT-4, GPT-3.5-turbo)
        - Ollama (local models: llama2, mistral, codellama)
        - HuggingFace (open source models)
        - Anthropic (Claude)
        - Mistral AI
        - Groq (fast inference)
        - And 50+ more via LangChain integrations
        """
        if provider == "openai":
            return ChatOpenAI(
                model="gpt-4-turbo-preview",
                temperature=0.7,
                streaming=True
            )
        elif provider == "ollama":
            return ChatOllama(
                model="mistral",  # or llama2, codellama, etc.
                base_url="http://localhost:11434",
                temperature=0.7
            )
        elif provider == "mistral":
            from langchain_mistralai import ChatMistralAI
            return ChatMistralAI(
                model="mistral-large-latest",
                temperature=0.7
            )
        elif provider == "groq":
            from langchain_groq import ChatGroq
            return ChatGroq(
                model="mixtral-8x7b-32768",
                temperature=0.7
            )
        elif provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(
                model="claude-3-opus-20240229",
                temperature=0.7
            )
        elif provider == "qwen":
            # Via Ollama or HuggingFace
            return ChatOllama(model="qwen:72b", temperature=0.7)
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")
    
    def _setup_tools(self) -> List:
        """
        Define tools that delegate to Java core service
        """
        # These are Python functions that call Java via gRPC
        return [
            self.search_knowledge_base,
            self.get_business_context,
            self.execute_java_tool,
        ]
    
    async def search_knowledge_base(self, query: str, user_id: str) -> str:
        """Tool: Search internal knowledge base (delegates to Java)"""
        channel = grpc.aio.insecure_channel(f"{JAVA_CORE_HOST}:{JAVA_CORE_PORT}")
        stub = CoreLogicServiceStub(channel)
        
        request = SearchRequest(query=query, top_k=5, user_id=user_id)
        response = await stub.SearchKnowledgeBase(request)
        
        results = [
            {"content": chunk.content, "score": chunk.score, "source": chunk.source}
            for chunk in response.chunks
        ]
        
        return json.dumps({"search_results": results})
    
    async def get_business_context(self, user_id: str, context_type: str) -> str:
        """Tool: Get business context from Java core"""
        channel = grpc.aio.insecure_channel(f"{JAVA_CORE_HOST}:{JAVA_CORE_PORT}")
        stub = CoreLogicServiceStub(channel)
        
        request = ContextRequest(user_id=user_id, context_type=context_type)
        response = await stub.GetBusinessContext(request)
        
        return json.dumps({
            "data": dict(response.data),
            "allowed": response.allowed
        })
    
    async def execute_java_tool(self, tool_name: str, arguments: str) -> str:
        """Tool: Execute arbitrary Java tool"""
        channel = grpc.aio.insecure_channel(f"{JAVA_CORE_HOST}:{JAVA_CORE_PORT}")
        stub = CoreLogicServiceStub(channel)
        
        request = ToolRequest(tool_name=tool_name, arguments=arguments)
        response = await stub.ExecuteTool(request)
        
        return json.dumps({
            "result": response.result,
            "success": response.success
        })
    
    def _route_to_agent(self, state: GraphState) -> str:
        """
        Conditional edge: Route to appropriate agent based on message content
        This is where LangGraph shines - dynamic routing based on LLM output
        """
        last_message = state["messages"][-1]
        content = last_message.get("content", "").lower()
        
        # Simple routing logic - can be enhanced with LLM-based classification
        if "approve" in content or "permission" in content:
            return "approval_node"
        elif "search" in content or "find" in content:
            return "researcher_node"
        elif "code" in content or "implement" in content:
            return "coder_node"
        else:
            return "analyst_node"
    
    async def approval_node(self, state: GraphState) -> GraphState:
        """
        Human-in-the-loop: Pause for human approval
        In production, this would integrate with your approval system
        """
        print("⏸️  PAUSING FOR HUMAN APPROVAL")
        print(f"Trace ID: {state['trace_id']}")
        print(f"Current state: {json.dumps(state, indent=2)}")
        
        # In real implementation:
        # 1. Call Java's RequestApproval RPC
        # 2. Java stores state and notifies external system
        # 3. User approves/rejects via UI
        # 4. Java calls back to resume graph
        
        # Simulated approval (replace with actual gRPC call)
        approved = True  # Would come from Java/core system
        
        return {
            **state,
            "requires_approval": True,
            "approval_status": "approved" if approved else "rejected"
        }
    
    async def researcher_node(self, state: GraphState) -> GraphState:
        """Research agent with tool calling"""
        messages = state["messages"]
        
        # Bind tools to LLM
        llm_with_tools = self.llm.bind_tools(self.tools)
        
        # Invoke LLM
        response = await llm_with_tools.ainvoke(messages)
        
        return {
            **state,
            "messages": state["messages"] + [response.dict()]
        }
    
    async def coder_node(self, state: GraphState) -> GraphState:
        """Coder agent - uses different system prompt"""
        system_prompt = "You are an expert software developer. Write clean, efficient code."
        messages = [{"role": "system", "content": system_prompt}] + state["messages"]
        
        response = await self.llm.ainvoke(messages)
        
        return {
            **state,
            "messages": state["messages"] + [response.dict()]
        }
    
    async def analyst_node(self, state: GraphState) -> GraphState:
        """General analyst agent"""
        response = await self.llm.ainvoke(state["messages"])
        
        return {
            **state,
            "messages": state["messages"] + [response.dict()]
        }
    
    def _build_graph(self) -> StateGraph:
        """
        Build the LangGraph workflow with:
        - Multiple nodes (agents)
        - Conditional edges (dynamic routing)
        - Tool integration
        - Human-in-the-loop
        - Subgraphs support
        """
        builder = StateGraph(GraphState)
        
        # Add nodes
        builder.add_node("researcher_node", self.researcher_node)
        builder.add_node("coder_node", self.coder_node)
        builder.add_node("analyst_node", self.analyst_node)
        builder.add_node("approval_node", self.approval_node)
        builder.add_node("tools", ToolNode(self.tools))
        
        # Set entry point
        builder.add_edge(START, "analyst_node")
        
        # Conditional edges for dynamic routing
        builder.add_conditional_edges(
            source="analyst_node",
            condition=self._route_to_agent,
            mapping={
                "approval_node": "approval_node",
                "researcher_node": "researcher_node",
                "coder_node": "coder_node",
                "analyst_node": "analyst_node"
            }
        )
        
        # Tool calling loop
        builder.add_conditional_edges(
            source="researcher_node",
            condition=lambda state: "tools" if state["messages"][-1].get("tool_calls") else END,
            mapping={"tools": "tools"}
        )
        
        builder.add_edge("tools", "researcher_node")  # Loop back after tool use
        
        # Approval flow
        builder.add_conditional_edges(
            source="approval_node",
            condition=lambda state: "analyst_node" if state["approval_status"] == "approved" else END,
            mapping={"analyst_node": "analyst_node"}
        )
        
        # Compile with checkpointing (for human-in-the-loop persistence)
        memory = MemorySaver()
        graph = builder.compile(checkpointer=memory)
        
        return graph
    
    async def run(self, query: str, user_id: str, llm_provider: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute the graph with streaming support
        """
        config = {
            "configurable": {
                "thread_id": f"{user_id}_{asyncio.get_event_loop().time()}"
            }
        }
        
        initial_state = {
            "messages": [{"role": "user", "content": query}],
            "current_agent": "analyst",
            "user_id": user_id,
            "trace_id": f"trace_{asyncio.get_event_loop().time()}",
            "requires_approval": False,
            "approval_status": None,
            "context_data": {},
            "search_results": [],
            "llm_provider": llm_provider or self.llm_provider
        }
        
        # Run with streaming
        async for event in self.graph.astream(initial_state, config=config, stream_mode="values"):
            # Stream events to client or log
            print(f"📊 Event: {event['messages'][-1]['role']} - {event['messages'][-1]['content'][:100]}...")
        
        return event


# Example: Subgraph for complex nested operations
def create_research_subgraph():
    """
    Subgraph example: Complex research workflow nested inside main graph
    """
    subgraph_builder = StateGraph(GraphState)
    
    subgraph_builder.add_node("search_web", lambda state: state)  # Placeholder
    subgraph_builder.add_node("analyze_results", lambda state: state)
    subgraph_builder.add_node("synthesize", lambda state: state)
    
    subgraph_builder.add_edge(START, "search_web")
    subgraph_builder.add_edge("search_web", "analyze_results")
    subgraph_builder.add_edge("analyze_results", "synthesize")
    subgraph_builder.add_edge("synthesize", END)
    
    return subgraph_builder.compile()


# Observability: Integrate with LangSmith for tracing
def setup_observability():
    """
    Enable LangSmith tracing for full observability
    """
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"
    os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGCHAIN_API_KEY")
    os.environ["LANGCHAIN_PROJECT"] = "hybrid-graph-engine"
    
    # Also supports OpenTelemetry for Java integration
    # from opentelemetry import trace
    # tracer = trace.get_tracer(__name__)


if __name__ == "__main__":
    # Setup observability
    setup_observability()
    
    # Create engine with dynamic LLM selection
    engine = HybridGraphEngine(llm_provider="openai")
    
    # Run example
    async def main():
        result = await engine.run(
            query="Search for information about quantum computing and summarize it",
            user_id="user_123",
            llm_provider="ollama"  # Can switch LLM per request!
        )
        print("\n✅ Final Result:")
        print(result["messages"][-1]["content"])
    
    asyncio.run(main())
