package org.sandbox.langgraph.dto.graphql.payload.user;

public record ClaimThreadInput(
        String threadId,
        String graphId,
        String title
) {
}

