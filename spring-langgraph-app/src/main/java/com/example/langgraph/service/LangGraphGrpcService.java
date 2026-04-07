package com.example.langgraph.service;

import com.example.langgraph.config.LangGraphServiceProperties;
import com.example.langgraph.dto.BuildGraphRequest;
import com.example.langgraph.dto.ExecuteGraphRequest;
import com.example.langgraph.grpc.*;
import com.google.protobuf.Empty;
import io.grpc.StatusRuntimeException;
import io.grpc.stub.StreamObserver;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;

/**
 * Service for interacting with the LangGraph gRPC service.
 */
@Service
public class LangGraphGrpcService {

    private static final Logger log = LoggerFactory.getLogger(LangGraphGrpcService.class);

    private final LangGraphServiceGrpc.LangGraphServiceFutureStub futureStub;
    private final LangGraphServiceGrpc.LangGraphServiceStub asyncStub;
    private final LangGraphServiceProperties properties;

    public LangGraphGrpcService(
            LangGraphServiceGrpc.LangGraphServiceFutureStub futureStub,
            LangGraphServiceGrpc.LangGraphServiceStub asyncStub,
            LangGraphServiceProperties properties) {
        this.futureStub = futureStub;
        this.asyncStub = asyncStub;
        this.properties = properties;
    }

    /**
     * Build a new graph with the given configuration.
     */
    public Mono<BuildGraphResponse> buildGraph(BuildGraphRequest request) {
        return Mono.fromFuture(() -> {
            log.info("Building graph: {}", request.getGraphId());
            
            // Convert DTO to gRPC request
            com.example.langgraph.grpc.BuildGraphRequest.Builder grpcRequestBuilder = 
                com.example.langgraph.grpc.BuildGraphRequest.newBuilder()
                    .setGraphId(request.getGraphId())
                    .setGraphName(request.getGraphName());
            
            // Add nodes
            if (request.getNodes() != null) {
                for (BuildGraphRequest.NodeDefinition node : request.getNodes()) {
                    com.example.langgraph.grpc.NodeDefinition.Builder nodeBuilder = 
                        com.example.langgraph.grpc.NodeDefinition.newBuilder()
                            .setNodeId(node.getNodeId())
                            .setNodeType(node.getNodeType())
                            .setHandlerName(node.getHandlerName());
                    
                    if (node.getMetadata() != null) {
                        nodeBuilder.putAllMetadata(node.getMetadata());
                    }
                    
                    grpcRequestBuilder.addNodes(nodeBuilder.build());
                }
            }
            
            // Add edges
            if (request.getEdges() != null) {
                for (BuildGraphRequest.EdgeDefinition edge : request.getEdges()) {
                    com.example.langgraph.grpc.EdgeDefinition.Builder edgeBuilder = 
                        com.example.langgraph.grpc.EdgeDefinition.newBuilder()
                            .setSource(edge.getSource())
                            .setTarget(edge.getTarget());
                    
                    if (edge.getCondition() != null) {
                        edgeBuilder.setCondition(edge.getCondition());
                    }
                    
                    grpcRequestBuilder.addEdges(edgeBuilder.build());
                }
            }
            
            // Add config
            if (request.getConfig() != null) {
                grpcRequestBuilder.putAllConfig(request.getConfig());
            }
            
            CompletableFuture<com.example.langgraph.grpc.BuildGraphResponse> future = 
                futureStub.buildGraph(grpcRequestBuilder.build());
            
            return future.thenApply(response -> {
                log.info("Graph built successfully: {}", response.getGraphId());
                return response;
            });
        }).subscribeOn(Schedulers.boundedElastic());
    }

