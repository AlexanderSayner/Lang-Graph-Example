package org.sandbox.langgraph.dto.graphql.payload.redis;

/**
 * Graph edge representation for visualization with conditional support
 */
public record GraphEdge(
        String source,
        String target,
        String condition,
        String label
) {}

