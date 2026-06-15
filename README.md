# Spring Boot + LangGraph Integration

A best-practices example of integrating Spring Boot with LangGraph using gRPC for inter-service communication.

## Architecture

This project demonstrates a microservices architecture where:

1. **Spring Boot Application** (Java 25, Gradle)
   - Provides a GraphQL API for clients
   - Handles graph building and execution requests
   - Communicates with the Python service via gRPC

2. **Python LangGraph Service**
   - Implements LangGraph workflows
   - Exposes gRPC endpoints for graph operations
   - Manages graph state and execution

## Project Structure

```
/workspace
├── spring-langgraph-app/          # Spring Boot application
│   ├── build.gradle               # Gradle build configuration
│   ├── settings.gradle
│   └── src/main/
│       ├── java/com/example/langgraph/
│       │   ├── LangGraphApplication.java
│       │   ├── config/            # Configuration classes
│       │   ├── controller/        # GraphQL controllers
│       │   ├── service/           # Business logic
│       │   ├── grpc/              # gRPC client stubs (generated)
│       │   ├── dto/               # Data transfer objects
│       │   └── exception/         # Exception handlers
│       ├── proto/                 # Protocol Buffer definitions
│       └── resources/
│           ├── application.yml    # Application configuration
│           └── graphql/           # GraphQL schema
└── python-langgraph-service/      # Python LangGraph service
    ├── app/
    │   └── server.py              # gRPC server implementation
    ├── proto/                     # Protocol Buffer definitions
    ├── requirements.txt           # Python dependencies
    └── generate_proto.sh          # Proto code generation script
```

## Prerequisites

- Java 25
- Gradle 8.x
- Python 3.10+
- protoc (Protocol Buffers compiler)

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
Add these 3 secrets:
    VPS_HOST: Your VPS IP address.
    VPS_USER: deployer
    VPS_SSH_KEY:
```

### Uploading files into VPS
```bash
#If you change docker-compose.yml (e.g., add a new port or environment variable) -> You must run on local machine 
scp docker-compose.yml root@YOUR_VPS_IP:/opt/myapp/ 
```
```bash
scp -r ./scripts/ root@80.66.78.40:/opt/myapp
```

## Getting Started

### Run Redis server
```shell
docker run -d --name redis-stack -p 6380:6379 redis/redis-stack-server:latest
```

### 1. Start the Python LangGraph Service

```bash
cd /workspace/python-langgraph-service

# Install dependencies
pip install -r requirements.txt

# Generate gRPC code from proto files
chmod +x generate_proto.sh
./generate_proto.sh

# Start the gRPC server
python -m app.server
```

The Python service will start on `localhost:50051`.

#### Build python service docker image
```shell
docker build -t langgraph-gateway:snapshot .
```
```shell
docker run -d --name langgraph-container-snapshot -p 50051:50051 langgraph-gateway:snapshot
```

### 2. Build and Run the Spring Boot Application

```bash
cd /workspace/spring-langgraph-app

# Build the application
./gradlew build

# Run the application
./gradlew bootRun
```

The Spring Boot application will start on `http://localhost:8080`.

## GraphQL API

The application exposes a GraphQL endpoint at `http://localhost:8080/graphql`.

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
  buildGraph(
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
  ) {
    success
    graphId
    message
  }
}
```

#### Execute a Graph

```graphql
mutation {
  executeGraph(
    graphId: "my-graph"
    input: "Hello, how can you help me?"
    context: { user_id: "123" }
  ) {
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
  updateGraphState(
    graphId: "my-graph"
    threadId: "thread-1"
    stateUpdates: { last_message: "User said hello" }
  ) {
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
  executeGraphStream(
    graphId: "my-graph"
    input: "Process this request"
    context: { priority: "high" }
  ) {
    eventType
    nodeId
    output
    state
    timestamp
    errorMessage
  }
}
```

## Key Features

### 1. gRPC Communication
- Efficient binary protocol for service-to-service communication
- Strong typing with Protocol Buffers
- Streaming support for real-time graph execution events

### 2. GraphQL API
- Flexible query language for clients
- Real-time updates via subscriptions
- Type-safe schema

### 3. LangGraph Integration
- Dynamic graph building
- State management
- Streaming execution results

### 4. Best Practices
- Clean architecture with separation of concerns
- Reactive programming with Project Reactor
- Proper error handling
- Configuration externalization
- Logging and observability

## Development

### Regenerating gRPC Code

If you modify the `.proto` files:

**For Java:**
```bash
cd /workspace/spring-langgraph-app
./gradlew generateProto
```

**For Python:**
```bash
cd /workspace/python-langgraph-service
./generate_proto.sh
```

### Running Tests

```bash
# Spring Boot tests
cd /workspace/spring-langgraph-app
./gradlew test

# Python tests (if added)
cd /workspace/python-langgraph-service
pytest
```

## Configuration

### Spring Boot (application.yml)

```yaml
server:
  port: 8080

spring:
  graphql:
    path: /graphql

langgraph:
  service:
    host: localhost
    port: 50051
```

### Python Service

The Python service can be configured via environment variables or command-line arguments.

## License

MIT License
