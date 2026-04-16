# Python LangGraph Service - Refactored Architecture

## Overview

This service provides a **scalable, multi-LLM orchestration layer** using LangGraph, designed to work in a hybrid architecture with Java for business logic. The Python layer focuses on what it does best: LLM integration and workflow orchestration, while delegating enterprise concerns to the Java layer via gRPC.

## Architecture Principles

### Python Layer (Orchestration Only)
- ✅ **Multi-LLM switching**: OpenAI, Ollama, Mistral, Groq, Anthropic, Qwen (+50 more via LangChain)
- ✅ **Full LangGraph features**: Conditional edges, subgraphs, human-in-the-loop
- ✅ **LangSmith tracing** for observability
- ✅ **Tool integration** for RAG, API calls, approvals
- ❌ **NO business logic** - delegated to Java via gRPC

### Java Layer (Business Logic via gRPC)
- ✅ RAG search service
- ✅ Business context validation
- ✅ Approval workflow management
- ✅ Enterprise tool execution
- ❌ **NO direct LLM integrations** - uses Python service

## Key Features

### 1. Multi-LLM Provider Support

Switch between 50+ LLM providers with ease:

```python
# Configuration via environment variables
DEFAULT_LLM_PROVIDER=openai  # or anthropic, ollama, mistral, groq, yandex

# Per-node LLM selection via metadata
{
    "node_id": "research",
    "metadata": {
        "llm_provider": "openai",
        "system_prompt": "You are a research assistant"
    }
}
```

**Supported Providers:**
- OpenAI (GPT-4, GPT-3.5-turbo)
- Anthropic (Claude 3 family)
- Ollama (local models: Llama2, Mistral, etc.)
- Mistral AI
- Groq (fast inference)
- YandexGPT (legacy support)
- +50 more via LangChain integrations

### 2. Conditional Edges for Dynamic Routing

```python
# Edge with condition configuration
{
    "source": "analyze",
    "target": "positive_response",
    "condition": json.dumps({
        "field": "sentiment",
        "map": {
            "positive": "positive_response",
            "negative": "escalate",
            "neutral": "neutral_response"
        }
    })
}
```

### 3. Tool Integration

Tools available to nodes:
- `rag_search` - Semantic search (delegates to Java)
- `api_call` - Generic REST/gRPC API calls
- `request_approval` - Human-in-the-loop approvals (Java workflow)
- `validate_business_context` - Business rule validation (Java)

```python
# Node with tools
{
    "node_id": "research_agent",
    "metadata": {
        "tools": ["rag_search", "api_call"],
        "llm_provider": "openai"
    }
}
```

### 4. Human-in-the-Loop Workflows

Built-in approval workflow pattern:

```python
builder = LangGraphBuilder(...)
builder.create_workflow()
builder.add_node("draft", llm=openai_llm)
builder.add_node("approval_required", handler=approval_handler)
builder.add_node("publish", llm=openai_llm)
builder.add_node("revise", llm=openai_llm)

builder.build_approval_workflow(
    approval_node_id="approval_required",
    approved_target="publish",
    rejected_target="revise"
)
```

### 5. Subgraphs for Complex Operations

```python
# Create subgraph
subgraph_builder = LangGraphBuilder(...)
subgraph_builder.create_workflow()
subgraph_builder.add_node("step1", ...)
subgraph_builder.add_node("step2", ...)
subgraph_builder.add_edge("step1", "step2")
subgraph_builder.set_entry_point("step1")

# Add to main graph
main_builder.add_subgraph("complex_operation", subgraph_builder)
```

### 6. Observability with LangSmith

```bash
# Enable tracing
export LANGCHAIN_API_KEY=your_key
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_PROJECT=my-project
```

## Project Structure

```
app/
├── clients/           # Legacy LLM clients (YandexGPT)
│   └── yandex_client.py
├── llm/               # NEW: Multi-LLM provider abstraction
│   ├── __init__.py
│   └── providers.py   # Factory for 50+ LLM providers
├── tools/             # NEW: Tool integration layer
│   ├── __init__.py
│   └── registry.py    # RAG, API, Approval, Validation tools
├── routers/           # NEW: Graph building utilities
│   ├── __init__.py
│   └── graph_builder.py  # LangGraphBuilder class
├── services/          # gRPC servicers
│   ├── __init__.py
│   ├── graph_store.py    # In-memory graph storage
│   └── langgraph_servicer.py  # Main gRPC service
├── config.py          # Enhanced multi-LLM configuration
├── main.py            # Server entry point
└── proto/             # Protocol Buffer definitions
```

## Configuration

### Environment Variables

```bash
# Server
SERVER_PORT=50051
LOG_LEVEL=INFO

# Default LLM Provider
DEFAULT_LLM_PROVIDER=openai

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-3-opus-20240229

# Ollama (local)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama2

# Mistral
MISTRAL_API_KEY=your_key
MISTRAL_MODEL=mistral-large-latest

# Groq
GROQ_API_KEY=your_key
GROQ_MODEL=mixtral-8x7b-32768

# Yandex (legacy)
YC_API_KEY=...
YC_FOLDER_ID=...
YC_MODEL_NAME=yandexgpt

# LangSmith Tracing
LANGCHAIN_API_KEY=...
LANGCHAIN_PROJECT=langgraph-service
LANGCHAIN_TRACING_V2=true

# Java gRPC Service
JAVA_GRPC_HOST=localhost
JAVA_GRPC_PORT=50052
```

## Running the Service

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Generate proto files
mkdir -p app/generated
python -m grpc_tools.protoc \
    -I./app/proto \
    --python_out=./app/generated \
    --grpc_python_out=./app/generated \
    ./app/proto/langgraph.proto

# Fix imports
sed -i 's/import langgraph_pb2/from app.generated import langgraph_pb2/g' \
    ./app/generated/langgraph_pb2_grpc.py

# Run server
python -m app.main
```

### Docker

```bash
docker-compose up --build
```

## Dependencies

See `requirements.txt`:

```txt
# Core
langgraph>=0.2.53
langchain-core>=0.3.25

# LLM Providers (install only what you need)
langchain-openai>=0.2.11
langchain-anthropic>=0.3.0
langchain-ollama>=0.2.0
langchain-mistralai>=0.2.0
langchain-groq>=0.2.0

# Observability
langsmith>=0.2.0

# gRPC
grpcio>=1.68.1
grpcio-tools>=1.68.1
```

## Best Practices

1. **Use environment variables** for API keys
2. **Install only needed LLM packages** to reduce image size
3. **Enable LangSmith tracing** in production for debugging
4. **Use checkpointing** for long-running workflows
5. **Delegate business logic** to Java service via gRPC
6. **Cache compiled graphs** for performance

## License

MIT
