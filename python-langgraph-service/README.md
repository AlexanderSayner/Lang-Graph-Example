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
python -m grpc_tools.protoc   -I./app/proto   --python_out=./app/generated   --grpc_python_out=./app/generated   ./app/proto/langgraph.proto
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
