# Graph Viewer Application - Read-Only Visualization

A desktop reactive JavaScript application for viewing graphs with conditional edges, powered by a Java Spring Boot GraphQL backend that reads graph data from Redis.

## Architecture

### Backend (Java/Spring Boot)
- **GraphQL API** at `http://localhost:9191/graphql`
- **Redis Integration**: Reads graph data in read-only mode
- **gRPC Client**: Communicates with LangGraph service (for future features)

### Frontend (React/Electron)
- **ReactFlow**: Interactive graph visualization
- **Apollo Client**: GraphQL communication
- **Electron**: Desktop application wrapper

## Key Features

### Read-Only Graph Viewing
- View graphs stored in Redis
- No CRUD operations (as requested)
- Conditional edge visualization with color coding:
  - **Green edges**: Success conditions
  - **Red edges**: Failure conditions  
  - **Gray edges**: Unconditional transitions

## Redis Data Structure

Graphs should be stored in Redis with the following structure:

```
graph:{graphId}:meta   -> JSON: { graphName, nodeCount, status, createdAt }
graph:{graphId}:nodes  -> JSON: [{ nodeId, nodeType, handlerName, metadata }]
graph:{graphId}:edges  -> JSON: [{ source, target, condition }]
graphs:index           -> SET of graph IDs
```

Example Redis commands to add a sample graph:

```bash
# Add graph metadata
SET graph:sample-graph-1:meta '{"graphName":"Sample Graph","nodeCount":6,"status":"ACTIVE","createdAt":"2024-01-01T00:00:00Z"}'

# Add nodes
SET graph:sample-graph-1:nodes '[
  {"nodeId":"start","nodeType":"START","handlerName":"startHandler","metadata":{}},
  {"nodeId":"process","nodeType":"PROCESS","handlerName":"processHandler","metadata":{}},
  {"nodeId":"decision","nodeType":"DECISION","handlerName":"decisionHandler","metadata":{}},
  {"nodeId":"success","nodeType":"SUCCESS","handlerName":"successHandler","metadata":{}},
  {"nodeId":"failure","nodeType":"FAILURE","handlerName":"failureHandler","metadata":{}},
  {"nodeId":"end","nodeType":"END","handlerName":"endHandler","metadata":{}}
]'

# Add edges with conditions
SET graph:sample-graph-1:edges '[
  {"source":"start","target":"process","condition":null},
  {"source":"process","target":"decision","condition":null},
  {"source":"decision","target":"success","condition":"result == \"success\""},
  {"source":"decision","target":"failure","condition":"result == \"failure\""},
  {"source":"success","target":"end","condition":null},
  {"source":"failure","target":"end","condition":null}
]'

# Add to index
SADD graphs:index sample-graph-1
```

## Setup & Running

### Prerequisites
- Java 21+
- Node.js 18+
- Redis server running on localhost:6379

### Backend Setup

1. Start Redis:
```bash
redis-server
```

2. Build and run Spring Boot app:
```bash
cd /workspace/spring-langgraph-app
./gradlew bootRun
```

The GraphQL API will be available at `http://localhost:9191/graphql`

### Frontend Setup

1. Install dependencies:
```bash
cd /workspace/graph-viewer-app
npm install
```

2. Run development server:
```bash
npm run dev
```

## Usage

1. Launch the application
2. Enter a graph ID (e.g., `sample-graph-1`)
3. Click "Load Graph"
4. View the graph with conditional edges displayed

## Implementation Summary

### Backend Changes
- Added `spring-boot-starter-data-redis-reactive` dependency
- Created `RedisGraphViewService` for reading graphs from Redis
- Added `getGraphView` GraphQL query endpoint
- Extended GraphQL schema with `GraphViewPayload`, `GraphNode`, `GraphEdge` types

### Frontend Changes
- Updated GraphQL endpoint to port 9191
- Simplified to read-only view (removed CRUD operations)
- Added conditional edge rendering with color coding
- Implemented dynamic graph loading by ID
