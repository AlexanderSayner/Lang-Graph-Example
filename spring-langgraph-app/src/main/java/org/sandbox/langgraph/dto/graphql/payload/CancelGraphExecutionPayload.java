package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * GraphQL result of the cancelGraphExecution mutation.
 * <p>
 * Avoids collision with org.sandbox.langgraph.grpc.CancelGraphExecutionResponse.
 * {@code cancelled} is true only when a live execution was actually found and asked to stop;
 * false means the run had already finished or never started - which is not a failure.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record CancelGraphExecutionPayload(
        boolean success,
        boolean cancelled,
        String message
) {
}