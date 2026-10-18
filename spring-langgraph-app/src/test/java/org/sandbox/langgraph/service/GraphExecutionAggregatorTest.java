package org.sandbox.langgraph.service;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.core.model.ExecutionEventType;
import org.sandbox.langgraph.dto.graphql.payload.ExecuteGraphPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphExecutionEventPayload;
import reactor.core.publisher.Flux;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The folding of a run's event stream into the single payload a blocking caller
 * sees. Shared by the GraphQL mutation and the B2B {@code ExecuteGraph} RPC, so
 * a behaviour change here changes both surfaces at once.
 */
class GraphExecutionAggregatorTest {

    private final GraphExecutionAggregator aggregator = new GraphExecutionAggregator();

    @Test
    @DisplayName("the last non-empty output wins and state keys accumulate")
    void accumulatesOutputAndState() {
        Flux<GraphExecutionEventPayload> events = Flux.just(
                event(ExecutionEventType.NODE_END, "first", Map.of("step", "1"), 10),
                event(ExecutionEventType.NODE_END, "second", Map.of("answer", "42"), 25),
                event(ExecutionEventType.END, "", Map.of(), 25)
        );

        ExecuteGraphPayload payload = aggregator.aggregate(events).block();

        assertThat(payload.success()).isTrue();
        assertThat(payload.output()).isEqualTo("second");
        assertThat(payload.eventType()).isEqualTo(ExecutionEventType.END);
        assertThat(payload.state()).containsEntry("step", "1").containsEntry("answer", "42");
        assertThat(payload.totalTokens()).isEqualTo(25);
        assertThat(payload.errorMessage()).isNull();
    }

    @Test
    @DisplayName("an ERROR event marks the whole run failed and keeps the message")
    void reportsErrorEvents() {
        Flux<GraphExecutionEventPayload> events = Flux.just(
                event(ExecutionEventType.NODE_END, "partial", null, 5),
                event(ExecutionEventType.ERROR, "", null, 5, "engine exploded")
        );

        ExecuteGraphPayload payload = aggregator.aggregate(events).block();

        assertThat(payload.success()).isFalse();
        assertThat(payload.errorMessage()).isEqualTo("engine exploded");
        assertThat(payload.eventType()).isEqualTo(ExecutionEventType.ERROR);
    }

    @Test
    @DisplayName("an empty stream produces an empty successful payload")
    void emptyStream() {
        ExecuteGraphPayload payload = aggregator.aggregate(Flux.empty()).block();

        assertThat(payload).isNotNull();
        assertThat(payload.success()).isTrue();
        assertThat(payload.output()).isNull();
        assertThat(payload.state()).isNull();
    }

    /**
     * The one definition of "nothing happened": a blocking caller that reaches the fallback (unary
     * B2B ExecuteGraph) must describe an empty run exactly like the aggregator does, so the two
     * cannot drift into two different empty payloads.
     */
    @Test
    @DisplayName("emptyPayload() is what an empty stream aggregates to")
    void emptyPayloadMatchesTheAggregator() {
        assertThat(GraphExecutionAggregator.emptyPayload())
                .isEqualTo(aggregator.aggregate(Flux.empty()).block());
    }

    private static GraphExecutionEventPayload event(ExecutionEventType type,
                                                    String output,
                                                    Map<String, Object> state,
                                                    int tokens) {
        return event(type, output, state, tokens, null);
    }

    private static GraphExecutionEventPayload event(ExecutionEventType type,
                                                    String output,
                                                    Map<String, Object> state,
                                                    int tokens,
                                                    String errorMessage) {
        return new GraphExecutionEventPayload(type, "node", output, state, 1L, tokens, errorMessage);
    }
}
