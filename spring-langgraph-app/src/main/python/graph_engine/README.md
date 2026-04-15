# Python Graph Engine - Thin Orchestrator Layer

This is the **Python LangGraph orchestration layer** in our hybrid architecture.

## Role

- ✅ **Orchestrates** multi-LLM workflows using LangGraph
- ✅ **Routes** dynamically between agents (conditional edges)
- ✅ **Manages** human-in-the-loop checkpoints
- ✅ **Delegates** heavy lifting to Java via gRPC:
  - RAG searches
  - Business logic validation
  - Approval workflows
  - Tool execution

## What's NOT Here

- ❌ No business logic (moved to Java)
- ❌ No database access (Java handles)
- ❌ No complex integrations (Java provides tools)
- ❌ No security enforcement (Java validates)

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Generate gRPC stubs
python -m grpc_tools.protoc \
  -I../../proto \
  --python_out=. \
  --grpc_python_out=. \
  ../../proto/core_logic.proto

# Set environment variables
export JAVA_CORE_HOST=localhost
export JAVA_CORE_PORT=50051
export OPENAI_API_KEY=your-key
export LANGCHAIN_API_KEY=your-langsmith-key

# Run example
python hybrid_graph_engine.py
```

## Adding New LLM Providers

Trivial! Just add one more case to `_get_llm()`:

```python
elif provider == "new_provider":
    from langchain_newprovider import ChatNewProvider
    return ChatNewProvider(model="...", temperature=0.7)
```

Compare this to Java where you'd need to write:
- HTTP client setup
- Authentication handling
- Streaming support
- Token counting
- Error handling
- Retry logic
- (~500 lines per provider!)

## Architecture Diagram

```
┌─────────────────────────────────────┐
│  Python Graph Engine                │
│  ┌───────────────────────────────┐  │
│  │ LangGraph StateGraph          │  │
│  │                               │  │
│  │ [START] → Analyst Node        │  │
│  │              ↓                │  │
│  │      Conditional Edge         │  │
│  │     ↙    ↓    ↘   ↘          │  │
│  │  Research Coder Approval End │  │
│  │     ↓                          │  │
│  │  ToolNode (gRPC calls)        │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
              │ gRPC
              ▼
┌─────────────────────────────────────┐
│  Java Core Service                  │
│  • RAG Search                       │
│  • Business Validation              │
│  • Approval Management              │
│  • Tool Execution                   │
└─────────────────────────────────────┘
```

## File Structure

```
graph_engine/
├── hybrid_graph_engine.py    # Main orchestration logic
├── requirements.txt           # Python dependencies
├── README.md                  # This file
└── proto/                     # Generated gRPC stubs (after build)
    ├── core_logic_pb2.py
    └── core_logic_pb2_grpc.py
```

## Testing

```bash
# Run tests
pytest tests/

# Test with mock Java service
pytest tests/test_hybrid_engine.py -v
```

## Observability

Enable LangSmith tracing:

```bash
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
export LANGCHAIN_API_KEY=your-key
export LANGCHAIN_PROJECT=hybrid-graph-engine
```

View traces at: https://smith.langchain.com

## Performance

- **gRPC overhead**: ~5-10ms per call (negligible vs LLM latency)
- **Throughput**: Can handle 100+ concurrent graph executions
- **Memory**: ~200MB base + model-specific requirements

## When to Modify This Layer

✅ **DO modify when:**
- Adding new LLM providers
- Changing graph topology (nodes, edges)
- Adjusting routing logic
- Adding new agent types

❌ **DON'T modify for:**
- Business logic changes (update Java instead)
- New database queries (add Java tool)
- Security policy changes (update Java)
- RAG indexing changes (Java handles)
