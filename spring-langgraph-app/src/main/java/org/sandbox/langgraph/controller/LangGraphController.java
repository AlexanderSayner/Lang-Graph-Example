package org.sandbox.langgraph.controller;

import jakarta.validation.Valid;
import org.jspecify.annotations.NonNull;
import org.reactivestreams.Publisher;
import org.sandbox.langgraph.dto.graphql.input.BuildGraphInput;
import org.sandbox.langgraph.dto.graphql.input.ExecuteGraphInput;
import org.sandbox.langgraph.dto.graphql.input.UpdateGraphStateInput;
import org.sandbox.langgraph.dto.graphql.payload.*;
import org.sandbox.langgraph.service.LangGraphGrpcService;
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
import java.util.Map;

@Controller
@Validated
public class LangGraphController {

    private static final Logger log = LoggerFactory.getLogger(LangGraphController.class);

    private final LangGraphGrpcService langGraphService;

    public LangGraphController(LangGraphGrpcService langGraphService) {
        this.langGraphService = langGraphService;
    }

    @QueryMapping
    public Mono<@NonNull GraphListPayload> listGraphs(
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

    @MutationMapping
    public Mono<@NonNull BuildGraphPayload> buildGraph(@Valid @Argument BuildGraphInput input) {
        log.info("Building graph: {}", input.graphId());
        return langGraphService.buildGraph(input);
    }

    @MutationMapping
    public Mono<@NonNull ExecuteGraphPayload> executeGraph(@Valid @Argument ExecuteGraphInput input) {
        log.info("Executing graph: {}", input.graphId());
        return langGraphService.executeGraphStream(input, false)
                .reduce(new ExecuteGraphPayloadAccumulator(), ExecuteGraphPayloadAccumulator::accumulate)
                .map(ExecuteGraphPayloadAccumulator::toPayload)
                .defaultIfEmpty(new ExecuteGraphPayload(true, "", null, null)); // Fix: Use Record constructor
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
