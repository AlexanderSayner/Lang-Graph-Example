package org.sandbox.langgraph.dto.graphql.payload;

public record RewindGraphPayload(
        boolean success,
        String message
) {
}
