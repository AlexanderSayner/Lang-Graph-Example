# Spring Boot + LangGraph Integration

A best-practices example of integrating Spring Boot with LangGraph using gRPC for inter-service communication, featuring a visual graph editor web UI, a copilot assistant, and persistent graph state.

## Architecture

This project demonstrates a microservices architecture where:

1. **Spring Boot Application** (`spring-langgraph-app/`, Java 21, Spring Boot 4, Gradle)
   - Provides a GraphQL API (queries, mutations, subscriptions) for clients
   - Handles graph building, execution and state management requests
   - Persists graph definitions and users in PostgreSQL (Flyway migrations), sessions and caches in Redis
   - Acts as a gRPC **client** of the Python service (graph operations) and as a gRPC **server** on port `9090`, so the Python service can call Java-side tools back
   - Implements user accounts (register/login, thread claiming) and an optional Yandex Cloud billing integration

2. **Python LangGraph Service** (`python-langgraph-service/`, Python 3.10+)
   - Implements LangGraph workflows with node handlers and a copilot agent
   - Exposes gRPC endpoints for graph operations on port `50051`
   - Uses PostgreSQL checkpoints for execution state and Redis for cache/invalidation

3. **Graph Viewer Web App** (`graph-viewer-app/`, React 19 + ReactFlow + Vite)
   - Visual editor for building and running graphs
   - Chat panel with the copilot assistant and execution state inspection
   - Proxies `/graphql` to the Spring Boot application

4. **Infrastructure**
   - **PostgreSQL 15** – graph definitions, checkpoints, users
   - **Redis** – sessions, caches, IAM token cache

## Project Structure

```
.
├── spring-langgraph-app/               # Spring Boot application
│   ├── build.gradle                    # Gradle build configuration
│   ├── gradlew                         # Gradle wrapper
│   └── src/main/
│       ├── java/org/sandbox/langgraph/
│       │   ├── LangGraphApplication.java
│       │   ├── config/                 # Configuration (gRPC, security, GraphQL, props)
│       │   ├── controller/             # GraphQL controllers
│       │   ├── service/                # Business logic (grpc/, user/, customer/, ui/)
│       │   ├── core/                   # JPA entities, repositories, graph service
│       │   ├── dto/                    # Data transfer objects
│       │   └── exception/              # Exception handlers
│       ├── proto/langgraph.proto       # Protocol Buffer definitions
│       └── resources/
│           ├── application.yml         # Application configuration
│           ├── graphql/schema.graphqls # GraphQL schema
│           └── db/migration/           # Flyway migrations
├── python-langgraph-service/           # Python LangGraph service
│   ├── app/
│   │   ├── main.py                     # gRPC server entry point
│   │   ├── services/                   # gRPC servicer implementation
│   │   ├── graph_engine/               # Node handlers & graph utilities
│   │   ├── copilot/                    # Copilot agent
│   │   ├── proto/langgraph.proto       # Protocol Buffer definitions
│   │   └── generated/                  # Generated protobuf/gRPC code
│   ├── scripts/
│   │   ├── generate_proto.sh           # Proto code generation script
│   │   └── start_local.sh              # Local startup (venv, deps, proto, run)
│   ├── requirements.txt                # Python dependencies
│   └── Dockerfile
├── graph-viewer-app/                   # React + ReactFlow visual editor
│   ├── src/                            # app.jsx, builder.jsx, copilot.jsx, components/
│   ├── vite.config.js                  # Dev server (proxies /graphql to :9191)
│   └── Dockerfile
├── scripts/init-db.sql                 # Database initialization
└── docker-compose.yml                  # Full stack orchestration
```

## Prerequisites

- Java 21
- Gradle (wrapper is included: `./gradlew`)
- Python 3.10+
- Node.js 18+ (only for the graph viewer)
- Docker & Docker Compose (recommended for infrastructure services)
- protoc (Protocol Buffers compiler) – only needed to regenerate gRPC code

## Deployment

### Install docker
```bash
# Install Docker using the official script
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Create a dedicated 'deployer' user (skip the password prompts by pressing Enter)
adduser deployer
usermod -aG docker deployer

# Create the app folder
mkdir -p /opt/myapp
chown deployer:deployer /opt/myapp
```

### Login into GitHub
```bash
su - deployer
mkdir -p ~/.ssh && chmod 700 ~/.ssh
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# Print the private key to copy it
cat ~/.ssh/github_deploy
```

