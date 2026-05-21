package org.sandbox.langgraph.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import io.grpc.stub.StreamObserver;
import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.dto.graphql.input.BuildGraphInput;
import org.sandbox.langgraph.dto.graphql.input.ExecuteGraphInput;
import org.sandbox.langgraph.dto.graphql.input.UpdateGraphStateInput;
import org.sandbox.langgraph.dto.graphql.payload.*;
import org.sandbox.langgraph.dto.graphql.payload.StateSnapshot;
import org.sandbox.langgraph.exception.LangGraphException;
import org.sandbox.langgraph.grpc.*;
import org.sandbox.langgraph.mapper.GraphGrpcMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.Collections;
import java.util.List;
import java.util.Map;

@Service
public class LangGraphGrpcService {

    private static final Logger log = LoggerFactory.getLogger(LangGraphGrpcService.class);

    private final LangGraphServiceGrpc.LangGraphServiceFutureStub futureStub;
    private final LangGraphServiceGrpc.LangGraphServiceStub asyncStub;
    private final GraphGrpcMapper mapper;
    private final ObjectMapper objectMapper;

    public LangGraphGrpcService(
            LangGraphServiceGrpc.LangGraphServiceFutureStub futureStub,
            LangGraphServiceGrpc.LangGraphServiceStub asyncStub,
            GraphGrpcMapper mapper,
            ObjectMapper objectMapper) {
        this.futureStub = futureStub;
        this.asyncStub = asyncStub;
        this.mapper = mapper;
        this.objectMapper = objectMapper;
    }

    public Mono<@NonNull BuildGraphPayload> buildGraph(BuildGraphInput input) {
        return Mono.fromCallable(() -> {
                    log.debug("Building graph: {}", input.graphId());
                    BuildGraphRequest request = mapper.toBuildGraphRequest(input);
                    return futureStub.buildGraph(request).get();
                })
                .map(mapper::toBuildGraphPayload)
                .doOnSuccess(response -> log.info("Graph built successfully: {}", input.graphId()))
                .doOnError(this::logAndWrapGrpcError)
                .subscribeOn(Schedulers.boundedElastic());
    }

    public Flux<@NonNull GraphExecutionEventPayload> executeGraphStream(ExecuteGraphInput input, boolean stream) {
        ExecuteGraphRequest request = stream
                ? mapper.toStreamExecuteGraphRequest(input)
                : mapper.toExecuteGraphRequest(input);

        // Explicitly use the gRPC generated ExecuteGraphResponse inside the Flux
        return Flux.<ExecuteGraphResponse>create(emitter -> {
                    log.debug("Executing graph: {} (stream={})", input.graphId(), stream);

                    asyncStub.executeGraph(request, new StreamObserver<>() {
                        @Override
                        public void onNext(ExecuteGraphResponse value) {
                            log.trace("Received event: {} for node: {}", value.getEventType(), value.getNodeId());
                            emitter.next(value);
                        }

                        @Override
                        public void onError(Throwable t) {
                            log.error("gRPC error executing graph: {}", input.graphId(), t);
                            emitter.error(t);
                        }

                        @Override
                        public void onCompleted() {
                            log.debug("Graph execution completed: {}", input.graphId());
                            emitter.complete();
                        }
                    });
                })
                .map(mapper::toGraphExecutionEventPayload) // Map gRPC response to GraphQL Payload
                .subscribeOn(Schedulers.boundedElastic());
    }

    public Mono<@NonNull GraphStatePayload> getGraphState(String graphId, String threadId) {
        return Mono.fromCallable(() -> {
                    log.debug("Getting graph state for: {}", graphId);
                    GetGraphStateRequest request = mapper.toGetGraphStateRequest(graphId, threadId);
                    return futureStub.getGraphState(request).get();
                })
                .map(mapper::toGraphStatePayload)
                .doOnSuccess(response -> log.debug("Retrieved graph state for: {}", graphId))
                .doOnError(this::logAndWrapGrpcError)
                .subscribeOn(Schedulers.boundedElastic());
    }

