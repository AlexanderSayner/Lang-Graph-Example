package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude;

@JsonInclude(JsonInclude.Include.NON_NULL)
public record ToolDebugPayload(
        boolean success,
        int statusCode,
        String body,
        String errorMessage
) {
}
