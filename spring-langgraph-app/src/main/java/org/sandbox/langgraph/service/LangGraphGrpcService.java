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
import org.sandbox.langgraph.dto.pagination.GraphScopeFilter;
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

    /**
     * Page size used when a caller asks for nothing usable (notably {@code 0}), which every surface
     * documents as "default 10". Also the value the B2B contract's {@code page_size} falls back to.
     */
    public static final int DEFAULT_PAGE_SIZE = 10;

    /**
     * Upper bound of one page - the cap documented on the B2B contract's {@code page_size}. One
     * constant, two enforcement points (the facade clamps at the wire, this service clamps for the
     * GraphQL path and for anything else that calls in), so the two can never drift apart.
     */
    public static final int MAX_PAGE_SIZE = 100;

    private static final Logger log = LoggerFactory.getLogger(LangGraphGrpcService.class);
    private static final int TIMEOUT = 30;
    private static final TimeUnit TIMEOUT_UNIT = TimeUnit.SECONDS;

    private final LangGraphServiceGrpc.LangGraphServiceBlockingStub blockingStub;
    private final GraphGrpcMapper mapper;
    private final ObjectMapper objectMapper;
    private final GraphPostgresService graphPostgresService;

    public @NonNull BuildGraphPayload buildGraph(BuildGraphInput input) {
        log.debug("Building graph: {}", input.graphId());

        BuildGraphRequest request = mapper.toBuildGraphRequest(input);
        BuildGraphResponse response;
        try {
            response = blockingStub.withDeadlineAfter(TIMEOUT, TIMEOUT_UNIT).buildGraph(request);
        } catch (StatusRuntimeException e) {
            // The engine rejects an invalid definition with INVALID_ARGUMENT and a precise reason
            // (unknown provider, malformed edge condition, ...). Left unwrapped, the raw
            // StatusRuntimeException matched no case in GraphQlExceptionHandler and surfaced as a
            // bare "Internal server error", hiding exactly what the editor needs to show. Mapping
            // it here mirrors what executeGraphStream and the state calls already do:
            // INVALID_ARGUMENT becomes GraphBuildException -> BAD_REQUEST carrying the description,
            // and builder.jsx renders it in its error toast.
            throw wrapGrpcError(e);
        }

        // Persist only after the engine accepted the definition. The Python service validates the
        // config eagerly (e.g. an unknown `config.llm_provider` fails the compile with
        // LLMConfigError), so saving first used to leave an uncompilable definition behind even
        // though the mutation failed. Safe to reorder: the engine compiles straight from the
        // request payload and never reads the saved row back during BuildGraph.
        if (response.getSuccess()) {
            graphPostgresService.saveGraph(input);
        }
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
        return listGraphs(pageSize, pageToken, GraphScopeFilter.anyGraph());
    }

    /**
     * Lists graphs, restricted to the caller's graph scope when one is given.
     * <p>
     * The scope reaches the repository so that {@code total_count} / {@code has_next_page}
     * / {@code next_page_token} describe the rows the caller is allowed to see. The UI
     * (GraphQL) path has no tenant and keeps the unrestricted overload.
     *
     * @param pageSize requested page size; clamped here ({@link #clampPageSize}) so the GraphQL
     *                 path and the B2B facade share one rule
     * @param pageToken numeric offset as a string; null/blank means the first page
     * @param filter   the caller's graph scope
     * @return the filtered page
     */
    public @NonNull GraphListPayload listGraphs(int pageSize, String pageToken, GraphScopeFilter filter) {
        // Clamp before anything computes from it: the page size feeds offset / limit (the GraphQL
        // path could send pageSize: 0 straight into a division by zero -> 500) and
        // nextOffset = offset + pageSize, so an unclamped value corrupts paging as well.
        int clampedPageSize = clampPageSize(pageSize);
        log.debug("Listing graphs with page size: {}", clampedPageSize);

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
            PageData pageData = graphPostgresService.listGraphs(clampedPageSize, offset, filter);
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

            int nextOffset = offset + clampedPageSize;
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

    public @NonNull LlmModelsPayload listLlmModels(String provider) {
        log.debug("Listing LLM models for provider: {}", provider);

        ListLlmModelsRequest request = ListLlmModelsRequest.newBuilder()
                .setProvider(provider != null ? provider : "")
                .build();
        try {
            ListLlmModelsResponse response = blockingStub.withDeadlineAfter(TIMEOUT, TIMEOUT_UNIT).listLlmModels(request);
            return mapper.toLlmModelsPayload(response);
        } catch (StatusRuntimeException e) {
            // The catalog itself never fails; this is only for a transport/service outage.
            throw wrapGrpcError(e);
        }
    }

    /**
     * Ask the Python engine to stop a graph execution that is currently running.
     * <p>
     * Sent on its own (short) RPC while the ExecuteGraph stream for the same thread is still
     * open on another request thread. The blocking stub multiplexes both calls over the shared
     * channel, so this is safe and returns as soon as the engine flags the live run. A
     * {@code cancelled=false} simply means nothing was running - not an error.
     */
    public @NonNull CancelGraphExecutionPayload cancelGraphExecution(String graphId, String threadId) {
        log.debug("Cancelling graph execution for {} (thread {})", graphId, threadId);

        CancelGraphExecutionRequest request = CancelGraphExecutionRequest.newBuilder()
                .setGraphId(graphId)
                .setThreadId(threadId)
                .build();

        try {
            org.sandbox.langgraph.grpc.CancelGraphExecutionResponse response = blockingStub
                    .withDeadlineAfter(TIMEOUT, TIMEOUT_UNIT)
                    .cancelGraphExecution(request);
            log.debug("Cancel graph execution response for {}: cancelled={}", graphId, response.getCancelled());
            return mapper.toCancelGraphExecutionPayload(response);
        } catch (StatusRuntimeException e) {
            // A cancellation is best-effort: surface a transport failure as a clean payload
            // rather than failing the mutation, because the UI has already stopped locally.
            log.warn("Failed to reach the engine to cancel {}: {}", graphId, e.getMessage());
            return new CancelGraphExecutionPayload(false, false, "Cancel request failed: " + e.getMessage());
        }
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

    /** The one page-size rule: default below a single page, cap at {@link #MAX_PAGE_SIZE}. */
    public static int clampPageSize(int requested) {
        if (requested <= 0) {
            return DEFAULT_PAGE_SIZE;
        }
        return Math.min(requested, MAX_PAGE_SIZE);
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
