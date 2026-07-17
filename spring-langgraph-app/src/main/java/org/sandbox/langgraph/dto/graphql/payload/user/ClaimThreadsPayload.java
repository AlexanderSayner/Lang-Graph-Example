package org.sandbox.langgraph.dto.graphql.payload.user;

public record ClaimThreadsPayload(
        Boolean success,
        String message,
        Integer syncedCount
) {
}

