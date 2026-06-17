package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.Map; /**
 * Avoids collision with org.sandbox.langgraph.grpc.ExecuteGraphResponse
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ExecuteGraphPayload(
        boolean success,
        String output,
        String eventType,
        Map<String, Object> state,
        Integer totalTokens,
        String errorMessage
) {}
