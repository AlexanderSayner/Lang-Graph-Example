package org.sandbox.langgraph.dto.graphql.payload;

public record CopilotGraphPayload(
        Boolean success,
        String aiResponse,
        Integer totalTokens,
        String errorMessage
) {
}
