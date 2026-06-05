package org.sandbox.langgraph.dto.graphql.payload;

public record CopilotGraphPayload(
        Boolean success,
        String aiResponse,
        String errorMessage
) {
}
