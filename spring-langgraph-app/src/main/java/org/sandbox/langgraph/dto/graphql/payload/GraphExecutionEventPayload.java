package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.Map; /**
 * Used specifically for the GraphQL Subscription stream.
 * Avoids collision with org.sandbox.langgraph.grpc.ExecuteGraphResponse
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record GraphExecutionEventPayload(
        String eventType,
        String nodeId,
        String output,
        Map<String, Object> state,
        long timestamp,
        String errorMessage
) {}
