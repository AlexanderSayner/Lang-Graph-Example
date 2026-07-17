package org.sandbox.langgraph.dto.graphql.payload.user;

public record AuthPayload(
        Boolean success,
        String message,
        String username
) {
}

