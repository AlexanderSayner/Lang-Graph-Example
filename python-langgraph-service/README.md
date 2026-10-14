# Python LangGraph gRPC Service

## Overview

This is an **asynchronous gRPC service** built with Python that provides a runtime for defining, building, and executing LangGraph workflows. It allows clients to dynamically create graph-based workflows (using LangGraph), execute them with streaming support, and manage their state.

### Purpose

The service was designed to:
- Provide a **remote execution engine** for LangGraph workflows
- Enable **dynamic graph creation** via gRPC API calls
- Support **streaming execution results** for real-time feedback
- Offer **state management** for graph executions (per thread/session)
- Serve as a **microservice** that can be integrated into larger AI/LLM orchestration systems

---

## Features

### Core Functionality

| Feature              | Description                                                             |
|----------------------|-------------------------------------------------------------------------|
| **BuildGraph**       | Dynamically create and compile LangGraph workflows with nodes and edges |
| **ExecuteGraph**     | Execute graphs with streaming support (server-side streaming RPC)       |
| **GetGraphState**    | Retrieve the current state of a graph execution for a specific thread   |
| **UpdateGraphState** | Update graph state manually for a specific thread                       |
| **ListGraphs**       | Paginated listing of all available graphs                               |
| **DeleteGraph**      | Remove a graph from the system                                          |

### Technical Features

- **Async/Await Architecture**: Built on `grpc.aio` for high-performance concurrent request handling
- **Streaming Support**: `ExecuteGraph` uses server-side streaming to yield events in real-time
- **Request Logging**: Custom gRPC interceptor logs all requests with latency metrics
- **Error Handling**: Decorator-based error mapping to gRPC status codes
- **In-Memory Store**: Thread-safe storage for graph definitions and execution states
- **Pydantic Validation**: All graph definitions are validated using Pydantic models
- **gRPC Reflection**: Enabled for easier debugging and client development
- **uvloop Support**: Optional high-performance event loop for Linux/macOS

---

## Development

### Proto
```bash
mkdir app/generated
```
```bash
python3 -m grpc_tools.protoc \
    -I./app/proto \
    --python_out=./app/generated \
    --grpc_python_out=./app/generated \
    ./app/proto/langgraph.proto
```
If there is a problem with redis import run
```bash
pip install --upgrade "redis>=4.2.0"
```
If there are any problems with a generated file imports set to
```python
from . import langgraph_pb2 as langgraph__pb2
```

---

## Usage Examples

### Starting the Service Locally

```bash
cd python-langgraph-service
./scripts/start_local.sh
```

### Building and Running with Docker

```bash
docker build -t langgraph-gateway .
docker run -d --name langgraph-container -p 50051:50051 --env-file .env langgraph-gateway
```

### Example Client Code (Python)

```python
import grpc
from app.generated import langgraph_pb2, langgraph_pb2_grpc

channel = grpc.aio.insecure_channel('localhost:50051')
stub = langgraph_pb2_grpc.LangGraphServiceStub(channel)

# Build a graph
request = langgraph_pb2.BuildGraphRequest(
    graph_id="my-graph",
    graph_name="My Workflow",
    nodes=[
        langgraph_pb2.NodeDefinition(
            node_id="start",
            node_type="processor",
            handler_name="text_processor"
        ),
        langgraph_pb2.NodeDefinition(
            node_id="llm",
            node_type="llm",
            handler_name="llm_call"
        )
    ],
    edges=[
        langgraph_pb2.EdgeDefinition(source="start", target="llm")
    ]
)

response = await stub.BuildGraph(request)
print(f"Graph built: {response.message}")

# Execute the graph with streaming
exec_request = langgraph_pb2.ExecuteGraphRequest(
    graph_id="my-graph",
    input="Hello, process this text!",
    stream_output=True
)

async for event in stub.ExecuteGraph(exec_request):
    print(f"Event: {event.event_type}, Node: {event.node_id}, Output: {event.output}")
```

---

## Environment Variables

| Variable               | Default         | Description                     |
|------------------------|-----------------|---------------------------------|
| `SERVER_PORT`          | 50051           | gRPC server port                |
| `LOG_LEVEL`            | INFO            | Logging level                   |
| `DATABASE_URL`         | -               | PostgreSQL connection string    |
| `REDIS_URL`            | -               | Redis connection string         |
| `OPENAI_API_KEY`       | -               | OpenAI API key for LLM handlers |
| `MAX_WORKERS`          | 10              |                                 |
| `MAX_MESSAGE_LENGTH`   | 52428800        |                                 |
| `YC_API_KEY`           |                 | Yandex clout api key            |
| `YC_FOLDER_ID`         |                 | Yandex cloud folder ID          |
| `YC_COMPLETION_MODE`   | `sync`          | `sync` = synchronous `/completion` (predictable latency, used for interactive chat) · `async` = deferred `/completionAsync` (~50% cheaper, but queued/batch) |
| `YC_TIMEOUT_SECONDS`   | 60              | HTTP timeout for a synchronous completion |
| `YC_ASYNC_MAX_WAIT_SECONDS`     | 90     | Maximum time to wait for a deferred operation before giving up |
| `YC_ASYNC_POLL_INTERVAL_SECONDS`| 1      | Poll interval while waiting for a deferred operation |
| `YC_ASYNC_FALLBACK_TO_SYNC`     | true   | Fall back to the synchronous endpoint when the deferred operation times out |
| `LLM_MAX_ATTEMPTS`              | 2      | Attempts per graph node when the LLM answer is unusable (refusal / empty text) |
| `LLM_PROVIDER`                  | yandex | Default provider: yandex / openai / groq / deepseek / openrouter / anthropic |
| `LLM_MODEL`                     | (provider default) | Default model; empty = provider default model |
| `LLM_TEMPERATURE`               | 0.6    | Default sampling temperature (0.6 keeps the pre-multi-provider YandexGPT behaviour) |
| `LLM_MAX_OUTPUT_TOKENS`         | 2000   | Default max output tokens                |
| `COPILOT_PROVIDER` / `COPILOT_MODEL` | (from LLM_*) | Optional dedicated provider/model for the in-UI copilot |
| `GROQ_API_KEY` / `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` | - | Key for that provider (only the selected provider key is needed) |
| `<PROVIDER>_MODEL` / `<PROVIDER>_BASE_URL` | (preset) | Per-provider model/endpoint override, e.g. `GROQ_MODEL`, `OPENROUTER_BASE_URL` |

