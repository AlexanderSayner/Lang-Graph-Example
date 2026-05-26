package org.sandbox.langgraph.dto.graphql.input;

public record ToolDebugInput(
        String method,
        String url,
        String headers,    // JSON String
        String body,
        String variables   // JSON String
) {
}