### Saving env secrets
```text
Go to your GitHub Repo -> Settings -> Secrets and variables -> Actions.
Required secrets:
    VPS_HOST: Your VPS IP address.
    VPS_USER: deployer
    VPS_SSH_KEY: Private key created above.
Optional secrets (Yandex Cloud billing / LLM integration):
    YC_API_KEY, YC_FOLDER_ID, YC_BILLING_ACCOUNT_ID, YC_SA_KEY_JSON
```

### Uploading files into VPS
```bash
#If you change docker-compose.yml (e.g., add a new port or environment variable) -> You must run on local machine 
scp docker-compose.yml root@YOUR_VPS_IP:/opt/myapp/ 
```
```bash
scp -r ./scripts/ root@YOUR_VPS_IP:/opt/myapp
```

## Getting Started

### Option A: Run the full stack with Docker Compose

```bash
docker compose up -d --build
```

This starts all services:

| Service | URL / Port |
|---|---|
| Graph Viewer UI | http://localhost:8080 |
| Spring Boot GraphQL | http://localhost:9191/graphql |
| Spring Boot GraphiQL | http://localhost:9191/graphiql |
| Spring Boot gRPC server (tool execution) | localhost:9090 |
| Python LangGraph gRPC | localhost:50051 |
| PostgreSQL | localhost:7432 |
| Redis | localhost:6879 |

Optional environment variables consumed by `docker-compose.yml`: `OPENAI_API_KEY`, `YC_API_KEY`, `YC_FOLDER_ID`, `YC_BILLING_ACCOUNT_ID`, `YC_SA_KEY_JSON_B64`.

### Option B: Run services locally for development

#### 1. Start the infrastructure (PostgreSQL + Redis)

```bash
# PostgreSQL on 7432 (matches the Python service default)
docker compose up -d postgres

# Redis on 6380 (matches the default ports of both services)
docker run -d --name redis-stack -p 6380:6379 redis/redis-stack-server:latest
```

#### 2. Start the Python LangGraph Service

```bash
cd python-langgraph-service

# Creates a venv, installs dependencies, generates gRPC code and starts the server
./scripts/start_local.sh
```

The Python service starts on `localhost:50051`. Its defaults point PostgreSQL to `localhost:7432` and Redis to `localhost:6380` (see `app/config.py`, overridable via a `.env` file or environment variables).

#### 3. Build and Run the Spring Boot Application

```bash
cd spring-langgraph-app

# Point the application to the local infrastructure
export SPRING_JDBC_URL=jdbc:postgresql://localhost:7432/langgraph_db
export POSTGRES_FLYWAY_URL=jdbc:postgresql://localhost:7432/langgraph_db
export SPRING_JDBC_USERNAME=langgraph
export SPRING_JDBC_PASSWORD=langgraph_password

# Build the application
./gradlew build

# Run the application
./gradlew bootRun
```

The Spring Boot application starts on `http://localhost:9191` (GraphiQL: `http://localhost:9191/graphiql`). Flyway applies the database migrations automatically on startup.

> Note: the Yandex Cloud integration is optional. If `YC_SA_KEY_JSON` is not set, the application still starts and logs a warning; only Yandex billing queries will fail.

#### 4. Start the Graph Viewer (optional)

```bash
cd graph-viewer-app
npm install
npm run dev
```

The UI is available at `http://localhost:5173` and proxies `/graphql` to the Spring Boot application.

#### Docker images for the Python service

```bash
cd python-langgraph-service
docker build -t langgraph-gateway:snapshot .
docker run -d --name langgraph-container-snapshot -p 50051:50051 --env-file .env langgraph-gateway:snapshot
```

## GraphQL API

The application exposes a GraphQL endpoint at `http://localhost:9191/graphql` (GraphiQL IDE at `http://localhost:9191/graphiql`, subscriptions over WebSocket at `ws://localhost:9191/graphql/ws`).

### Example Queries

#### List Graphs

```graphql
query {
  listGraphs(pageSize: 10) {
    graphs {
      graphId
      graphName
      nodeCount
      status
    }
    nextPageToken
    totalCount
  }
}
```

#### Get Graph State

```graphql
query {
  getGraphState(graphId: "my-graph", threadId: "thread-1") {
    success
    state
    currentNode
    nodeHistory
  }
}
```

### Example Mutations

#### Build a Graph