---

## Multi-Provider LLM Configuration

Graph nodes can use **YandexGPT, OpenAI, Groq, DeepSeek, OpenRouter or Anthropic**.
The client layer lives in `app/clients/llm.py` and is backed by LangChain: `ChatOpenAI`
for the OpenAI-compatible providers, `ChatAnthropic` for Anthropic, plus the existing
YandexGPT HTTP client.

**Selection precedence** (first match wins):

1. **Node metadata** - per AI node: `llm_provider`, `llm_model`, `llm_temperature`, `llm_max_tokens`
2. **Graph config** - `config.llm_provider` / `config.llm_model` stored with the graph
3. **Service default** - `LLM_PROVIDER` / `LLM_MODEL` / `LLM_TEMPERATURE` / `LLM_MAX_OUTPUT_TOKENS`

Per-node override (builder UI or the `buildGraph` API):

```json
{ "system_prompt": "...", "llm_provider": "groq", "llm_model": "llama-3.3-70b-versatile" }
```

Graph-wide default (same keys in the graph `config`):

```json
{ "llm_provider": "anthropic", "llm_model": "claude-3-5-sonnet-latest" }
```

| `llm_provider` | Backend | Default model | Base URL |
|---|---|---|---|
| `yandex` (default) | YandexGPT HTTP client | `yandexgpt` | Yandex Cloud |
| `openai` | ChatOpenAI | `gpt-4o-mini` | `https://api.openai.com/v1` |
| `groq` | ChatOpenAI | `llama-3.3-70b-versatile` | `https://api.groq.com/openai/v1` |
| `deepseek` | ChatOpenAI | `deepseek-chat` | `https://api.deepseek.com/v1` |
| `openrouter` | ChatOpenAI | `openai/gpt-4o-mini` | `https://openrouter.ai/api/v1` |
| `anthropic` | ChatAnthropic | `claude-3-5-sonnet-latest` | `https://api.anthropic.com` |

Groq / DeepSeek / OpenRouter / OpenAI share one OpenAI-compatible client (they differ
only by base URL); Anthropic needs the extra `langchain-anthropic` dependency. Only the
selected provider key must be set. An unknown `llm_provider` fails fast when the graph is
compiled, naming the supported values.

A *missing* API key deliberately does **not** fail the build: the provider client is
resolved lazily on the node's first model call (`LazyLLMClient`), so a graph can be built
and saved on a service that does not have that provider's key yet. The error then surfaces
as a node error at run time, exactly like it did before the multi-provider layer.

A blank `system_message` sends no system prompt at all: the Yandex path omits the system
message instead of sending empty text (the REST API rejects `{"text": ""}`), and the old
implicit `settings.system_prompt` (`app/ya_prompt.md`) fallback is no longer injected.

Note: `langchain-openai` sends `max_completion_tokens` (the modern OpenAI name) rather
than `max_tokens`. Groq, DeepSeek and OpenRouter accept it; if a strict gateway rejects
it, drop the `llm_max_tokens` override for that node/graph.

---

## Project Structure

```
python-langgraph-service/
├── app/
│   ├── __init__.py
│   ├── config.py              # Configuration settings
│   ├── interceptors.py        # gRPC logging interceptor
│   ├── main.py                # Application entry point
│   ├── proto/
│   │   └── langgraph.proto    # Protocol buffer definitions
│   ├── generated/             # Auto-generated protobuf files
│   ├── services/
│   │   ├── __init__.py
│   │   ├── graph_store.py     # Graph and state storage
│   │   └── langgraph_servicer.py  # gRPC service implementation
│   └── handlers/              # [TO BE CREATED] Real handler implementations
│       ├── __init__.py
│       ├── registry.py        # Handler registry
│       └── conditions.py      # Conditional routing functions
├── scripts/
│   ├── generate_proto.sh      # Proto generation script
│   └── start_local.sh         # Local development startup
├── Dockerfile                 # Container build instructions
├── requirements.txt           # Python dependencies
└── docker-compose.yml         # [TO BE CREATED] Multi-service orchestration
```

---

## Next Steps for Production Readiness

1. ✅ **Implement real node handlers** (LLM calls, API integrations, database queries)
2. ✅ **Add persistent storage** (PostgreSQL + Redis)
3. ✅ **Implement conditional edge routing**
4. 🔲 **Add authentication/authorization** (API keys, JWT tokens)
5. 🔲 **Implement rate limiting**
6. 🔲 **Add health check endpoints**
7. 🔲 **Set up monitoring and metrics** (Prometheus, Grafana)
8. 🔲 **Add distributed tracing** (OpenTelemetry)
9. 🔲 **Implement graph versioning**
10. 🔲 **Add support for graph imports/exports**
