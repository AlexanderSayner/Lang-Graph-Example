
# Spring LangGraph gRPC-GraphQL Gateway

A Spring Boot 4 (Java 21) gateway that exposes a GraphQL API on top of a LangGraph gRPC backend. 

## Quick Start with Docker

You can build and run the entire application from source using the provided multi-stage Dockerfile, without needing to install Java or Gradle locally.

```bash
# Build the Docker image
docker build -t spring-langgraph-gateway .

# Run the container (Assuming your LangGraph gRPC server is running on host port 50051)
docker run -d \
  --name langgraph-gateway \
  -p 9191:9191 \
  -e LANGGRAPH_HOST=host.docker.internal \
  -e LANGGRAPH_PORT=50051 \
  spring-langgraph-gateway
```
> **Note on `host.docker.internal`**: If your Python LangGraph gRPC server is running locally on your machine (and not in Docker), you must use `host.docker.internal` as the host so the Spring Boot container can reach your local machine. On Linux, you may need `--add-host=host.docker.internal:host-gateway`.

## Endpoints

* **GraphQL Playground (UI)**: [http://localhost:9191/graphiql](http://localhost:9191/graphiql)
* **GraphQL POST Endpoint**: `http://localhost:9191/graphql`
* **WebSocket (Subscriptions)**: `ws://localhost:9191/graphql/ws`

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
        nodeType: "tool",
        handlerName: "tavily_search",
        metadata: { maxResults: "5" }
      },
      {
        nodeId: "synthesize",
        nodeType: "llm",
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
| `LANGGRAPH_HOST` | The hostname of the LangGraph gRPC server. | `localhost` |
| `LANGGRAPH_PORT` | The port of the LangGraph gRPC server. | `50051` |
| `LANGGRAPH_EXECUTION_TIMEOUT` | Timeout for gRPC execution calls. | `60s` |
```~~

