# Production-Ready Java GraphQL API for Graph Management: Comprehensive Analysis & User Guide

## Executive Summary

This document provides a deep architectural analysis and comprehensive user guide for building a production-ready GraphQL API in Java using Spring Boot 4, based on the `spring-langgraph-app` reference implementation. This gateway application exposes a GraphQL interface on top of a LangGraph gRPC backend, enabling graph creation, execution, state management, real-time streaming capabilities, and graph visualization with coordinate storage.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Technology Stack](#technology-stack)
3. [Core Components Deep Dive](#core-components-deep-dive)
4. [GraphQL Schema Design](#graphql-schema-design)
5. [Building the Application](#building-the-application)
6. [Configuration Guide](#configuration-guide)
7. [API Usage Examples](#api-usage-examples)
8. [Production Best Practices](#production-best-practices)
9. [Error Handling Strategy](#error-handling-strategy)
10. [Testing Strategy](#testing-strategy)
11. [Deployment Guide](#deployment-guide)
12. [Monitoring & Observability](#monitoring--observability)
13. [Security Considerations](#security-considerations)
14. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

### High-Level Architecture

```
┌─────────────────┐     GraphQL      ┌──────────────────────┐     gRPC      ┌───────────────────┐
│   GraphQL       │ ◄──────────────► │  Spring Boot Gateway │ ◄───────────► │  LangGraph        │
│   Client        │    HTTP/WS       │  (Java 21)           │    Protocol   │  gRPC Service     │
│   (Frontend)    │                  │  - GraphQL API       │               │  (Python)         │
│                 │                  │  - Reactive Stack    │               │                   │
└─────────────────┘                  │  - Redis Integration │               └───────────────────┘
                                     └──────────────────────┘
                                              │
                                              ▼
                                     ┌──────────────────────┐
                                     │      Redis           │
                                     │  - Graph Metadata    │
                                     │  - Coordinates       │
                                     └──────────────────────┘
```

### Architectural Patterns

1. **API Gateway Pattern**: The Spring Boot application acts as a GraphQL gateway, abstracting the underlying gRPC microservice.
2. **Reactive Programming**: Built on Project Reactor (Mono/Flux) for non-blocking I/O operations.
3. **DTO Layer Separation**: Clear separation between GraphQL input/output types and gRPC messages.
4. **Mapper Pattern**: MapStruct and manual mappers for efficient object mapping between layers.
5. **Exception Translation**: Centralized exception handling with proper GraphQL error type mapping.
6. **Service Separation**: Distinct services for gRPC communication, Redis graph viewing, and coordinate management.

---

## Technology Stack

### Core Dependencies

| Technology | Version | Purpose |
|------------|---------|---------|
| Java | 21 | Language runtime with virtual threads support |
| Spring Boot | 4.0.5 | Application framework |
| Spring GraphQL | Included | GraphQL API implementation |
| Spring gRPC | 1.0.2 | gRPC client integration |
| Project Reactor | Included | Reactive programming |
| Redis | Latest | Graph metadata storage |
| MapStruct | 1.6.3 | Object mapping |
| Lombok | Latest | Boilerplate reduction |
| GraphQL Extended Scalars | 22.0 | JSON & Long scalar support |
| Jackson | Included | JSON processing |
| Protobuf | 3.25.5 | Protocol buffers |
| gRPC | 1.75.0 | RPC framework |

### Build Configuration (build.gradle)

```gradle
plugins {
    id 'java'
    id 'org.springframework.boot' version '4.0.5'
    id 'io.spring.dependency-management' version '1.1.7'
    id 'com.google.protobuf' version '0.9.5'
}

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(21)
    }
}
```

---

## Core Components Deep Dive


### 1. GraphQL Controller Layer

**File**: `LangGraphController.java`

The controller serves as the entry point for all GraphQL operations:

```java
@Controller
@Validated
public class LangGraphController {

    // Query mappings
    public Mono<@NonNull GraphListPayload> listGraphs(int pageSize, String pageToken);
    public Mono<@NonNull GraphStatePayload> getGraphState(String graphId, String threadId);
    public Mono<@NonNull GraphViewPayload> getGraphView(String graphId);

    // Mutation mappings
    public Mono<@NonNull BuildGraphPayload> buildGraph(BuildGraphInput input);
    public Mono<@NonNull ExecuteGraphPayload> executeGraph(ExecuteGraphInput input);
    public Mono<@NonNull UpdateGraphStatePayload> updateGraphState(UpdateGraphStateInput input);
    public Mono<@NonNull DeleteGraphPayload> deleteGraph(String graphId);
    public Mono<@NonNull GraphViewPayload> saveGraphCoordinates(String graphId, List<NodePositionInput> positions);

    // Subscription mappings
    public Publisher<GraphExecutionEventPayload> executeGraphStream(ExecuteGraphInput input);
}
```

**Key Features**:
- Uses `@Validated` for automatic input validation
- Returns reactive types (`Mono`, `Publisher`) for non-blocking operations
- Implements accumulator pattern for stream aggregation in synchronous execution
- Handles graph visualization queries and coordinate saving mutations

### 2. Service Layer (gRPC Integration)

**File**: `LangGraphGrpcService.java`

Handles communication with the backend gRPC service:

```java
@Service
public class LangGraphGrpcService {

    // Unary operations (using futureStub)
    public Mono<@NonNull BuildGraphPayload> buildGraph(BuildGraphInput input);
    public Mono<@NonNull GraphStatePayload> getGraphState(String graphId, String threadId);
    public Mono<@NonNull UpdateGraphStatePayload> updateGraphState(UpdateGraphStateInput input);
    public Mono<@NonNull GraphListPayload> listGraphs(int pageSize, String pageToken);
    public Mono<@NonNull DeleteGraphPayload> deleteGraph(String graphId);

    // Streaming operations (using asyncStub)
    public Flux<@NonNull GraphExecutionEventPayload> executeGraphStream(ExecuteGraphInput input, boolean stream);
}
```

**Best Practices Implemented**:
- Separate stubs for unary (futureStub) and streaming (asyncStub) calls
- Proper error translation from gRPC Status codes to domain exceptions
- Uses `boundedElastic()` scheduler for blocking gRPC operations

### 3. Redis Services

**File**: `RedisGraphViewService.java`

Provides read-only access to graph visualization data from Redis:

```java
@Service
public class RedisGraphViewService {

    public Mono<@NonNull GraphViewData> getGraphViewData(String graphId);
}
```

**File**: `RedisGraphCoordinatesService.java`

Manages graph node coordinates storage in Redis:

```java
@Service
public class RedisGraphCoordinatesService {

    public Mono<@NonNull Boolean> saveCoordinates(String graphId, Map<String, Map<String, Double>> coordinates);
    public Mono<@NonNull Map<String, Map<String, Double>>> getCoordinates(String graphId);
}
```

### 4. Mapper Layer

**File**: `GraphGrpcMapper.java` (MapStruct)

Handles conversion between GraphQL DTOs and gRPC messages:

```java
@Mapper(componentModel = "spring")
public interface GraphGrpcMapper {

    // GraphQL Input -> gRPC Request
    BuildGraphRequest toBuildGraphRequest(BuildGraphInput input);
    NodeDefinition toNodeDefinition(NodeInput node);
    EdgeDefinition toEdgeDefinition(EdgeInput edge);
    ExecuteGraphRequest toExecuteGraphRequest(ExecuteGraphInput input);
    ExecuteGraphRequest toStreamExecuteGraphRequest(ExecuteGraphInput input);
    GetGraphStateRequest toGetGraphStateRequest(String graphId, String threadId);
    UpdateGraphStateRequest toUpdateGraphStateRequest(UpdateGraphStateInput input);
    ListGraphsRequest toListGraphsRequest(int pageSize, String pageToken);
    DeleteGraphRequest toDeleteGraphRequest(String graphId);

    // gRPC Response -> GraphQL Payload
    GraphListPayload toGraphListPayload(ListGraphsResponse response);
    GraphSummary toGraphSummary(GraphInfo graph);
    GraphStatePayload toGraphStatePayload(GetGraphStateResponse response);
    BuildGraphPayload toBuildGraphPayload(BuildGraphResponse response);
    GraphExecutionEventPayload toGraphExecutionEventPayload(ExecuteGraphResponse response);
    UpdateGraphStatePayload toUpdateGraphStatePayload(UpdateGraphStateResponse response);
    DeleteGraphPayload toDeleteGraphPayload(DeleteGraphResponse response);

    // Helper methods
    Map<String, String> convertToStringMap(Map<String, Object> map);
}
```

**File**: `RedisGraphStorageMapper.java` (Manual mapper)

Converts Redis graph data to GraphQL payload:

```java
@Component
public class RedisGraphStorageMapper {

    public GraphViewPayload toPayload(GraphViewData data);
}
```

### 5. Exception Handling

**File**: `GraphQlExceptionHandler.java`

Centralized exception resolution with proper GraphQL error types:

```java
@Component
public class GraphQlExceptionHandler extends DataFetcherExceptionResolverAdapter {

    @Override
    protected GraphQLError resolveToSingleError(Throwable ex, DataFetchingEnvironment env);
}
```

**Custom Exception Hierarchy** (`LangGraphException.java`):

```java
@Getter
public sealed class LangGraphException extends RuntimeException permits
        GraphNotFoundException,
        GraphBuildException,
        GraphExecutionException,
        GraphStateException {

    private final String errorCode;
}
```

**Exception Types**:
- `GraphNotFoundException`: Thrown when a requested graph does not exist
- `GraphBuildException`: Thrown when graph building fails
- `GraphExecutionException`: Thrown when graph execution encounters an error
- `GraphStateException`: Thrown when state operations fail

### 3. Mapper Layer (MapStruct)

**File**: `GraphGrpcMapper.java`

Handles conversion between GraphQL DTOs and gRPC messages:

```java
@Mapper(componentModel = "spring")
public interface GraphGrpcMapper {
    
    default BuildGraphRequest toBuildGraphRequest(BuildGraphInput input) {
        BuildGraphRequest.Builder builder = BuildGraphRequest.newBuilder()
                .setGraphId(input.graphId())
                .setGraphName(input.graphName());
        
        if (input.nodes() != null) {
            builder.addAllNodes(input.nodes().stream()
                .map(this::toNodeDefinition).toList());
        }
        
        if (input.config() != null) {
            builder.putAllConfig(convertToStringMap(input.config()));
        }
        
        return builder.build();
    }
    
    default Map<String, String> convertToStringMap(Map<String, Object> map) {
        if (map == null) return null;
        return map.entrySet().stream()
            .collect(Collectors.toMap(
                Map.Entry::getKey,
                e -> e.getValue() != null ? e.getValue().toString() : null
            ));
    }
}
```

### 4. Exception Handling

**File**: `GraphQlExceptionHandler.java`

Centralized exception resolution with proper GraphQL error types:

```java
@Component
public class GraphQlExceptionHandler extends DataFetcherExceptionResolverAdapter {
    
    @Override
    protected GraphQLError resolveToSingleError(
            Throwable ex, DataFetchingEnvironment env) {
        
        return switch (ex) {
            case LangGraphException.GraphNotFoundException e -> 
                GraphqlErrorBuilder.newError()
                    .message(e.getMessage())
                    .errorType(ErrorType.NOT_FOUND)
                    .extensions(Map.of("code", e.getErrorCode()))
                    .build();
            
            case LangGraphException.GraphBuildException e -> 
                GraphqlErrorBuilder.newError()
                    .message(e.getMessage())
                    .errorType(ErrorType.BAD_REQUEST)
                    .build();
            
            default -> GraphqlErrorBuilder.newError()
                    .message("Internal server error")
                    .errorType(ErrorType.INTERNAL_ERROR)
                    .build();
        };
    }
}
```

**Custom Exception Hierarchy**:

```java
@Getter
public sealed class LangGraphException extends RuntimeException permits
        GraphNotFoundException,
        GraphBuildException,
        GraphExecutionException,
        GraphStateException {
    
    private final String errorCode;
    
    public static final class GraphNotFoundException extends LangGraphException {
        public GraphNotFoundException(String graphId) {
            super("Graph not found: " + graphId, "GRAPH_NOT_FOUND");
        }
    }
}
```

### 5. Redis Integration

**File**: `RedisGraphViewService.java`

Provides read-only access to graph visualization data:

```java
@Service
public class RedisGraphViewService {
    
    private static final String GRAPH_META_KEY_PREFIX = "graph_def:";
    
    public Mono<@NonNull GraphViewData> getGraphViewData(String graphId) {
        String key = GRAPH_META_KEY_PREFIX + graphId;
        
        return redisOperations.opsForValue().get(key)
            .switchIfEmpty(Mono.error(new RuntimeException("Graph not found")))
            .flatMap(data -> {
                // Parse JSON and transform to GraphViewData
                Map<String, Object> graphData = objectMapper.readValue(data, typeRef);
                return Mono.just(new GraphViewData(...));
            });
    }
}
```

---

## GraphQL Schema Design

### Complete Schema (schema.graphqls)

```graphql
# Query Operations
type Query {
    listGraphs(pageSize: Int! = 10, pageToken: String): GraphListPayload!
    getGraphState(graphId: String!, threadId: String): GraphStatePayload!
    getGraphView(graphId: String!): GraphViewPayload!
}

# Mutation Operations
type Mutation {
    buildGraph(input: BuildGraphInput!): BuildGraphPayload!
    executeGraph(input: ExecuteGraphInput!): ExecuteGraphPayload!
    updateGraphState(input: UpdateGraphStateInput!): UpdateGraphStatePayload!
    deleteGraph(graphId: String!): DeleteGraphPayload!
}

# Subscription Operations
type Subscription {
    executeGraphStream(input: ExecuteGraphInput!): GraphExecutionEventPayload!
}

# Input Types
input BuildGraphInput {
    graphId: String!
    graphName: String!
    nodes: [NodeInput!]!
    edges: [EdgeInput!]!
    config: JSON
}

input NodeInput {
    nodeId: String!
    nodeType: String!
    handlerName: String!
    metadata: JSON
}

input EdgeInput {
    source: String!
    target: String!
    condition: String
}

# Output Payloads
type GraphExecutionEventPayload {
    eventType: String!
    nodeId: String
    output: String
    state: JSON
    timestamp: Long!
    errorMessage: String
}

# Custom Scalars
scalar JSON
scalar Long
```

### Schema Design Principles

1. **Input/Object Separation**: Distinct types for inputs and outputs prevent coupling
2. **Payload Pattern**: All mutations return structured payloads with success flags
3. **Pagination Support**: `pageInfo` and `pageToken` for cursor-based pagination
4. **Nullable vs Non-Null**: Careful use of `!` to indicate required fields
5. **Custom Scalars**: JSON for flexible metadata, Long for timestamps

---

## Building the Application

### Prerequisites

- **JDK 21+**: Required for Spring Boot 4 and modern Java features
- **Gradle 8+**: Build automation tool
- **Protobuf Compiler**: For generating gRPC code
- **Docker** (optional): For containerized builds

### Step-by-Step Build Process

#### Option 1: Local Build with Gradle

```bash
# Navigate to project directory
cd spring-langgraph-app

# Generate protobuf/gRPC code
./gradlew generateProto

# Compile the application
./gradlew compileJava

# Run tests
./gradlew test

# Build JAR file
./gradlew bootJar

# The JAR will be located at:
# build/libs/spring-langgraph-app-0.0.1-SNAPSHOT.jar
```

#### Option 2: Docker Build (Recommended for Production)

```dockerfile
# Multi-stage Dockerfile
FROM gradle:8.5-jdk21 AS build
WORKDIR /app
COPY . .
RUN gradle bootJar --no-daemon

FROM eclipse-temurin:21-jre-alpine
WORKDIR /app
COPY --from=build /app/build/libs/*.jar app.jar
EXPOSE 9191
ENTRYPOINT ["java", "-jar", "app.jar"]
```

**Build and Run**:

```bash
# Build the image
docker build -t spring-langgraph-gateway .

# Run the container
docker run -d \
  --name langgraph-gateway \
  -p 9191:9191 \
  -e LANGGRAPH_HOST=host.docker.internal \
  -e LANGGRAPH_PORT=50051 \
  spring-langgraph-gateway
```

#### Option 3: Docker Compose (Full Stack)

```yaml
version: '3.8'
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6380:6379"
  
  langgraph-grpc:
    build: ../python-langgraph-service
    ports:
      - "50051:50051"
  
  graphql-gateway:
    build: ./spring-langgraph-app
    ports:
      - "9191:9191"
    environment:
      - SPRING_DATA_REDIS_HOST=redis
      - SPRING_GRPC_CLIENT_CHANNELS_LANGGRAPH-SERVICE_ADDRESS=langgraph-grpc:50051
    depends_on:
      - redis
      - langgraph-grpc
```

---

## Configuration Guide

### application.yml Structure

```yaml
server:
  port: 9191
  shutdown: graceful  # Graceful shutdown for production

spring:
  application:
    name: spring-langgraph-app
  
  lifecycle:
    timeout-per-shutdown-phase: 30s
  
  graphql:
    http:
      enabled: true
    path: /graphql
    graphiql:
      enabled: true  # Disable in production
      path: /graphiql
    websocket:
      path: /graphql/ws
  
  jackson:
    default-property-inclusion: non_null
  
  data:
    redis:
      host: localhost
      port: 6380
      timeout: 2000ms
  
  grpc:
    client:
      channels:
        langgraph-service:
          address: localhost:50051
          negotiation-type: PLAINTEXT  # Use TLS in production
          enable-keep-alive: true
          keep-alive-time: 30s
          keep-alive-without-calls: true

management:
  endpoints:
    web:
      exposure:
        include: health,info,metrics,prometheus
  endpoint:
    health:
      show-details: when_authorized

logging:
  level:
    root: INFO
    org.sandbox.langgraph: DEBUG
    org.springframework.graphql: DEBUG
    io.grpc: INFO
  pattern:
    console: "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n"
```

### Environment Variable Overrides

For production deployments, use environment variables:

```bash
# Server configuration
export SERVER_PORT=9191

# Redis configuration
export SPRING_DATA_REDIS_HOST=redis-cluster.prod
export SPRING_DATA_REDIS_PORT=6379
export SPRING_DATA_REDIS_PASSWORD=${REDIS_PASSWORD}

# gRPC configuration
export SPRING_GRPC_CLIENT_CHANNELS_LANGGRAPH-SERVICE_ADDRESS=langgraph.prod:50051
export SPRING_GRPC_CLIENT_CHANNELS_LANGGRAPH-SERVICE_NEGOTIATION-TYPE=TLS

# Feature flags
export SPRING_GRAPHQL_GRAPHIQL_ENABLED=false
```

### GraphQL Configuration Bean

```java
@Configuration
public class GraphQlConfig {
    
    @Bean
    public RuntimeWiringConfigurer runtimeWiringConfigurer() {
        return wiringBuilder -> wiringBuilder
            .scalar(ExtendedScalars.Json)
            .scalar(ExtendedScalars.GraphQLLong);
    }
}
```

### gRPC Configuration Bean

```java
@Configuration
@Slf4j
public class GrpcConfig {
    
    @Bean
    public LangGraphServiceGrpc.LangGraphServiceFutureStub futureStub(
            GrpcChannelFactory channelFactory) {
        ManagedChannel channel = channelFactory.createChannel("langgraph-service");
        log.info("Created future stub channel: {}", channel.getState(true));
        return LangGraphServiceGrpc.newFutureStub(channel);
    }
    
    @Bean
    public LangGraphServiceGrpc.LangGraphServiceStub asyncStub(
            GrpcChannelFactory channelFactory) {
        ManagedChannel channel = channelFactory.createChannel("langgraph-service");
        return LangGraphServiceGrpc.newStub(channel);
    }
}
```

---

## API Usage Examples

### 1. Building a Graph

**Mutation**: Create a new graph with nodes and edges

```graphql
mutation BuildSupportTriageGraph {
  buildGraph(input: {
    graphId: "support-triage-v1",
    graphName: "Support Ticket Triage",
    nodes: [
      {
        nodeId: "triage",
        nodeType: "START",
        handlerName: "triageHandler",
        metadata: { priority: "high" }
      },
      {
        nodeId: "technical",
        nodeType: "ACTION",
        handlerName: "technicalHandler"
      },
      {
        nodeId: "billing",
        nodeType: "ACTION",
        handlerName: "billingHandler"
      },
      {
        nodeId: "general",
        nodeType: "ACTION",
        handlerName: "generalHandler"
      },
      {
        nodeId: "resolve",
        nodeType: "END",
        handlerName: "resolveHandler"
      }
    ],
    edges: [
      {
        source: "triage",
        target: "technical",
        condition: "ticket_type == 'technical'"
      },
      {
        source: "triage",
        target: "billing",
        condition: "ticket_type == 'billing'"
      },
      {
        source: "triage",
        target: "general",
        condition: "ticket_type == 'general'"
      },
      { source: "technical", target: "resolve" },
      { source: "billing", target: "resolve" },
      { source: "general", target: "resolve" }
    ],
    config: {
      timeout: "30s",
      retryCount: "3"
    }
  }) {
    success
    graphId
    message
  }
}
```

**Expected Response**:
```json
{
  "data": {
    "buildGraph": {
      "success": true,
      "graphId": "support-triage-v1",
      "message": "Graph built successfully"
    }
  }
}
```

### 2. Executing a Graph (Synchronous)

**Mutation**: Execute and wait for completion

```graphql
mutation ExecuteGraphSync {
  executeGraph(input: {
    graphId: "support-triage-v1",
    threadId: "thread-abc-123",
    input: "My billing statement is incorrect",
    context: {
      userId: "user-456",
      ticketId: "TKT-789"
    }
  }) {
    success
    output
    state
    errorMessage
  }
}
```

**Expected Response**:
```json
{
  "data": {
    "executeGraph": {
      "success": true,
      "output": "Billing issue resolved. Refund processed.",
      "state": {
        "ticket_type": "billing",
        "resolution": "refund_processed",
        "iteration_count": "1"
      },
      "errorMessage": null
    }
  }
}
```

### 3. Streaming Graph Execution (Subscription)

**Subscription**: Real-time event streaming via WebSocket

```graphql
subscription StreamGraphExecution {
  executeGraphStream(input: {
    graphId: "support-triage-v1",
    threadId: "thread-xyz-789",
    input: "Technical issue with API integration",
    context: {
      userId: "user-789",
      priority: "critical"
    }
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

**Stream Events**:
```json
// Event 1: START
{
  "eventType": "START",
  "nodeId": null,
  "output": null,
  "state": { "input": "Technical issue..." },
  "timestamp": 1698765432000,
  "errorMessage": null
}

// Event 2: NODE_START
{
  "eventType": "NODE_START",
  "nodeId": "triage",
  "output": null,
  "state": { "current_step": "triage" },
  "timestamp": 1698765432100,
  "errorMessage": null
}

// Event 3: NODE_END
{
  "eventType": "NODE_END",
  "nodeId": "triage",
  "output": "Categorized as technical",
  "state": { "ticket_type": "technical" },
  "timestamp": 1698765432500,
  "errorMessage": null
}

// Event 4: END
{
  "eventType": "END",
  "nodeId": "resolve",
  "output": "Issue escalated to engineering team",
  "state": { "status": "escalated" },
  "timestamp": 1698765435000,
  "errorMessage": null
}
```

### 4. Getting Graph State

**Query**: Retrieve current execution state

```graphql
query GetGraphState {
  getGraphState(
    graphId: "support-triage-v1",
    threadId: "thread-abc-123"
  ) {
    success
    state
    currentNode
    nodeHistory
  }
}
```

**Response**:
```json
{
  "data": {
    "getGraphState": {
      "success": true,
      "state": {
        "ticket_type": "billing",
        "resolution": "refund_processed"
      },
      "currentNode": "resolve",
      "nodeHistory": ["triage", "billing", "resolve"]
    }
  }
}
```

### 5. Updating Graph State

**Mutation**: Manually modify state

```graphql
mutation UpdateGraphState {
  updateGraphState(input: {
    graphId: "support-triage-v1",
    threadId: "thread-abc-123",
    stateUpdates: {
      user_feedback: "Please expedite this request",
      escalation_level: "2",
      requires_manager_approval: "true"
    }
  }) {
    success
    updatedState
    message
  }
}
```

### 6. Listing Graphs with Pagination

**Query**: Paginated graph listing

```graphql
query ListGraphs {
  listGraphs(pageSize: 5, pageToken: "cursor-abc123") {
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

**Response**:
```json
{
  "data": {
    "listGraphs": {
      "totalCount": 23,
      "pageInfo": {
        "hasNextPage": true,
        "endCursor": "cursor-def456"
      },
      "graphs": [
        {
          "graphId": "support-triage-v1",
          "graphName": "Support Ticket Triage",
          "nodeCount": 5,
          "status": "ACTIVE",
          "createdAt": "2024-01-15T10:30:00Z"
        }
      ]
    }
  }
}
```

### 7. Getting Graph Visualization Data

**Query**: Retrieve graph structure for UI rendering

```graphql
query GetGraphView {
  getGraphView(graphId: "support-triage-v1") {
    success
    graphId
    graphName
    status
    nodes {
      nodeId
      nodeType
      handlerName
      metadata
      position {
        x
        y
      }
    }
    edges {
      source
      target
      condition
      label
    }
    message
  }
}
```

### 8. Deleting a Graph

**Mutation**: Remove graph and associated data

```graphql
mutation DeleteGraph {
  deleteGraph(graphId: "support-triage-v1") {
    success
    message
  }
}
```

---

## Production Best Practices

### 1. Performance Optimization

#### Connection Pooling
```yaml
spring:
  grpc:
    client:
      channels:
        langgraph-service:
          address: langgraph.prod:50051
          max-inbound-message-size: 10MB
          keep-alive-time: 30s
          keep-alive-without-calls: true
          idle-timeout: 5m
```

#### Caching Strategy
```java
@Service
public class CachedGraphViewService {
    
    @Cacheable(value = "graphViews", key = "#graphId", unless = "#result.success == false")
    public Mono<GraphViewPayload> getCachedGraphView(String graphId) {
        return redisGraphViewService.getGraphViewData(graphId)
            .map(redisGraphStorageMapper::toPayload);
    }
    
    @CacheEvict(value = "graphViews", key = "#graphId")
    public Mono<BuildGraphPayload> buildAndInvalidate(BuildGraphInput input) {
        return langGraphService.buildGraph(input);
    }
}
```

#### Reactive Backpressure
```java
public Flux<GraphExecutionEventPayload> executeGraphStream(...) {
    return Flux.<ExecuteGraphResponse>create(emitter -> { ... })
        .map(mapper::toGraphExecutionEventPayload)
        .subscribeOn(Schedulers.boundedElastic())
        .onBackpressureBuffer(1000)  // Prevent memory issues
        .timeout(Duration.ofMinutes(30));  // Prevent hanging subscriptions
}
```

### 2. Security Hardening

#### Disable GraphiQL in Production
```yaml
spring:
  graphql:
    graphiql:
      enabled: false
```

#### Implement Authentication
```java
@Configuration
@EnableWebSecurity
public class SecurityConfig {
    
    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        return http
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/graphql", "/graphql/ws").authenticated()
                .anyRequest().permitAll()
            )
            .oauth2ResourceServer(oauth2 -> oauth2.jwt(Customizer.withDefaults()))
            .build();
    }
}
```

#### Input Validation
```java
public record BuildGraphInput(
    @NotBlank(message = "Graph ID is required")
    @Size(max = 100, message = "Graph ID must be under 100 characters")
    @Pattern(regexp = "^[a-zA-Z0-9-_]+$", message = "Invalid graph ID format")
    String graphId,
    
    @Valid
    @Size(min = 1, max = 100, message = "Must have between 1 and 100 nodes")
    List<NodeInput> nodes
) {}
```

### 3. Resilience Patterns

#### Circuit Breaker
```java
@Service
public class ResilientLangGraphService {
    
    @CircuitBreaker(name = "langgraphService", fallbackMethod = "fallbackBuildGraph")
    public Mono<BuildGraphPayload> buildGraph(BuildGraphInput input) {
        return langGraphService.buildGraph(input);
    }
    
    public Mono<BuildGraphPayload> fallbackBuildGraph(
            BuildGraphInput input, Throwable ex) {
        log.error("Circuit breaker triggered for buildGraph", ex);
        return Mono.just(new BuildGraphPayload(false, null, "Service temporarily unavailable"));
    }
}
```

#### Retry with Exponential Backoff
```java
@Bean
public Retry retryConfig() {
    return Retry.backoff(3, Duration.ofSeconds(1))
        .maxBackoff(Duration.ofSeconds(10))
        .transientErrors(true)
        .retryOn(exception -> exception instanceof StatusRuntimeException sre &&
            sre.getStatus().getCode() == Status.Code.UNAVAILABLE);
}
```

#### Timeout Configuration
```java
public Mono<BuildGraphPayload> buildGraph(BuildGraphInput input) {
    return Mono.fromCallable(() -> { ... })
        .timeout(Duration.ofSeconds(30))
        .doOnError(TimeoutException.class, 
            ex -> log.error("Build graph timed out for {}", input.graphId()));
}
```

### 4. Logging & Auditing

#### Structured Logging
```yaml
logging:
  pattern:
    console: '{"timestamp":"%d{yyyy-MM-dd HH:mm:ss.SSS}",' +
             '"level":"%5p",' +
             '"traceId":"%X{traceId:-}",' +
             '"spanId":"%X{spanId:-}",' +
             '"logger":"%logger{36}",' +
             '"message":"%msg"}%n'
```

#### Audit Trail
```java
@Aspect
@Component
public class GraphAuditAspect {
    
    @Around("@annotation(AuditGraphOperation)")
    public Object auditOperation(ProceedingJoinPoint joinPoint, 
                                  AuditGraphOperation annotation) throws Throwable {
        long startTime = System.currentTimeMillis();
        String operation = annotation.value();
        
        try {
            Object result = joinPoint.proceed();
            long duration = System.currentTimeMillis() - startTime;
            auditLog.success(operation, duration);
            return result;
        } catch (Throwable ex) {
            auditLog.failure(operation, ex);
            throw ex;
        }
    }
}
```

---

## Error Handling Strategy

### Error Type Mapping

| Exception Type | GraphQL Error Type | HTTP Status | Use Case |
|---------------|-------------------|-------------|----------|
| `GraphNotFoundException` | `NOT_FOUND` | 404 | Graph doesn't exist |
| `GraphBuildException` | `BAD_REQUEST` | 400 | Invalid graph configuration |
| `GraphExecutionException` | `INTERNAL_ERROR` | 500 | Runtime execution failure |
| `GraphStateException` | `BAD_REQUEST` | 400 | Invalid state manipulation |
| `ConstraintViolationException` | `BAD_REQUEST` | 400 | Validation errors |
| `StatusRuntimeException (UNAVAILABLE)` | `INTERNAL_ERROR` | 503 | Backend service down |

### Error Response Format

```json
{
  "errors": [
    {
      "message": "Graph not found: invalid-graph-id",
      "locations": [{ "line": 2, "column": 3 }],
      "path": ["buildGraph"],
      "extensions": {
        "code": "GRAPH_NOT_FOUND",
        "classification": "DataFetchingException"
      }
    }
  ],
  "data": {
    "buildGraph": null
  }
}
```

### Best Practices

1. **Never expose stack traces** to clients
2. **Use error codes** for programmatic handling
3. **Log full details** server-side for debugging
4. **Provide actionable messages** for end users
5. **Implement consistent error shapes** across all operations

---

## Testing Strategy

### Unit Tests

```java
@SpringBootTest
class LangGraphControllerTest {
    
    @Autowired
    private GraphQlTester graphQlTester;
    
    @MockBean
    private LangGraphGrpcService langGraphService;
    
    @Test
    void shouldBuildGraphSuccessfully() {
        BuildGraphInput input = new BuildGraphInput(
            "test-graph", "Test Graph", 
            List.of(new NodeInput("n1", "START", "handler")),
            List.of(), null
        );
        
        when(langGraphService.buildGraph(any()))
            .thenReturn(Mono.just(new BuildGraphPayload(true, "test-graph", "Success")));
        
        graphQlTester.document("""
            mutation BuildGraph($input: BuildGraphInput!) {
                buildGraph(input: $input) {
                    success
                    graphId
                    message
                }
            }
            """)
            .variable("input", input)
            .execute()
            .path("buildGraph.success").entity(Boolean.class).isEqualTo(true)
            .path("buildGraph.graphId").isEqualTo("test-graph");
    }
}
```

### Integration Tests

```java
@Testcontainers
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class GraphIntegrationTest {
    
    @Container
    static RedisContainer redis = new RedisContainer("redis:7-alpine");
    
    @DynamicPropertySource
    static void redisProps(DynamicPropertyRegistry registry) {
        registry.add("spring.data.redis.host", redis::getHost);
        registry.add("spring.data.redis.port", redis::getFirstMappedPort);
    }
    
    @Test
    void shouldPersistAndGetGraphView() {
        // Build graph
        var buildResponse = graphQlTester.document("""
            mutation { buildGraph(input: { ... }) { success } }
            """)
            .execute()
            .path("buildGraph.success").entity(Boolean.class).isEqualTo(true);
        
        // Retrieve graph view
        graphQlTester.document("""
            query { getGraphView(graphId: "test") { success nodes { nodeId } } }
            """)
            .execute()
            .path("getGraphView.success").entity(Boolean.class).isEqualTo(true);
    }
}
```

### Load Testing

```yaml
# k6 load test script
import http from 'k6/http';
import ws from 'k6/ws';

export const options = {
  stages: [
    { duration: '30s', target: 100 },
    { duration: '1m', target: 100 },
    { duration: '30s', target: 0 },
  ],
};

export default function () {
  const query = JSON.stringify({
    query: `
      query ListGraphs {
        listGraphs(pageSize: 10) {
          graphs { graphId graphName }
        }
      }
    `
  });
  
  const res = http.post('http://localhost:9191/graphql', query, {
    headers: { 'Content-Type': 'application/json' },
  });
  
  check(res, {
    'status is 200': (r) => r.status === 200,
    'response time < 200ms': (r) => r.timings.duration < 200,
  });
}
```

---

## Deployment Guide

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: graphql-gateway
spec:
  replicas: 3
  selector:
    matchLabels:
      app: graphql-gateway
  template:
    metadata:
      labels:
        app: graphql-gateway
    spec:
      containers:
      - name: gateway
        image: your-registry/spring-langgraph-gateway:1.0.0
        ports:
        - containerPort: 9191
        env:
        - name: SPRING_DATA_REDIS_HOST
          valueFrom:
            configMapKeyRef:
              name: app-config
              key: redis-host
        - name: SPRING_GRPC_CLIENT_CHANNELS_LANGGRAPH-SERVICE_ADDRESS
          value: "langgraph-grpc:50051"
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /actuator/health/liveness
            port: 9191
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /actuator/health/readiness
            port: 9191
          initialDelaySeconds: 10
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: graphql-gateway-service
spec:
  selector:
    app: graphql-gateway
  ports:
  - port: 80
    targetPort: 9191
  type: ClusterIP
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: graphql-ingress
  annotations:
    nginx.ingress.kubernetes.io/websocket-services: "graphql-gateway-service"
spec:
  rules:
  - host: api.example.com
    http:
      paths:
      - path: /graphql
        pathType: Prefix
        backend:
          service:
            name: graphql-gateway-service
            port:
              number: 80
```

### Helm Chart Values

```yaml
# values.yaml
replicaCount: 3

image:
  repository: your-registry/spring-langgraph-gateway
  tag: 1.0.0
  pullPolicy: IfNotPresent

resources:
  limits:
    cpu: 500m
    memory: 1Gi
  requests:
    cpu: 250m
    memory: 512Mi

config:
  redis:
    host: redis-master
    port: 6379
  grpc:
    address: langgraph-grpc:50051
    negotiationType: PLAINTEXT

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: api.example.com
      paths:
        - path: /graphql
          pathType: Prefix

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 80
```

### CI/CD Pipeline (GitHub Actions)

```yaml
name: Build and Deploy

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v4
    
    - name: Set up JDK 21
      uses: actions/setup-java@v4
      with:
        java-version: '21'
        distribution: 'temurin'
    
    - name: Build with Gradle
      run: ./gradlew build
    
    - name: Run tests
      run: ./gradlew test
    
    - name: Build Docker image
      run: docker build -t ${{ secrets.REGISTRY }}/gateway:${{ github.sha }} .
    
    - name: Push to registry
      run: |
        docker push ${{ secrets.REGISTRY }}/gateway:${{ github.sha }}
        docker tag ${{ secrets.REGISTRY }}/gateway:${{ github.sha }} ${{ secrets.REGISTRY }}/gateway:latest
        docker push ${{ secrets.REGISTRY }}/gateway:latest
    
    - name: Deploy to Kubernetes
      run: |
        helm upgrade --install graphql-gateway ./helm \
          --set image.tag=${{ github.sha }} \
          --namespace production
```

---

## Monitoring & Observability

### Metrics Collection

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health,info,metrics,prometheus
  metrics:
    tags:
      application: ${spring.application.name}
    distribution:
      percentiles-histogram:
        http.server.requests: true
        grpc.client.calls: true
```

### Key Metrics to Monitor

```java
@Component
public class GraphMetrics {
    
    private final MeterRegistry meterRegistry;
    private final Counter buildGraphCounter;
    private final Timer executeGraphTimer;
    private final Gauge activeSubscriptionsGauge;
    
    public GraphMetrics(MeterRegistry meterRegistry) {
        this.meterRegistry = meterRegistry;
        
        this.buildGraphCounter = Counter.builder("graph.build.total")
            .description("Total number of graph build operations")
            .register(meterRegistry);
        
        this.executeGraphTimer = Timer.builder("graph.execution.time")
            .description("Graph execution duration")
            .register(meterRegistry);
        
        this.activeSubscriptionsGauge = Gauge.builder("graph.subscriptions.active", 
                new AtomicInteger(0), AtomicInteger::get)
            .description("Active WebSocket subscriptions")
            .register(meterRegistry);
    }
}
```

### Distributed Tracing

```yaml
management:
  tracing:
    sampling:
      probability: 1.0
  zipkin:
    tracing:
      endpoint: http://zipkin:9411/api/v2/spans
```

### Grafana Dashboard Example

```json
{
  "dashboard": {
    "title": "GraphQL Gateway Metrics",
    "panels": [
      {
        "title": "Request Rate",
        "targets": [{
          "expr": "rate(http_server_requests_total[5m])"
        }]
      },
      {
        "title": "P95 Latency",
        "targets": [{
          "expr": "histogram_quantile(0.95, rate(http_server_requests_bucket[5m]))"
        }]
      },
      {
        "title": "Error Rate",
        "targets": [{
          "expr": "rate(http_server_requests_total{status=~\"5..\"}[5m])"
        }]
      },
      {
        "title": "Active Subscriptions",
        "targets": [{
          "expr": "graph_subscriptions_active"
        }]
      }
    ]
  }
}
```

### Alerting Rules (Prometheus)

```yaml
groups:
- name: graphql-gateway-alerts
  rules:
  - alert: HighErrorRate
    expr: rate(http_server_requests_total{status=~"5.."}[5m]) > 0.05
    for: 5m
    annotations:
      summary: "High error rate detected"
  
  - alert: HighLatency
    expr: histogram_quantile(0.95, rate(http_server_requests_bucket[5m])) > 1
    for: 10m
    annotations:
      summary: "P95 latency above 1 second"
  
  - alert: GRPCConnectionFailures
    expr: rate(grpc_client_calls_total{status_code!="OK"}[5m]) > 0.1
    for: 5m
    annotations:
      summary: "gRPC connection failures detected"
```

---

## Security Considerations

### 1. Authentication & Authorization

```java
@Configuration
@EnableMethodSecurity
public class SecurityConfig {
    
    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        return http
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/actuator/health").permitAll()
                .requestMatchers("/graphql", "/graphql/ws").hasRole("API_USER")
                .anyRequest().authenticated()
            )
            .oauth2ResourceServer(oauth2 -> oauth2
                .jwt(jwt -> jwt
                    .decoder(jwtDecoder())
                )
            )
            .build();
    }
    
    @Bean
    public JwtDecoder jwtDecoder() {
        return NimbusJwtDecoder.withJwkSetUri(
            "https://auth.example.com/.well-known/jwks.json"
        ).build();
    }
}
```

### 2. Query Complexity Analysis

```java
@Component
public class QueryComplexityInstrumentation implements Instrumentation {
    
    private static final int MAX_COMPLEXITY = 100;
    
    @Override
    public CompletableFuture<ExecutionResult> beginExecution(
            InstrumentationContext<ExecutionResult> context,
            InstrumentationState state) {
        
        int complexity = calculateComplexity(context.getExecutionInput());
        
        if (complexity > MAX_COMPLEXITY) {
            return CompletableFuture.failedFuture(
                new RuntimeException("Query too complex: " + complexity)
            );
        }
        
        return super.beginExecution(context, state);
    }
}
```

### 3. Rate Limiting

```java
@Configuration
public class RateLimitConfig {
    
    @Bean
    public RateLimiterRegistry rateLimiterRegistry() {
        return RateLimiterRegistry.of(
            RateLimiterConfig.custom()
                .limitRefreshPeriod(Duration.ofSeconds(1))
                .limitForPeriod(100)
                .timeoutDuration(Duration.ofMillis(500))
                .build()
        );
    }
}

@Aspect
@Component
public class RateLimitAspect {
    
    private final RateLimiterRegistry registry;
    
    public RateLimitAspect(RateLimiterRegistry registry) {
        this.registry = registry;
    }
    
    @Around("@annotation(RateLimited)")
    public Object applyRateLimit(ProceedingJoinPoint joinPoint) throws Throwable {
        RateLimiter limiter = registry.rateLimiter("graphql-requests");
        
        if (!limiter.acquirePermission()) {
            throw new TooManyRequestsException("Rate limit exceeded");
        }
        
        return joinPoint.proceed();
    }
}
```

### 4. Input Sanitization

```java
@Component
public class InputSanitizer {
    
    public String sanitize(String input) {
        if (input == null) return null;
        
        // Remove potential XSS vectors
        return input.replaceAll("[<>\"'&]", "");
    }
    
    public Map<String, Object> sanitizeMetadata(Map<String, Object> metadata) {
        if (metadata == null) return null;
        
        return metadata.entrySet().stream()
            .collect(Collectors.toMap(
                Map.Entry::getKey,
                e -> e.getValue() instanceof String s ? sanitize(s) : e.getValue()
            ));
    }
}
```

---

## Troubleshooting

### Common Issues

#### 1. gRPC Connection Failures

**Symptoms**: `StatusRuntimeException: UNAVAILABLE`

**Solutions**:
```yaml
# Check gRPC configuration
spring:
  grpc:
    client:
      channels:
        langgraph-service:
          address: correct-host:50051
          negotiation-type: PLAINTEXT  # or TLS
          enable-keep-alive: true
          keep-alive-time: 30s
```

```bash
# Test connectivity
grpcurl -plaintext localhost:50051 list
```

#### 2. WebSocket Connection Issues

**Symptoms**: Subscriptions fail to connect

**Solutions**:
```yaml
# Ensure WebSocket is enabled
spring:
  graphql:
    websocket:
      path: /graphql/ws
```

```java
// Configure CORS for WebSocket
@Configuration
public class CorsConfig implements WebMvcConfigurer {
    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/**")
            .allowedOrigins("https://your-frontend.com")
            .allowedMethods("*")
            .allowCredentials(true);
    }
}
```

#### 3. Redis Connection Timeouts

**Symptoms**: `RedisConnectionException: Unable to connect`

**Solutions**:
```yaml
spring:
  data:
    redis:
      host: redis-host
      port: 6379
      timeout: 2000ms
      lettuce:
        pool:
          max-active: 8
          max-idle: 8
          min-idle: 0
```

#### 4. Memory Leaks in Streams

**Symptoms**: Increasing memory usage over time

**Solutions**:
```java
public Flux<GraphExecutionEventPayload> executeGraphStream(...) {
    return Flux.<ExecuteGraphResponse>create(emitter -> { ... })
        .map(mapper::toGraphExecutionEventPayload)
        .subscribeOn(Schedulers.boundedElastic())
        .onBackpressureBuffer(1000)
        .timeout(Duration.ofMinutes(30))
        .doOnCancel(() -> log.info("Subscription cancelled"))
        .doFinally(signalType -> log.info("Stream finished: {}", signalType));
}
```

#### 5. Slow Queries

**Symptoms**: High P95 latency

**Solutions**:
- Enable query complexity analysis
- Implement DataLoader for batching
- Add caching layer
- Optimize database queries
- Scale horizontally

### Debug Mode Configuration

```yaml
logging:
  level:
    root: DEBUG
    org.sandbox.langgraph: TRACE
    org.springframework.graphql: DEBUG
    org.springframework.grpc: DEBUG
    io.grpc: DEBUG
  pattern:
    console: "%d{HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n"
```

### Health Check Endpoints

```bash
# Liveness probe
curl http://localhost:9191/actuator/health/liveness

# Readiness probe
curl http://localhost:9191/actuator/health/readiness

# Detailed health
curl http://localhost:9191/actuator/health
```

---

## Conclusion

This comprehensive guide has covered all aspects of building a production-ready Java GraphQL API for graph management. Key takeaways:

1. **Architecture**: Use a layered approach with clear separation between GraphQL, service, and data layers
2. **Reactive Stack**: Leverage Spring WebFlux for non-blocking I/O and better scalability
3. **Error Handling**: Implement centralized exception handling with proper error type mapping
4. **Security**: Always validate inputs, implement authentication, and use rate limiting
5. **Observability**: Instrument your application with metrics, tracing, and structured logging
6. **Resilience**: Use circuit breakers, retries, and timeouts for external service calls
7. **Testing**: Implement comprehensive unit, integration, and load tests
8. **Deployment**: Use containerization and orchestration for scalable deployments

By following these best practices and patterns demonstrated in the `spring-langgraph-app` reference implementation, you can build robust, scalable, and maintainable GraphQL APIs for production environments.

---

## Appendix A: Complete Project Structure

```
spring-langgraph-app/
├── src/main/java/org/sandbox/langgraph/
│   ├── LangGraphApplication.java
│   ├── config/
│   │   ├── CorsConfig.java
│   │   ├── GraphQlConfig.java
│   │   ├── GrpcConfig.java
│   │   └── MapperConfig.java
│   ├── controller/
│   │   └── LangGraphController.java
│   ├── dto/
│   │   └── graphql/
│   │       ├── input/
│   │       │   ├── BuildGraphInput.java
│   │       │   ├── EdgeInput.java
│   │       │   ├── ExecuteGraphInput.java
│   │       │   ├── NodeInput.java
│   │       │   └── UpdateGraphStateInput.java
│   │       └── payload/
│   │           ├── BuildGraphPayload.java
│   │           ├── DeleteGraphPayload.java
│   │           ├── ExecuteGraphPayload.java
│   │           ├── GraphExecutionEventPayload.java
│   │           ├── GraphListPayload.java
│   │           ├── GraphStatePayload.java
│   │           ├── GraphSummary.java
│   │           ├── UpdateGraphStatePayload.java
│   │           ├── meta/
│   │           │   └── PageInfo.java
│   │           └── redis/
│   │               ├── GraphEdge.java
│   │               ├── GraphNode.java
│   │               ├── GraphViewData.java
│   │               ├── GraphViewPayload.java
│   │               └── Position.java
│   ├── exception/
│   │   ├── GraphQlExceptionHandler.java
│   │   └── LangGraphException.java
│   ├── mapper/
│   │   ├── GraphGrpcMapper.java
│   │   └── RedisGraphStorageMapper.java
│   ├── model/
│   │   └── GraphEvent.java
│   └── service/
│       ├── LangGraphGrpcService.java
│       └── RedisGraphViewService.java
├── src/main/proto/
│   └── langgraph.proto
├── src/main/resources/
│   ├── application.yml
│   └── graphql/
│       └── schema.graphqls
├── build.gradle
├── settings.gradle
└── Dockerfile
```

## Appendix B: Quick Reference Commands

```bash
# Build
./gradlew clean build

# Run locally
./gradlew bootRun

# Run tests
./gradlew test

# Generate protobuf
./gradlew generateProto

# Docker build
docker build -t gateway:latest .

# Docker run
docker run -p 9191:9191 gateway:latest

# Test GraphQL endpoint
curl -X POST http://localhost:9191/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ __typename }"}'

# Check health
curl http://localhost:9191/actuator/health

# View metrics
curl http://localhost:9191/actuator/metrics
```

## Appendix C: Useful Resources

- [Spring GraphQL Documentation](https://docs.spring.io/spring-graphql/reference/)
- [Spring gRPC Documentation](https://github.com/spring-projects-experimental/spring-grpc)
- [Project Reactor Documentation](https://projectreactor.io/docs/core/release/reference/)
- [GraphQL Specification](https://spec.graphql.org/)
- [gRPC Java Documentation](https://grpc.io/docs/languages/java/)
- [MapStruct Documentation](https://mapstruct.org/documentation/)