```graphql
mutation {
  buildGraph(input: {
    graphId: "my-graph"
    graphName: "My First Graph"
    nodes: [
      {
        nodeId: "start"
        nodeType: "agent"
        handlerName: "chat_agent"
        metadata: { model: "gpt-4" }
      }
      {
        nodeId: "end"
        nodeType: "end"
        handlerName: "finalize"
      }
    ]
    edges: [
      { source: "start", target: "end" }
    ]
  }) {
    success
    graphId
    message
  }
}
```

#### Execute a Graph

```graphql
mutation {
  executeGraph(input: {
    graphId: "my-graph"
    input: "Hello, how can you help me?"
    context: { user_id: "123" }
  }) {
    success
    output
    state
    errorMessage
  }
}
```

#### Update Graph State

```graphql
mutation {
  updateGraphState(input: {
    graphId: "my-graph"
    threadId: "thread-1"
    stateUpdates: { last_message: "User said hello" }
  }) {
    success
    updatedState
    message
  }
}
```

### Example Subscriptions

#### Stream Graph Execution

```graphql
subscription {
  executeGraphStream(input: {
    graphId: "my-graph"
    input: "Process this request"
    context: { priority: "high" }
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

### Example Auth & Copilot

#### Register / Login

```graphql
mutation {
  login(username: "alice", password: "secret") {
    success
    message
    username
  }
}
```

#### Ask the Copilot

```graphql
mutation {
  askCopilot(
    graphId: "my-graph"
    threadId: "thread-1"
    message: "Add a node that summarizes the conversation"
  ) {
    success
    aiResponse
    totalTokens
    errorMessage
  }
}
```

## Key Features

### 1. Bidirectional gRPC Communication
- Java → Python: build/execute graphs, read and update state
- Python → Java: tool execution callbacks (`ToolGrpcService` on port `9090`)
- Efficient binary protocol with strong typing via Protocol Buffers
- Server-side streaming for real-time graph execution events

### 2. GraphQL API
- Queries, mutations and WebSocket subscriptions
- GraphiQL IDE out of the box
- Session-based authentication (register/login/logout, thread claiming)

### 3. LangGraph Integration
- Dynamic graph building from node/edge definitions
- Node handlers, conditional edges and copilot agent (OpenAI / YandexGPT)
- Checkpointed execution state persisted in PostgreSQL
- Execution history with state diffs and rewind

### 4. Visual Graph Editor
- React + ReactFlow UI for building, running and inspecting graphs
- Copilot chat panel and execution state modal

### 5. Best Practices
- Clean architecture with separation of concerns
- JPA + Flyway for database migrations, Redis for sessions and caches
- Proper error handling and configuration externalization
- Logging and observability (Actuator health/metrics endpoints)

## Development

### Regenerating gRPC Code

If you modify the `.proto` files:

**For Java:**
```bash
cd spring-langgraph-app
./gradlew generateProto
```

**For Python:**
```bash
cd python-langgraph-service
./scripts/generate_proto.sh
```

### Running Tests

```bash
# Spring Boot tests
cd spring-langgraph-app
./gradlew test

# Graph viewer build check
cd graph-viewer-app
npm run build
```

## Configuration

### Spring Boot (application.yml)

```yaml
server:
  port: 9191

spring:
  graphql:
    path: /graphql
  datasource:
    url: ${SPRING_JDBC_URL:jdbc:postgresql://localhost:5432/your_database_name}
    username: ${SPRING_JDBC_USERNAME:langgraph}
    password: ${SPRING_JDBC_PASSWORD:langgraph_password}
  grpc:
    server:
      port: 9090
    client:
      channels:
        langgraph-service:
          address: ${LANGGRAPH_SERVICE_ADDRESS:localhost:50051}
```

Key environment variables: `SPRING_JDBC_URL`, `POSTGRES_FLYWAY_URL`, `SPRING_JDBC_USERNAME`, `SPRING_JDBC_PASSWORD`, `SPRING_REDIS_HOST`, `SPRING_REDIS_PORT`, `LANGGRAPH_SERVICE_ADDRESS`, `YC_BILLING_ACCOUNT_ID`, `YC_SA_KEY_JSON`.

### Python Service

Configured via environment variables or a `.env` file in `python-langgraph-service/` (see `python-langgraph-service/README.md` for the full list): `SERVER_PORT`, `LOG_LEVEL`, `DATABASE_URL`, `REDIS_URL`, `OPENAI_API_KEY`, `YC_API_KEY`, `YC_FOLDER_ID`, `YC_COMPLETION_MODE`, `TOOL_SERVICE_HOST`, `TOOL_SERVICE_PORT`.

## License

GNU General Public License v3.0 – see [LICENSE](LICENSE).
