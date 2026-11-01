package org.sandbox.langgraph.service;

import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.core.model.ExecutionEventType;
import org.sandbox.langgraph.dto.graphql.payload.ExecuteGraphPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphExecutionEventPayload;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

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
        return new ExecuteGraphPayload(true, null, null, null, 0, null, null);
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
        /**
         * Every billing marker seen in the run. Node events carry per-node owners while the
         * final END event already carries the engine's run-level verdict, so the fold has to
         * accept both shapes and still land on one honest word.
         */
        private final Set<String> billing = new HashSet<>();

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
            if (event.llmBilling() != null && !event.llmBilling().isBlank()) {
                this.billing.add(event.llmBilling());
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
                    errorMessage,
                    foldBilling(billing)
            );
        }
    }

    /**
     * {@code null} when no node spent tokens at all; {@code mixed} as soon as a run touched
     * both a personal key and the shared one - a user owning an OpenAI key but not an
     * Anthropic one will hit that, and the UI must not overstate who paid.
     */
    private static String foldBilling(Set<String> seen) {
        if (seen.isEmpty() || seen.stream().allMatch(String::isBlank)) {
            return null;
        }
        if (seen.contains("mixed")) {
            return "mixed";
        }
        if (seen.contains("user") && seen.contains("service")) {
            return "mixed";
        }
        if (seen.contains("user")) {
            return "user";
        }
        if (seen.contains("service")) {
            return "service";
        }
        return null;
    }
}
