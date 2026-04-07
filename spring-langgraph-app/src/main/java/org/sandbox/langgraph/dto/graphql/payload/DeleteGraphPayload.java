package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * Avoids collision with org.sandbox.langgraph.grpc.DeleteGraphResponse
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record DeleteGraphPayload(
        boolean success,
        String message
) {
}
