package org.sandbox.langgraph.dto.graphql.payload.user;

public record UserPayload(
        Boolean success,
        String username,
        String userId,
        String message
) {
}
