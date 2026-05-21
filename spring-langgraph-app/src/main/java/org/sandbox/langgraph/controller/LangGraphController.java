package org.sandbox.langgraph.controller;

import jakarta.validation.Valid;
import org.jspecify.annotations.NonNull;
import org.reactivestreams.Publisher;
import org.sandbox.langgraph.dto.graphql.input.BuildGraphInput;
import org.sandbox.langgraph.dto.graphql.input.ExecuteGraphInput;
import org.sandbox.langgraph.dto.graphql.input.NodePositionInput;
import org.sandbox.langgraph.dto.graphql.input.UpdateGraphStateInput;
import org.sandbox.langgraph.dto.graphql.payload.*;
import org.sandbox.langgraph.dto.graphql.payload.redis.GraphViewPayload;
import org.sandbox.langgraph.grpc.GraphHistoryResponse;
import org.sandbox.langgraph.mapper.RedisGraphStorageMapper;
import org.sandbox.langgraph.service.LangGraphGrpcService;
import org.sandbox.langgraph.service.RedisGraphViewService;
import org.sandbox.langgraph.service.ui.RedisGraphCoordinatesService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.graphql.data.method.annotation.Argument;
import org.springframework.graphql.data.method.annotation.MutationMapping;
import org.springframework.graphql.data.method.annotation.QueryMapping;
import org.springframework.graphql.data.method.annotation.SubscriptionMapping;
import org.springframework.stereotype.Controller;
import org.springframework.validation.annotation.Validated;
import reactor.core.publisher.Mono;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Controller
@Validated
public class LangGraphController {

    private static final Logger log = LoggerFactory.getLogger(LangGraphController.class);

    private final LangGraphGrpcService langGraphService;
    private final RedisGraphViewService redisGraphViewService;
    private final RedisGraphStorageMapper redisGraphStorageMapper;
    private final RedisGraphCoordinatesService coordinatesService;

    public LangGraphController(LangGraphGrpcService langGraphService,
                               RedisGraphViewService redisGraphViewService,
                               RedisGraphStorageMapper redisGraphStorageMapper,
                               RedisGraphCoordinatesService coordinatesService) {
        this.langGraphService = langGraphService;
        this.redisGraphViewService = redisGraphViewService;
        this.redisGraphStorageMapper = redisGraphStorageMapper;
        this.coordinatesService = coordinatesService;
    }

    @QueryMapping
    public Mono<@NonNull GraphListPayload> listGraphs(
            // Maps to pageSize: Int! in schema (primitive requires non-null)
            @Argument int pageSize,
            @Argument String pageToken) {
        return langGraphService.listGraphs(pageSize, pageToken);
    }

    @QueryMapping
    public Mono<@NonNull GraphStatePayload> getGraphState(
            @Argument String graphId,
            @Argument String threadId) {
        return langGraphService.getGraphState(graphId, threadId);
    }

    @QueryMapping
    public Mono<@NonNull GraphViewPayload> getGraphView(@Argument String graphId) {
        log.info("Getting graph view for: {}", graphId);
        return redisGraphViewService.getGraphViewData(graphId)
                .map(redisGraphStorageMapper::toPayload)
                .onErrorResume(e -> {
                    log.error("Error loading graph view for {}: {}", graphId, e.getMessage());
                    return Mono.just(GraphViewPayload.error("Failed to load graph: " + e.getMessage()));
                });
    }

    @QueryMapping
    public Mono<@NonNull GraphHistoryPayload> getExecutionHistory(@Argument String graphId, @Argument String threadId) {
        log.info("Getting execution history for: '{}'-'{}'", graphId, threadId);
        return langGraphService.getExecutionHistory(graphId, threadId);
    }

    @MutationMapping
    public Mono<@NonNull BuildGraphPayload> buildGraph(@Valid @Argument BuildGraphInput input) {
        log.info("Building graph mutation: {}", input.graphId());
        return langGraphService.buildGraph(input);
    }

    @MutationMapping
    public Mono<@NonNull ExecuteGraphPayload> executeGraph(@Valid @Argument ExecuteGraphInput input) {
        log.info("Executing graph: {}", input.graphId());
        return langGraphService.executeGraphStream(input, false)
                .reduce(new ExecuteGraphPayloadAccumulator(), ExecuteGraphPayloadAccumulator::accumulate)
                .map(ExecuteGraphPayloadAccumulator::toPayload)
                .defaultIfEmpty(new ExecuteGraphPayload(true, "", null, null));
    }

    @SubscriptionMapping
    public Publisher<GraphExecutionEventPayload> executeGraphStream(
            @Valid @Argument ExecuteGraphInput input) {
        log.info("Streaming graph execution: {}", input.graphId());
        return langGraphService.executeGraphStream(input, true);
    }

    @MutationMapping
    public Mono<@NonNull UpdateGraphStatePayload> updateGraphState(
            @Valid @Argument UpdateGraphStateInput input) {
        log.info("Updating graph state: {}", input.graphId());
        return langGraphService.updateGraphState(input);
    }

    @MutationMapping
    public Mono<@NonNull DeleteGraphPayload> deleteGraph(@Argument String graphId) {
        log.info("Deleting graph: {}", graphId);
        return langGraphService.deleteGraph(graphId);
    }

    @MutationMapping
    public Mono<@NonNull GraphViewPayload> saveGraphCoordinates(
            @Argument String graphId,
            @Argument List<NodePositionInput> positions) {

        // Convert List<Input> to Map<String, Map<String, Double>> for storage
        Map<String, Map<String, Double>> coordsMap = new HashMap<>();

        if (positions != null) {
            for (NodePositionInput p : positions) {
                Map<String, Double> pos = new HashMap<>();
                pos.put("x", p.x());
                pos.put("y", p.y());
                coordsMap.put(p.nodeId(), pos);
            }
        }

        return coordinatesService.saveCoordinates(graphId, coordsMap)
                .flatMap(savedSuccess -> {
                    if (!savedSuccess) {
                        return Mono.error(new RuntimeException("Redis returned false on save"));
                    }
                    // Fetch the fresh state (which includes new coords)
                    return redisGraphViewService.getGraphViewData(graphId);
                })
                .map(redisGraphStorageMapper::toPayload)
                .onErrorResume(e -> {
                    log.error("Error save graph coordinates for {}: {}", graphId, e.getMessage());
                    return Mono.just(GraphViewPayload.error("Failed to save graph coordinates: " + e.getMessage()));
                });
    }

    // Internal helper class for accumulating stream results
    private static final class ExecuteGraphPayloadAccumulator {
        private String output;
        private final Map<String, Object> state = new HashMap<>();
        private String errorMessage;
        private boolean hasError;

        ExecuteGraphPayloadAccumulator accumulate(GraphExecutionEventPayload event) {
            if (event.output() != null && !event.output().isEmpty()) {
                this.output = event.output();
            }
            if (event.state() != null) {
                this.state.putAll(event.state());
            }
            if ("ERROR".equals(event.eventType())) {
                this.hasError = true;
                this.errorMessage = event.errorMessage();
            }
            return this;
        }

        ExecuteGraphPayload toPayload() {
            return new ExecuteGraphPayload(
                    !hasError,
                    output,
                    state.isEmpty() ? null : state,
                    errorMessage
            );
        }
    }
}
