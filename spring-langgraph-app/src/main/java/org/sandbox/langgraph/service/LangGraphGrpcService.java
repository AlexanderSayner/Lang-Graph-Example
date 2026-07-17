package org.sandbox.langgraph.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import lombok.RequiredArgsConstructor;
import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.core.service.GraphPostgresService;
import org.sandbox.langgraph.dto.graphql.input.BuildGraphInput;
import org.sandbox.langgraph.dto.graphql.input.ExecuteGraphInput;
import org.sandbox.langgraph.dto.graphql.input.UpdateGraphStateInput;
import org.sandbox.langgraph.dto.graphql.payload.*;
import org.sandbox.langgraph.dto.graphql.payload.RewindGraphPayload;
import org.sandbox.langgraph.dto.graphql.payload.StateSnapshot;
import org.sandbox.langgraph.dto.graphql.payload.meta.PageInfo;
import org.sandbox.langgraph.dto.pagination.PageData;
import org.sandbox.langgraph.exception.LangGraphException;
import org.sandbox.langgraph.grpc.*;
import org.sandbox.langgraph.mapper.GraphGrpcMapper;
import org.sandbox.langgraph.util.JsonDiffUtil;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

@Service
@RequiredArgsConstructor
public class LangGraphGrpcService {

    private static final Logger log = LoggerFactory.getLogger(LangGraphGrpcService.class);
    private static final int TIMEOUT = 30;
    private static final TimeUnit TIMEOUT_UNIT = TimeUnit.SECONDS;

    private final LangGraphServiceGrpc.LangGraphServiceBlockingStub blockingStub;
    private final GraphGrpcMapper mapper;
    private final ObjectMapper objectMapper;
    private final GraphPostgresService graphPostgresService;

    public @NonNull BuildGraphPayload buildGraph(BuildGraphInput input) {
        graphPostgresService.saveGraph(input);
        log.debug("Building graph: {}", input.graphId());

        BuildGraphRequest request = mapper.toBuildGraphRequest(input);
        BuildGraphResponse response = blockingStub.withDeadlineAfter(TIMEOUT, TIMEOUT_UNIT).buildGraph(request);
        return mapper.toBuildGraphPayload(response);
    }

    public Flux<@NonNull GraphExecutionEventPayload> executeGraphStream(ExecuteGraphInput input, boolean stream) {
        ExecuteGraphRequest request = stream
                ? mapper.toStreamExecuteGraphRequest(input)
                : mapper.toExecuteGraphRequest(input);

        log.debug("Executing graph: {} (stream={})", input.graphId(), stream);

        return Flux.create(emitter -> {
            try {
                Iterator<ExecuteGraphResponse> iterator = blockingStub.executeGraph(request);
                while (iterator.hasNext()) {
                    if (emitter.isCancelled()) {
                        break;
                    }
                    ExecuteGraphResponse value = iterator.next();
                    log.trace("Received event: {} for node: {}", value.getEventType(), value.getNodeId());
                    emitter.next(mapper.toGraphExecutionEventPayload(value));
                }
                emitter.complete();
            } catch (StatusRuntimeException e) {
                log.error("gRPC error executing graph: {}", input.graphId(), e);
                emitter.error(wrapGrpcError(e));
            } catch (Throwable t) {
                log.error("Unexpected error executing graph: {}", input.graphId(), t);
                emitter.error(new LangGraphException.GraphExecutionException("Unexpected error: " + t.getMessage(), t));
            }
        });
    }

    public @NonNull GraphStatePayload getGraphState(String graphId, String threadId) {
        log.debug("Getting graph state for: {}", graphId);
        GetGraphStateRequest request = mapper.toGetGraphStateRequest(graphId, threadId);
        GetGraphStateResponse response = blockingStub.withDeadlineAfter(TIMEOUT, TIMEOUT_UNIT).getGraphState(request);
        log.debug("Retrieved graph state for: {}", graphId);
        return mapper.toGraphStatePayload(response);
    }

    public @NonNull UpdateGraphStatePayload updateGraphState(UpdateGraphStateInput input) {
        log.debug("Updating graph state for: {}", input.graphId());
        UpdateGraphStateRequest request = mapper.toUpdateGraphStateRequest(input);
        UpdateGraphStateResponse response = blockingStub.withDeadlineAfter(TIMEOUT, TIMEOUT_UNIT).updateGraphState(request);
        log.debug("Updated graph state for: {}", input.graphId());
        return mapper.toUpdateGraphStatePayload(response);
    }