    /**
     * Execute a graph and return a Flux of streaming responses.
     */
    public Flux<ExecuteGraphResponse> executeGraphStream(ExecuteGraphRequest request) {
        return Flux.create(emitter -> {
            log.info("Executing graph: {} with input: {}", request.getGraphId(), request.getInput());
            
            // Convert DTO to gRPC request
            com.example.langgraph.grpc.ExecuteGraphRequest.Builder grpcRequestBuilder = 
                com.example.langgraph.grpc.ExecuteGraphRequest.newBuilder()
                    .setGraphId(request.getGraphId())
                    .setInput(request.getInput())
                    .setStreamOutput(request.isStreamOutput());
            
            if (request.getContext() != null) {
                grpcRequestBuilder.putAllContext(request.getContext());
            }
            
            StreamObserver<com.example.langgraph.grpc.ExecuteGraphResponse> responseObserver = 
                new StreamObserver<com.example.langgraph.grpc.ExecuteGraphResponse>() {
                    @Override
                    public void onNext(com.example.langgraph.grpc.ExecuteGraphResponse value) {
                        log.debug("Received event: {} for node: {}", value.getEventType(), value.getNodeId());
                        emitter.next(value);
                    }

                    @Override
                    public void onError(Throwable t) {
                        log.error("Error executing graph", t);
                        emitter.error(t);
                    }

                    @Override
                    public void onCompleted() {
                        log.info("Graph execution completed");
                        emitter.complete();
                    }
                };
            
            asyncStub.executeGraph(grpcRequestBuilder.build(), responseObserver);
        }).subscribeOn(Schedulers.boundedElastic());
    }

    /**
     * Get the current state of a graph.
     */
    public Mono<GetGraphStateResponse> getGraphState(String graphId, String threadId) {
        return Mono.fromFuture(() -> {
            log.info("Getting graph state for: {}", graphId);
            
            com.example.langgraph.grpc.GetGraphStateRequest.Builder requestBuilder = 
                com.example.langgraph.grpc.GetGraphStateRequest.newBuilder()
                    .setGraphId(graphId);
            
            if (threadId != null) {
                requestBuilder.setThreadId(threadId);
            }
            
            CompletableFuture<com.example.langgraph.grpc.GetGraphStateResponse> future = 
                futureStub.getGraphState(requestBuilder.build());
            
            return future.thenApply(response -> {
                log.info("Retrieved graph state for: {}", graphId);
                return response;
            });
        }).subscribeOn(Schedulers.boundedElastic());
    }

    /**
     * Update the state of a graph.
     */
    public Mono<UpdateGraphStateResponse> updateGraphState(
            String graphId, String threadId, Map<String, String> stateUpdates) {
        return Mono.fromFuture(() -> {
            log.info("Updating graph state for: {}", graphId);
            
            com.example.langgraph.grpc.UpdateGraphStateRequest.Builder requestBuilder = 
                com.example.langgraph.grpc.UpdateGraphStateRequest.newBuilder()
                    .setGraphId(graphId);
            
            if (threadId != null) {
                requestBuilder.setThreadId(threadId);
            }
            
            if (stateUpdates != null) {
                requestBuilder.putAllStateUpdates(stateUpdates);
            }
            
            CompletableFuture<com.example.langgraph.grpc.UpdateGraphStateResponse> future = 
                futureStub.updateGraphState(requestBuilder.build());
            
            return future.thenApply(response -> {
                log.info("Updated graph state for: {}", graphId);
                return response;
            });
        }).subscribeOn(Schedulers.boundedElastic());
    }

    /**
     * List all available graphs.
     */
    public Mono<ListGraphsResponse> listGraphs(int pageSize, String pageToken) {
        return Mono.fromFuture(() -> {
            log.info("Listing graphs with page size: {}", pageSize);
            
            com.example.langgraph.grpc.ListGraphsRequest.Builder requestBuilder = 
                com.example.langgraph.grpc.ListGraphsRequest.newBuilder()
                    .setPageSize(pageSize);
            
            if (pageToken != null) {
                requestBuilder.setPageToken(pageToken);
            }
            
            CompletableFuture<com.example.langgraph.grpc.ListGraphsResponse> future = 
                futureStub.listGraphs(requestBuilder.build());
            
            return future.thenApply(response -> {
                log.info("Listed {} graphs", response.getGraphsCount());
                return response;
            });
        }).subscribeOn(Schedulers.boundedElastic());
    }

    /**
     * Delete a graph.
     */
    public Mono<DeleteGraphResponse> deleteGraph(String graphId) {
        return Mono.fromFuture(() -> {
            log.info("Deleting graph: {}", graphId);
            
            com.example.langgraph.grpc.DeleteGraphRequest request = 
                com.example.langgraph.grpc.DeleteGraphRequest.newBuilder()
                    .setGraphId(graphId)
                    .build();
            
            CompletableFuture<com.example.langgraph.grpc.DeleteGraphResponse> future = 
                futureStub.deleteGraph(request);
            
            return future.thenApply(response -> {
                log.info("Deleted graph: {}", graphId);
                return response;
            });
        }).subscribeOn(Schedulers.boundedElastic());
    }
}
