package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.Map; /**
 * Avoids collision with org.sandbox.langgraph.grpc.UpdateGraphStateResponse
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record UpdateGraphStatePayload(
        boolean success,
        Map<String, Object> updatedState,
        String message
) {}
