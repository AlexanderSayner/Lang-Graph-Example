# Graph Viewer Desktop App

A reactive desktop JavaScript application for visualizing graphs with conditional edges, powered by a Java Spring Boot GraphQL backend.

## Features

- **Interactive Graph Visualization**: View and interact with graphs using ReactFlow
- **Conditional Edges**: Visualize conditional edge paths with different colors and labels
- **Real-time Updates**: Subscribe to graph execution events via GraphQL subscriptions
- **Node Status Tracking**: See active nodes during graph execution
- **Desktop Application**: Built with Electron for cross-platform desktop support

## Tech Stack

### Frontend
- React 18
- ReactFlow (graph visualization)
- Apollo Client (GraphQL client)
- GraphQL-Ws (WebSocket subscriptions)
- Vite (build tool)
- Electron (desktop app)

### Backend
- Java Spring Boot
- Spring GraphQL
- gRPC (for internal service communication)

## Project Structure

```
graph-viewer-app/
├── main.js              # Electron main process
├── package.json         # Dependencies and scripts
├── vite.config.js       # Vite configuration
├── index.html           # HTML entry point
└── src/
    ├── main.jsx         # React entry point
    ├── App.jsx          # Main React component
    ├── GraphViewer.jsx  # Graph visualization component
    ├── apollo-client.js # GraphQL client setup
    └── index.css        # Styles
```

## Getting Started

### Prerequisites

- Node.js 18+
- npm or yarn
- Java 21+ (for backend)

### Installation

1. Install dependencies:
```bash
cd graph-viewer-app
npm install
```

2. Start the development server:
```bash
npm run dev
```

3. Run as desktop app:
```bash
npm start
```

### Backend Setup

Ensure the Spring Boot GraphQL backend is running on `http://localhost:8080/graphql`.

## Usage

1. **Create Sample Graph**: Click "Create Sample Graph" to create a graph with conditional edges
2. **Execute Graph**: Click "Execute Graph" to run the graph and see real-time updates
3. **View Conditional Paths**: Conditional edges are shown in different colors:
   - Green: Success path (`result == "success"`)
   - Red: Failure path (`result == "failure"`)
4. **Monitor Execution**: Watch node status changes in real-time via the mini-map and status bar

## GraphQL API

The app connects to the following GraphQL operations:

- `listGraphs`: Fetch available graphs
- `buildGraph`: Create a new graph
- `executeGraph`: Execute a graph
- `executeGraphStream`: Subscribe to execution events
- `deleteGraph`: Remove a graph

## License

MIT