    public Mono<@NonNull UpdateGraphStatePayload> updateGraphState(UpdateGraphStateInput input) {
        return Mono.fromCallable(() -> {
                    log.debug("Updating graph state for: {}", input.graphId());
                    UpdateGraphStateRequest request = mapper.toUpdateGraphStateRequest(input);
                    return futureStub.updateGraphState(request).get();
                })
                .map(mapper::toUpdateGraphStatePayload)
                .doOnSuccess(response -> log.debug("Updated graph state for: {}", input.graphId()))
                .doOnError(this::logAndWrapGrpcError)
                .subscribeOn(Schedulers.boundedElastic());
    }

    public Mono<@NonNull GraphListPayload> listGraphs(int pageSize, String pageToken) {
        return Mono.fromCallable(() -> {
                    log.debug("Listing graphs with page size: {}", pageSize);
                    ListGraphsRequest request = mapper.toListGraphsRequest(pageSize, pageToken);
                    return futureStub.listGraphs(request).get();
                })
                .map(mapper::toGraphListPayload)
                .doOnSuccess(response -> {
                    assert response != null;
                    log.debug("Listed {} graphs", response.totalCount());
                })
                .doOnError(this::logAndWrapGrpcError)
                .subscribeOn(Schedulers.boundedElastic());
    }

    public Mono<@NonNull DeleteGraphPayload> deleteGraph(String graphId) {
        return Mono.fromCallable(() -> {
                    log.debug("Deleting graph: {}", graphId);
                    DeleteGraphRequest request = mapper.toDeleteGraphRequest(graphId);
                    return futureStub.deleteGraph(request).get();
                })
                .map(mapper::toDeleteGraphPayload)
                .doOnSuccess(response -> log.info("Deleted graph: {}", graphId))
                .doOnError(this::logAndWrapGrpcError)
                .subscribeOn(Schedulers.boundedElastic());
    }

    public Mono<@NonNull GraphHistoryPayload> getExecutionHistory(String graphId, String threadId) {
        return Mono.fromCallable(() -> {
                    log.debug("Getting graph {} execution history by thread {}", graphId, threadId);
                    GraphHistoryRequest request = mapper.toGraphHistoryRequest(graphId, threadId);
                    return futureStub.getExecutionHistory(request).get();
                })
                .map(this::apply)
                .doOnError(this::logAndWrapGrpcError)
                .subscribeOn(Schedulers.boundedElastic());
    }

    private void logAndWrapGrpcError(Throwable error) {
        if (error instanceof StatusRuntimeException sre) {
            Status status = sre.getStatus();
            log.error("gRPC error - Code: {}, Message: {}", status.getCode(), status.getDescription());

            throw switch (status.getCode()) {
                case NOT_FOUND -> new LangGraphException.GraphNotFoundException(
                        status.getDescription() != null ? status.getDescription().split(":")[0] : "unknown");
                case INVALID_ARGUMENT -> new LangGraphException.GraphBuildException(status.getDescription());
                case INTERNAL, UNAVAILABLE, DEADLINE_EXCEEDED ->
                        new LangGraphException.GraphExecutionException("Service unavailable: " + status.getDescription(), sre);
                default -> new LangGraphException.GraphExecutionException(status.getDescription(), sre);
            };
        }
        log.error("Unexpected error", error);
        throw new LangGraphException.GraphExecutionException("Unexpected error: " + error.getMessage(), error);
    }

    private GraphHistoryPayload apply(GraphHistoryResponse response) {
        try {
            // Map gRPC StateSnapshot list to GraphQL StateSnapshot list
            List<StateSnapshot> historyList = response.getHistoryList().stream().map(snap -> {
                try {
                    // Parse the JSON string from gRPC into a Map for the GraphQL JSON scalar
                    Map<String, Object> stateMap = objectMapper.readValue(snap.getStateJson(), new TypeReference<>() {
                    });
                    log.info("State map parsed: {}", stateMap);
                    return new StateSnapshot(snap.getNodeId(), stateMap, snap.getTimestamp());
                } catch (Exception e) {
                    log.warn("Failed to parse state JSON for node {}: {}", snap.getNodeId(), e.getMessage());
                    // Return a snapshot with empty state on parse error
                    return new StateSnapshot(snap.getNodeId(), Collections.emptyMap(), snap.getTimestamp());
                }
            }).toList();

            return new GraphHistoryPayload(true, historyList, null);
        } catch (Exception e) {
            log.error("Error processing history response", e);
            return GraphHistoryPayload.error("Failed to process history: " + e.getMessage());
        }
    }
}
