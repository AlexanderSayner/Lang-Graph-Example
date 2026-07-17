package org.sandbox.langgraph.dto.graphql.payload.user;

public record UserThread(
        String threadId,
        String graphId,
        String title,
        String lastActive
) {
}

