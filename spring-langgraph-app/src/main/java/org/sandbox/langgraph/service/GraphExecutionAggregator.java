package org.sandbox.langgraph.service;

import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.core.model.ExecutionEventType;
import org.sandbox.langgraph.dto.graphql.payload.ExecuteGraphPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphExecutionEventPayload;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.util.HashMap;
import java.util.Map;

/**
 * Folds an execution event stream into the single {@link ExecuteGraphPayload} a
 * blocking caller expects.
 * <p>
 * Shared by the GraphQL {@code executeGraph} mutation and the B2B
 * {@code ExecuteGraph} RPC so the two surfaces keep identical semantics for the
 * final output, the error flag, the accumulated state and the token counter -
 * exactly one implementation of "what does a finished run look like".
 */
@Service
public class GraphExecutionAggregator {

    /**
     * @param events the stream of events of one run; may be an empty Flux, in which
     *               case an empty successful payload is returned
     * @return a single payload carrying the last non-empty output, the final event
     * type, the merged state, the highest token count and the error message of the
     * run's ERROR event (if any)
     */
    public @NonNull Mono<ExecuteGraphPayload> aggregate(Flux<@NonNull GraphExecutionEventPayload> events) {
        return events.reduce(new Accumulator(), Accumulator::accumulate)
                .map(Accumulator::toPayload);
    }

    /**
     * The shape of a run that produced no events at all, in exactly one place.
     * <p>
     * {@link #aggregate(Flux)} folds an empty stream into precisely these values ({@code Flux.reduce}
     * emits its seed), so this is the fallback a <i>blocking</i> caller - the unary B2B
     * {@code ExecuteGraph} RPC - uses when its Mono comes back empty. Keeping the literal here (and
     * not in a handler) is what stops the two surfaces from describing "nothing happened" with two
     * different payloads.
     *
     * @return an empty run: success, no output, no event type, no state, zero tokens, no error
     */
    public static ExecuteGraphPayload emptyPayload() {
        return new ExecuteGraphPayload(true, null, null, null, 0, null);
    }

    /**
     * Internal helper class for accumulating stream results.
     * <p>
     * Mirrors the behaviour the GraphQL controller had before the accumulator was
     * extracted: the last non-empty output wins, state keys are overwritten by later
     * events, an ERROR marks the whole run failed and the token counter keeps the
     * highest value seen.
     */
    private static final class Accumulator {
        private String output;
        private ExecutionEventType eventType;
        private final Map<String, Object> state = new HashMap<>();
        private String errorMessage;
        private boolean hasError;
        private Integer totalTokens = 0;

        Accumulator accumulate(GraphExecutionEventPayload event) {
            if (event.output() != null && !event.output().isEmpty()) {
                this.output = event.output();
            }
            eventType = event.eventType();
            if (event.state() != null) {
                this.state.putAll(event.state());
            }
            if (event.eventType() == ExecutionEventType.ERROR) {
                this.hasError = true;
                this.errorMessage = event.errorMessage();
            }
            if (event.totalTokens() != null && event.totalTokens() > this.totalTokens) {
                this.totalTokens = event.totalTokens();
            }
            return this;
        }

        ExecuteGraphPayload toPayload() {
            return new ExecuteGraphPayload(
                    !hasError,
                    output,
                    eventType,
                    state.isEmpty() ? null : state,
                    totalTokens,
                    errorMessage
            );
        }
    }
}
