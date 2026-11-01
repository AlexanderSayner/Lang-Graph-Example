package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude;
import org.sandbox.langgraph.core.model.ExecutionEventType;

import java.util.Map; /**
 * Avoids collision with org.sandbox.langgraph.grpc.ExecuteGraphResponse
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ExecuteGraphPayload(
        boolean success,
        String output,
        ExecutionEventType eventType,
        Map<String, Object> state,
        Integer totalTokens,
        String errorMessage,
        /**
         * Whole-run billing: "user", "service", "mixed" (a run spanning both) or null when no
         * node called a model at all. Folded by {@code GraphExecutionAggregator}.
         */
        String llmBilling
) {}
