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

## Mock Functions

#### 1. Node Handler Mock (Line 100-107 in `langgraph_servicer.py`)

```python
def create_handler(name: str):
    async def handler(state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"Executing node: {name}")
        # Simulate async work
        await asyncio.sleep(0.01)
        return {"last_node": name, "processed": True}
    return handler
```

**Issue**: The node handlers are hardcoded to return a simple response. They don't actually call any real business logic or external services based on the `handler_name` specified in the graph definition.

#### 2. In-Memory Store (Lines 39-40 in `graph_store.py`)

```python
self._graphs: Dict[str, StoredGraph] = {}
self._states: Dict[str, Dict[str, Any]] = {}
```

**Issue**: Data is stored in memory only. All graphs and states are lost when the service restarts. The comment explicitly states: *"In a real app, this would be Redis or a Database"*

#### 3. Conditional Edges Ignored (Line 115 in `langgraph_servicer.py`)

```python
logger.warning(f"Conditional edge from {edge.source} ignored (requires custom routing)")
```

**Issue**: Conditional edges defined in the graph are not implemented - they're simply logged as warnings and ignored.

---

## Step-by-Step Guide to Implement Full Functionality

### Phase 1: Replace Mock Node Handlers with Real Implementations

#### Step 1.1: Create a Handler Registry

Create a new file `app/handlers/registry.py`:

```python
from typing import Dict, Callable, Any, Awaitable
import logging

logger = logging.getLogger(__name__)

class HandlerRegistry:
    """Registry for node handler functions."""
    
    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
    
    def register(self, name: str):
        """Decorator to register a handler function."""
        def decorator(func: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]):
            self._handlers[name] = func
            logger.info(f"Registered handler: {name}")
            return func
        return decorator
    
    def get(self, name: str) -> Callable:
        """Get a handler by name."""
        if name not in self._handlers:
            raise ValueError(f"Handler '{name}' not found")
        return self._handlers[name]
    
    def list_handlers(self) -> list:
        """List all registered handlers."""
        return list(self._handlers.keys())

# Global registry instance
registry = HandlerRegistry()
```

#### Step 1.2: Implement Real Handlers

Create `app/handlers/__init__.py` with actual implementations:

```python
from app.handlers.registry import registry
from typing import Dict, Any
import httpx
import os

@registry.register("llm_call")
async def llm_call_handler(state: Dict[str, Any]) -> Dict[str, Any]:
    """Call an LLM API with the input from state."""
    input_text = state.get("input", "")
    api_key = os.getenv("OPENAI_API_KEY")
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": input_text}]
            }
        )
        result = response.json()["choices"][0]["message"]["content"]
    
    return {
        "last_node": "llm_call",
        "llm_output": result,
        "processed": True
    }

@registry.register("text_processor")
async def text_processor_handler(state: Dict[str, Any]) -> Dict[str, Any]:
    """Process text (e.g., clean, transform, analyze)."""
    input_text = state.get("input", "")
    processed = input_text.strip().lower()
    
    return {
        "last_node": "text_processor",
        "processed_text": processed,
        "processed": True
    }

@registry.register("database_query")
async def database_query_handler(state: Dict[str, Any]) -> Dict[str, Any]:
    """Query a database based on input."""
    from app.database import get_connection
    
    query = state.get("input", "")
    conn = await get_connection()
    results = await conn.fetch(query)
    
    return {
        "last_node": "database_query",
        "db_results": results,
        "processed": True
    }

@registry.register("api_call")
async def api_call_handler(state: Dict[str, Any]) -> Dict[str, Any]:
    """Make an HTTP API call."""
    url = state.get("context", {}).get("api_url", "")
    method = state.get("context", {}).get("api_method", "GET")
    
    async with httpx.AsyncClient() as client:
        response = await client.request(method, url)
        result = response.text
    
    return {
        "last_node": "api_call",
        "api_response": result,
        "status_code": response.status_code,
        "processed": True
    }
```

#### Step 1.3: Update the Servicer to Use Real Handlers

Modify `app/services/langgraph_servicer.py`:

```python
from app.handlers.registry import registry

def _build_langgraph(self, graph_data: GraphDefinition) -> StateGraph:
    workflow = StateGraph(dict)

    for node in graph_data.nodes:
        # Get the actual handler from registry
        handler_func = registry.get(node.handler_name)
        workflow.add_node(node.node_id, handler_func)

    # Handle Edges (including conditional edges)
    for edge in graph_data.edges:
        if edge.condition:
            # Implement conditional routing
            condition_func = self._get_condition_handler(edge.condition)
            workflow.add_conditional_edges(
                edge.source,
                condition_func,
                {edge.target: edge.target}  # Map condition results to nodes
            )
        else:
            workflow.add_edge(edge.source, edge.target)

    if graph_data.nodes:
        workflow.set_entry_point(graph_data.nodes[0].node_id)

    return workflow.compile()

def _get_condition_handler(self, condition_name: str):
    """Get a conditional edge handler."""
    # Implement your conditional logic here
    def should_branch(state: Dict[str, Any]) -> str:
        # Your routing logic based on state
        if state.get("some_flag"):
            return "target_node"
        return "alternative_node"
    return should_branch
```

### Phase 2: Implement Persistent Storage

#### Step 2.1: Add Database Dependencies

Update `requirements.txt`:

```txt
# Database
asyncpg>=0.29.0
redis>=5.0.0
```

#### Step 2.2: Create Database Connection Module

Create `app/database.py`:

```python
import asyncpg
import redis.asyncio as redis
from app.config import settings
from typing import Optional

class Database:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None
        self.redis: Optional[redis.Redis] = None
    
    async def connect(self):
        self.pool = await asyncpg.create_pool(settings.DATABASE_URL)
        self.redis = await redis.from_url(settings.REDIS_URL)
        
        # Create tables
        await self._init_tables()
    
    async def _init_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS graphs (
                    graph_id TEXT PRIMARY KEY,
                    graph_name TEXT NOT NULL,
                    definition JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    status TEXT DEFAULT 'active'
                )
            """)
            
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS graph_states (
                    id SERIAL PRIMARY KEY,
                    graph_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    state JSONB NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(graph_id, thread_id)
                )
            """)
    
    async def disconnect(self):
        if self.pool:
            await self.pool.close()
        if self.redis:
            await self.redis.close()

db = Database()
```

#### Step 2.3: Update GraphStore to Use Database

Modify `app/services/graph_store.py`:

```python
from app.database import db
import json

class GraphStore:
    def __init__(self):
        self._compiled_graphs: Dict[str, Any] = {}
    
    async def add_graph(self, graph_id: str, graph_data: Dict[str, Any]) -> None:
        validated_data = GraphDefinition(**graph_data)
        created_at = datetime.now(timezone.utc).isoformat()
        
        async with db.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO graphs (graph_id, graph_name, definition, created_at, status)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (graph_id) DO UPDATE SET
                    graph_name = EXCLUDED.graph_name,
                    definition = EXCLUDED.definition,
                    status = EXCLUDED.status
                """,
                graph_id,
                validated_data.name,
                validated_data.model_dump_json(),
                created_at,
                "active"
            )
        
        logger.info(f"Graph {graph_id} stored in database")
    
    async def get_graph(self, graph_id: str) -> Optional[StoredGraph]:
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM graphs WHERE graph_id = $1", graph_id
            )
            if not row:
                return None
            
            return StoredGraph(
                data=GraphDefinition(**row["definition"]),
                created_at=row["created_at"].isoformat(),
                status=row["status"]
            )
    
    async def delete_graph(self, graph_id: str) -> bool:
        async with db.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM graphs WHERE graph_id = $1", graph_id
            )
            if result == "DELETE 1":
                logger.info(f"Graph {graph_id} deleted")
                return True
        return False
    
    async def list_graphs(self, page_size: int = 10, page_token: Optional[str] = None) -> Tuple[List[Tuple[str, StoredGraph]], str]:
        offset = int(page_token) if page_token else 0
        
        async with db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM graphs ORDER BY created_at DESC LIMIT $1 OFFSET $2",
                page_size, offset
            )
            
            graphs = []
            for row in rows:
                graphs.append((
                    row["graph_id"],
                    StoredGraph(
                        data=GraphDefinition(**row["definition"]),
                        created_at=row["created_at"].isoformat(),
                        status=row["status"]
                    )
                ))
            
            next_token = str(offset + len(rows)) if len(rows) == page_size else ""
            return graphs, next_token
    
    async def update_state(self, graph_id: str, thread_id: str, state_updates: Dict[str, Any]) -> Dict[str, Any]:
        async with db.pool.acquire() as conn:
            # Get current state
            row = await conn.fetchrow(
                "SELECT state FROM graph_states WHERE graph_id = $1 AND thread_id = $2",
                graph_id, thread_id
            )
            current_state = json.loads(row["state"]) if row else {}
            current_state.update(state_updates)
            
            # Upsert state
            await conn.execute(
                """
                INSERT INTO graph_states (graph_id, thread_id, state, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (graph_id, thread_id) DO UPDATE SET
                    state = EXCLUDED.state,
                    updated_at = NOW()
                """,
                graph_id, thread_id, json.dumps(current_state)
            )
            
            # Also cache in Redis for fast access
            if db.redis:
                await db.redis.set(
                    f"state:{graph_id}:{thread_id}",
                    json.dumps(current_state),
                    ex=3600  # 1 hour TTL
                )
            
            return current_state
    
    async def get_state(self, graph_id: str, thread_id: str) -> Dict[str, Any]:
        # Try Redis first
        if db.redis:
            cached = await db.redis.get(f"state:{graph_id}:{thread_id}")
            if cached:
                return json.loads(cached)
        
        # Fallback to database
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT state FROM graph_states WHERE graph_id = $1 AND thread_id = $2",
                graph_id, thread_id
            )
            return json.loads(row["state"]) if row else {}
```