    public @NonNull GraphListPayload listGraphs(int pageSize, String pageToken) {
        log.debug("Listing graphs with page size: {}", pageSize);

        int parsedOffset = 0;
        if (pageToken != null && !pageToken.isEmpty()) {
            try {
                parsedOffset = Integer.parseInt(pageToken);
            } catch (NumberFormatException e) {
                log.warn("Failed to parse page token {}: {}", pageToken, e.getMessage());
            }
        }

        final int offset = parsedOffset;

        try {
            PageData pageData = graphPostgresService.listGraphs(pageSize, offset);
            List<GraphSummary> infos = pageData.entities().stream()
                    .map(entity -> {
                        int nodeCount = 0;
                        try {
                            JsonNode root = objectMapper.readTree(entity.getDefinition());
                            if (root.has("nodes") && root.get("nodes").isArray()) {
                                nodeCount = root.get("nodes").size();
                            }
                        } catch (Exception e) {
                            log.warn("Failed to parse node count for graph {}", entity.getGraphId(), e);
                        }

                        return mapper.toGraphSummary(GraphInfo.newBuilder()
                                .setGraphId(entity.getGraphId())
                                .setGraphName(entity.getName())
                                .setNodeCount(nodeCount)
                                .setCreatedAt(entity.getCreatedAt() != null ? entity.getCreatedAt().toString() : "")
                                .setStatus(entity.getStatus() != null ? entity.getStatus().name() : "UNKNOWN")
                                .build()
                        );
                    })
                    .toList();

            int nextOffset = offset + pageSize;
            boolean hasNext = nextOffset < pageData.totalCount();
            String nextToken = hasNext ? String.valueOf(nextOffset) : null;

            GraphListPayload response = new GraphListPayload(
                    infos,
                    new PageInfo(hasNext, nextToken),
                    pageData.totalCount()
            );

            log.debug("Listed {} graphs", response.graphs().size());
            return response;
        } catch (Exception e) {
            log.error("Database operation failed", e);
            throw new LangGraphException.GraphExecutionException("Database error: " + e.getMessage(), e);
        }
    }

    public @NonNull DeleteGraphPayload deleteGraph(String graphId) {
        try {
            boolean isDeleted = graphPostgresService.deleteGraph(graphId);
            DeleteGraphPayload response = mapper.toDeleteGraphPayload(DeleteGraphResponse.newBuilder()
                    .setSuccess(isDeleted)
                    .setMessage("Deletion status: %s".formatted(isDeleted ? "success" : "failure"))
                    .build());
            log.info("Deleted graph: {}", graphId);
            return response;
        } catch (Exception e) {
            log.error("Database operation failed", e);
            throw new LangGraphException.GraphExecutionException("Database error: " + e.getMessage(), e);
        }
    }

    public @NonNull GraphHistoryPayload getExecutionHistory(String graphId, String threadId) {
        log.debug("Getting graph {} execution history by thread {}", graphId, threadId);
        GraphHistoryRequest request = mapper.toGraphHistoryRequest(graphId, threadId);
        GraphHistoryResponse response = blockingStub.withDeadlineAfter(TIMEOUT, TIMEOUT_UNIT).getExecutionHistory(request);
        return apply(response);
    }

    public @NonNull RewindGraphPayload rewindGraph(String graphId, String threadId, String stateJson, String targetNodeId) {
        log.debug("Rewinding graph: {} to node: {}", graphId, targetNodeId);
        RewindGraphRequest request = RewindGraphRequest.newBuilder()
                .setGraphId(graphId)
                .setThreadId(threadId)
                .setTargetStateJson(stateJson)
                .setTargetNodeId(targetNodeId)
                .build();
        org.sandbox.langgraph.grpc.RewindGraphPayload response = blockingStub.withDeadlineAfter(TIMEOUT, TIMEOUT_UNIT).rewindGraph(request);
        return mapper.toRewindGraphPayload(response);
    }

    private @NonNull RuntimeException wrapGrpcError(StatusRuntimeException e) {
        Status status = e.getStatus();
        log.error("gRPC error - Code: {}, Message: {}", status.getCode(), status.getDescription());

        return switch (status.getCode()) {
            case NOT_FOUND -> new LangGraphException.GraphNotFoundException(
                    status.getDescription() != null ? status.getDescription().split(":")[0] : "unknown");
            case INVALID_ARGUMENT -> new LangGraphException.GraphBuildException(status.getDescription());
            case INTERNAL, UNAVAILABLE, DEADLINE_EXCEEDED ->
                    new LangGraphException.GraphExecutionException("Service unavailable: " + status.getDescription(), e);
            default -> new LangGraphException.GraphExecutionException(status.getDescription(), e);
        };
    }

    private @NonNull GraphHistoryPayload apply(GraphHistoryResponse response) {
        try {
            List<StateSnapshot> cooking = new ArrayList<>(response.getHistoryList().size());
            Map<String, Object> previousState = null;

            for (org.sandbox.langgraph.grpc.StateSnapshot snap : response.getHistoryList()) {
                Map<String, Object> currentState = new HashMap<>();
                try {
                    Map<String, Object> stateMap = objectMapper.readValue(
                            snap.getStateJson(), new TypeReference<>() {
                            });
                    log.info("State map parsed: {}", stateMap);
                    currentState = stateMap;
                } catch (Exception e) {
                    log.warn("Failed to parse state JSON for node {}: {}", snap.getNodeId(), e.getMessage());
                }

                JsonDiffUtil.StateDiff diff = JsonDiffUtil.calculateDiff(previousState, currentState);

                cooking.add(new StateSnapshot(
                        snap.getNodeId(),
                        currentState,
                        snap.getTimestamp(),
                        diff,
                        snap.getTokensUsed(),
                        snap.getTotalTokens()
                ));

                previousState = currentState;
            }

            return new GraphHistoryPayload(true, cooking, null);
        } catch (Exception e) {
            log.error("Error processing history response", e);
            return GraphHistoryPayload.error("Failed to process history: " + e.getMessage());
        }
    }
}
