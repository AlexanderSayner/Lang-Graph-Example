# Spring LangGraph gRPC-GraphQL Gateway

A Spring Boot 4 (Java 21) gateway that exposes a GraphQL API on top of a LangGraph gRPC backend. 

## Quick Start with Docker

You can build and run the entire application from source using the provided multi-stage Dockerfile, without needing to install Java or Gradle locally.

```bash
# Build the Docker image
docker build -t spring-langgraph-gateway .

# Run the container (Assuming your LangGraph gRPC server is running on host port 50051).
# 9091 is the B2B gRPC facade; without B2B_API_KEY it still boots and rejects every
# B2B call with UNAUTHENTICATED (secure by default).
docker run -d \
  --name langgraph-gateway \
  -p 9191:9191 \
  -p 9091:9091 \
  -e LANGGRAPH_SERVICE_ADDRESS=host.docker.internal:50051 \
  -e B2B_API_KEY=change-me \
  spring-langgraph-gateway
```
> **Note on `host.docker.internal`**: If your Python LangGraph gRPC server is running locally on your machine (and not in Docker), you must use `host.docker.internal` as the host so the Spring Boot container can reach your local machine. On Linux, you may need `--add-host=host.docker.internal:host-gateway`.

## Endpoints

* **GraphQL Playground (UI)**: [http://localhost:9191/graphiql](http://localhost:9191/graphiql)
* **GraphQL POST Endpoint**: `http://localhost:9191/graphql`
* **WebSocket (Subscriptions)**: `ws://localhost:9191/graphql/ws`
* **B2B gRPC facade** (`external.v1`, API-key auth): `localhost:9091`
* **Internal gRPC server** (Python engine callbacks, cluster-internal only): `localhost:9090`

## B2B gRPC API (`external.v1`)

A narrow, authenticated facade for backend-to-backend callers (for example the
`orchestra` product). It is **not** a mirror of the GraphQL schema: UI concerns
(canvas coordinates), session/user management and the arbitrary-HTTP debug tool
are deliberately absent, and authentication is a service account instead of a
session cookie.

* Contract: `src/main/proto/external_api.proto`, package `external.v1`.
* Port: `9091`, served by `B2bGrpcServerConfig` - a separate gRPC server from the
  internal one on `9090`, and the facade is **excluded** from `9090`
  (`B2bGrpcServerConfig.grpcServiceDiscoverer`), so nothing on the engine-facing
  port exposes it.

### RPCs

| RPC | Kind | Notes |
|---|---|---|
| `BuildGraph` | unary | No `position`/coordinates are accepted |
| `ExecuteGraph` | unary | Runs to completion, returns the final state |
| `ExecuteGraphStream` | server streaming | Cancel the stream to stop the run |
| `GetGraphState`, `UpdateGraphState` | unary | Tenant-scoped `thread_id` |
| `GetExecutionHistory` | unary | Per-step state diffs |
| `CancelExecution` | unary | For runs started with the unary `ExecuteGraph` |
| `DeleteGraph`, `ListGraphs` | unary | `ListGraphs` is read-only, page-token paging |
| `AskCopilot` | unary | Chat history is a raw JSON string, passed through |

Structured JSON (`state`, `context`, `config`, `state_updates`) travels as
`google.protobuf.Struct`; raw JSON strings (the copilot chat history) stay
`string`. Note the internal engine contract stores state as `map<string,string>`,
so nested values are flattened to strings on the way to the engine - exactly as
on the GraphQL path.

### Authentication

Send one of the following on every call, plus the optional claims:

```
x-api-key: <key>                    # or
authorization: Bearer <key>
x-tenant-id: <tenant>               # optional; only honoured for accounts with allow-tenant-override
x-owner-id: <owner>                 # optional; recorded for audit
```

Accounts are configured under `b2b.grpc.security.clients` and matched by the
SHA-256 of the key. An entry with neither `api-key` nor `api-key-sha256` is not an
error - it is simply not registered (logged as a warning), so an environment with no
`B2B_API_KEY` boots the application and rejects every call with `UNAUTHENTICATED`.
A *broken* credential (bad digest, duplicate key) still stops the application at
startup rather than failing at call time. A failed call carries the GraphQL-parity
error code in the `x-error-code` trailer (e.g. `GRAPH_NOT_FOUND`,
`INVALID_ARGUMENT` for validation, `GRAPH_ACCESS_DENIED` for a scope denial).

### Graphs are scoped per account

`core.graphs` has no owner/tenant column, so the account's `allowed-graphs` is what
keeps one B2B tenant out of another's graphs:

```yaml
b2b.grpc.security.clients:
  - client-id: orchestra
    allowed-graphs: ["agent-v1", "agent-*"]   # exact id + prefix; ["*"] = every graph
```

* Applied to **every** graph-keyed RPC - `BuildGraph`, `ExecuteGraph`,
  `ExecuteGraphStream`, `GetGraphState`, `UpdateGraphState`, `GetExecutionHistory`,
  `CancelExecution`, `DeleteGraph` and `AskCopilot` answer
  `PERMISSION_DENIED` + `x-error-code: GRAPH_ACCESS_DENIED` for a graph outside the
  scope, and `ListGraphs` only returns in-scope rows (the filter is pushed into the
  SQL, so `total_count` / `has_next_page` / `next_page_token` stay correct).
* **Fail closed:** an account with no `allowed-graphs` entry may touch no graph at
  all. Because a graph id must be inside the scope *before* it can be built, adding a
  graph means widening this configuration (or starting from `*`, which is the shipped
  single-tenant default and should be narrowed before a second tenant is onboarded).
* **`*` is an authorisation default, not just a convenience.** The shipped
  `B2B_ALLOWED_GRAPHS`/`allowed-graphs` is `*`, i.e. one account with full read/run/delete
  over every graph the service knows about - authentication fails closed (no key means no
  account), authorisation does not. Narrow it before onboarding a second tenant; `core.graphs`
  has no owner column to fall back on.
* `allow-tenant-override: false` keeps an account inside its own tenant. Setting it to `true`
  lets that account claim any `x-tenant-id`, which re-opens exactly the cross-tenant thread
  access `B2BThreadScope` exists to close - grant it only to a trusted integration acting on
  behalf of its own tenants.
* Threads are scoped separately and independently: `<tenant>:<thread_id>` (see below).

### Threads are tenant-scoped

A thread addresses a run's checkpoint, state, history and cancellation, and
nothing in the engine owns one. The facade therefore scopes `thread_id` to the
caller's tenant (`<tenant>:<threadId>`): send `"abc"` and the engine is asked for
`"orchestra:abc"`. An id namespaced with another tenant's prefix is rejected.

### Cancelling a run

`ExecuteGraphStream` uses native gRPC cancellation: when the client cancels the
stream, the service disposes the subscription **and** asks the engine to stop the
run for that thread - which is what actually ends the token spend (the engine has
no "cancel this stream" RPC). `ExecuteGraph` (unary) is stopped with the
`CancelExecution` RPC; "nothing was running" answers `cancelled=false`, never an
error.

### Manual check

The repeatable check is `scripts/b2b_smoke.py` at the repository root. It compiles
`external_api.proto` on the fly (nothing generated is committed) with the Python engine's venv,
which already carries grpcio, and asserts authentication, paging, graph scoping, error redaction
and port isolation:

```bash
# facade running with a configured API key, wildcard scope (single-tenant default)
python-langgraph-service/.venv/bin/python scripts/b2b_smoke.py --api-key <key> --scope '*'

# against an account restricted to one tenant's graphs: only those ids come back,
# an out-of-scope graph is refused with GRAPH_ACCESS_DENIED
python-langgraph-service/.venv/bin/python scripts/b2b_smoke.py --api-key <key> --scope 'graph-*'

# against an account with no scope: 0 graphs listed, every graph-keyed call denied
python-langgraph-service/.venv/bin/python scripts/b2b_smoke.py --api-key <key> --scope ''
```

For a one-off by hand, `grpcurl` needs server reflection first
(`B2B_GRPC_REFLECTION_ENABLED=true`, off outside development):

```bash
grpcurl -plaintext -H 'x-api-key: <key>' -d '{"page_size": 5}' \
  localhost:9091 external.v1.LangGraphExternalApi/ListGraphs

# Without a key the same call is rejected (trailer x-error-code: UNAUTHENTICATED):
grpcurl -plaintext -d '{}' localhost:9091 external.v1.LangGraphExternalApi/ListGraphs
# => Code: Unauthenticated, Message: Missing credentials: ...

# Stream a run, then Ctrl-C mid-run and watch the Python log for
# "Cancelled running graph ...":
grpcurl -plaintext -H 'x-api-key: <key>' -d '{"graph_id": "agent-v1", "thread_id": "t1", "input": "hi"}' \
  localhost:9091 external.v1.LangGraphExternalApi/ExecuteGraphStream
```

## YC tricking
```bash
curl -sSL https://storage.yandexcloud.net/yandexcloud-yc/install.sh | bash
```       
```bash
yandex-cloud/bin/yc init
```
```bash
yandex-cloud/bin/yc iam service-account list
```
```bash
yandex-cloud/bin/yc iam key create --service-account-name ai-service-user --output ./authorized_key.json
```

---

## GraphQL Query & Mutation Examples

You can test these directly in the GraphiQL UI at `http://localhost:9191/graphiql`.

### Test query
```http request
POST http://localhost:9191/graphql

{
    "query": "mutation BuildGraph($input: BuildGraphInput!) { buildGraph(input: $input) { success graphId message } }",
    "variables": {
        "input": {
            "graphId": "graph-001",
            "graphName": "Test Graph",
            "nodes": [
                { "nodeId": "node-1", "nodeType": "START", "handlerName": "startHandler" },
                { "nodeId": "node-2", "nodeType": "END", "handlerName": "endHandler" }
            ],
            "edges": [
                { "source": "node-1", "target": "node-2" }
            ]
        }
    }
}
```
```http request
POST http://localhost:9191/graphql

{
  "query": "mutation BuildGraph($input: BuildGraphInput!) { buildGraph(input: $input) { success graphId message } }",
  "variables": {
    "input": {
      "graphId": "graph-002",
      "graphName": "Support Ticket Triage",
      "nodes": [
        { "nodeId": "triage", "nodeType": "START", "handlerName": "triageHandler" },
        { "nodeId": "technical", "nodeType": "ACTION", "handlerName": "technicalHandler" },
        { "nodeId": "billing", "nodeType": "ACTION", "handlerName": "billingHandler" },
        { "nodeId": "general", "nodeType": "ACTION", "handlerName": "generalHandler" },
        { "nodeId": "resolve", "nodeType": "END", "handlerName": "resolveHandler" }
      ],
      "edges": [
        { "source": "triage", "target": "technical", "condition": "ticket_type == 'technical'" },
        { "source": "triage", "target": "billing", "condition": "ticket_type == 'billing'" },
        { "source": "triage", "target": "general", "condition": "ticket_type == 'general'" },
        { "source": "technical", "target": "resolve" },
        { "source": "billing", "target": "resolve" },
        { "source": "general", "target": "resolve" }
      ]
    }
  }
}
```

### 1. Build a Graph
Creates a new graph structure with nodes and edges on the gRPC backend.
```graphql
mutation BuildNewGraph {
  buildGraph(input: {
    graphId: "agent-v1",
    graphName: "Research Agent",
    nodes: [
      {
        nodeId: "search",
        nodeType: "HTTP_REQUEST",
        handlerName: "tavily_search",
        metadata: { maxResults: "5" }
      },
      {
        nodeId: "synthesize",
        nodeType: "ACTION",
        handlerName: "openai_gpt4"
      }
    ],
    edges: [
      { source: "search", target: "synthesize" }
    ]
  }) {
    success
    graphId
    message
  }
}
```

### 2. Execute a Graph (Synchronous)
Runs the graph and returns the final aggregated result.
```graphql
mutation ExecuteGraphSync {
  executeGraph(input: {
    graphId: "agent-v1",
    input: "What are the latest advances in quantum computing?",
    context: { userId: "user-123" }
  }) {
    success
    output
    state
    errorMessage
  }
}
```

### 3. Execute a Graph (Streaming Subscription)
Streams real-time events (`NODE_START`, `NODE_END`, etc.) as they happen in the gRPC stream.
> *Note: Subscriptions must be run in GraphiQL or a WebSocket-enabled GraphQL client.*
```graphql
subscription ExecuteGraphStream {
  executeGraphStream(input: {
    graphId: "agent-v1",
    input: "Summarize this article...",
    context: { threadId: "thread-abc" }
  }) {
    eventType
    nodeId
    output
    state
    timestamp
    errorMessage
  }
}
```

### 4. Get Graph State
Fetches the current state dictionary and execution history for a specific thread.
```graphql
query GetState {
  getGraphState(
    graphId: "agent-v1", 
    threadId: "thread-abc"
  ) {
    success
    state
    currentNode
    nodeHistory
  }
}
```

### 5. Update Graph State
Manually injects or modifies state variables in the LangGraph thread.
```graphql
mutation UpdateState {
  updateGraphState(input: {
    graphId: "agent-v1",
    threadId: "thread-abc",
    stateUpdates: { 
      user_feedback: "Make it more concise", 
      iteration_count: "2" 
    }
  }) {
    success
    updatedState
    message
  }
}
```

### 6. List Graphs
Paginates through available graphs.
```graphql
query ListAllGraphs {
  listGraphs(pageSize: 5) {
    totalCount
    pageInfo {
      hasNextPage
      endCursor
    }
    graphs {
      graphId
      graphName
      nodeCount
      status
      createdAt
    }
  }
}
```

### 7. Delete a Graph
Removes a graph and its associated data from the backend.
```graphql
mutation DeleteGraph {
  deleteGraph(graphId: "agent-v1") {
    success
    message
  }
}
```

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SERVER_PORT` | The port the Spring Boot app runs on. | `9191` |
| `LANGGRAPH_SERVICE_ADDRESS` | Address of the Python LangGraph engine's gRPC server, as `host:port`. | `localhost:50051` |
| `LANGGRAPH_EXECUTION_TIMEOUT` | Timeout for gRPC execution calls. | `60s` |
| `B2B_GRPC_ENABLED` | Serve the B2B gRPC facade. | `true` |
| `B2B_GRPC_PORT` | Port of the B2B gRPC facade. | `9091` |
| `B2B_GRPC_HOST` | Bind address of the B2B facade (`*` = all interfaces). | `*` |
| `B2B_GRPC_REFLECTION_ENABLED` | gRPC server reflection on the B2B port (grpcurl discovery). | `false` |
| `B2B_GRPC_SSL_BUNDLE` | SSL bundle name for the B2B port; blank = plaintext (in-cluster only). | *(blank)* |
| `B2B_GRPC_SSL_CLIENT_AUTH` | `NONE`, `OPTIONAL` or `REQUIRE` (mTLS) for the B2B port. | `NONE` |
| `B2B_CLIENT_ID` | Service-account id of the default B2B caller. | `orchestra` |
| `B2B_TENANT_ID` | Tenant the default B2B caller is scoped to. | `orchestra` |
| `B2B_OWNER_ID` | Owner the default B2B caller acts on behalf of (audit). | *(blank)* |
| `B2B_API_KEY` | API key of the default B2B caller. With neither key set, every B2B call is rejected. | *(blank)* |
| `B2B_API_KEY_SHA256` | Hex SHA-256 of the API key, for setups that must not put the key in the environment. | *(blank)* |
| `B2B_ALLOWED_GRAPHS` | Graph scope of the default caller: exact ids, `prefix-*`, or `*` for every graph. Empty = no graph (fail closed). | `*` |