#### Step 2.4: Update Config for Database

Modify `app/config.py`:

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Server Configuration
    SERVER_PORT: int = 50051
    MAX_WORKERS: int = 10
    MAX_MESSAGE_LENGTH: int = 4 * 1024 * 1024
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # Database
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/langgraph"
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # External Services
    OPENAI_API_KEY: str = ""
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
```

#### Step 2.5: Update Main to Initialize Database

Modify `app/main.py`:

```python
from app.database import db

async def serve():
    try:
        import uvloop
        uvloop.install()
        logger.info("Uvloop installed for high-performance async.")
    except ImportError:
        logger.info("Uvloop not available, using default asyncio loop.")

    # Initialize database connection
    await db.connect()
    logger.info("Database connection established")

    server = await create_server()

    await server.start()
    logger.info(f"LangGraph gRPC server started on port {settings.SERVER_PORT}")

    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutdown signal received.")
        await server.stop(grace=5)
        await db.disconnect()
        logger.info("Server and database connections shut down gracefully.")
```

### Phase 3: Implement Conditional Edge Routing

#### Step 3.1: Create Condition Handlers

Create `app/handlers/conditions.py`:

```python
from typing import Dict, Any, Callable

class ConditionRegistry:
    """Registry for conditional edge routing functions."""
    
    def __init__(self):
        self._conditions: Dict[str, Callable] = {}
    
    def register(self, name: str):
        def decorator(func: Callable[[Dict[str, Any]], str]):
            self._conditions[name] = func
            return func
        return decorator
    
    def get(self, name: str) -> Callable:
        if name not in self._conditions:
            raise ValueError(f"Condition '{name}' not found")
        return self._conditions[name]

condition_registry = ConditionRegistry()

@condition_registry.register("has_output")
def has_output_condition(state: Dict[str, Any]) -> str:
    """Route based on whether output exists."""
    if state.get("llm_output"):
        return "with_output"
    return "without_output"

@condition_registry.register("error_check")
def error_check_condition(state: Dict[str, Any]) -> str:
    """Route based on error presence."""
    if state.get("error"):
        return "error_handler"
    return "success_handler"

@condition_registry.register("confidence_threshold")
def confidence_threshold_condition(state: Dict[str, Any]) -> str:
    """Route based on confidence score."""
    confidence = state.get("confidence", 0)
    if confidence > 0.8:
        return "high_confidence"
    elif confidence > 0.5:
        return "medium_confidence"
    return "low_confidence"
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
docker build -t langgraph-service .
docker run -p 50051:50051 langgraph-service
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

| Variable             | Default  | Description                     |
|----------------------|----------|---------------------------------|
| `SERVER_PORT`        | 50051    | gRPC server port                |
| `LOG_LEVEL`          | INFO     | Logging level                   |
| `DATABASE_URL`       | -        | PostgreSQL connection string    |
| `REDIS_URL`          | -        | Redis connection string         |
| `OPENAI_API_KEY`     | -        | OpenAI API key for LLM handlers |
| `MAX_WORKERS`        | 10       |                                 |
| `MAX_MESSAGE_LENGTH` | 52428800 |                                 |
| `YC_API_KEY`         |          |                                 |
| `YC_FOLDER_ID`       |          |                                 |

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
