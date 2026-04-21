package org.sandbox.langgraph.controller;

import jakarta.validation.Valid;
import org.jspecify.annotations.NonNull;
import org.reactivestreams.Publisher;
import org.sandbox.langgraph.dto.graphql.input.BuildGraphInput;
import org.sandbox.langgraph.dto.graphql.input.ExecuteGraphInput;
import org.sandbox.langgraph.dto.graphql.input.UpdateGraphStateInput;
import org.sandbox.langgraph.dto.graphql.payload.*;
import org.sandbox.langgraph.service.LangGraphGrpcService;
import org.sandbox.langgraph.service.RedisGraphViewService;
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

    public LangGraphController(LangGraphGrpcService langGraphService, 
                               RedisGraphViewService redisGraphViewService) {
        this.langGraphService = langGraphService;
        this.redisGraphViewService = redisGraphViewService;
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
                .map(data -> {
                    List<GraphNode> nodes = data.nodes().stream()
                            .map(nodeMap -> new GraphNode(
                                    (String) nodeMap.get("nodeId"),
                                    (String) nodeMap.get("nodeType"),
                                    (String) nodeMap.get("handlerName"),
                                    (Map<String, Object>) nodeMap.get("metadata"),
                                    null // Position can be auto-calculated by frontend
                            ))
                            .toList();

                    List<GraphEdge> edges = data.edges().stream()
                            .map(edgeMap -> {
                                String condition = (String) edgeMap.get("condition");
                                return new GraphEdge(
                                        (String) edgeMap.get("source"),
                                        (String) edgeMap.get("target"),
                                        condition,
                                        condition != null && !condition.isBlank() ? condition : null
                                );
                            })
                            .toList();

                    return GraphViewPayload.success(
                            data.graphId(),
                            data.graphName(),
                            data.status(),
                            nodes,
                            edges
                    );
                })
                .onErrorResume(e -> {
                    log.error("Error loading graph view for {}: {}", graphId, e.getMessage());
                    return Mono.just(GraphViewPayload.error("Failed to load graph: " + e.getMessage()));
                });
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
