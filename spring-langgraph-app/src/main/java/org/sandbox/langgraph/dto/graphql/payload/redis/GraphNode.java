package org.sandbox.langgraph.dto.graphql.payload.redis;

import java.util.Map;

/**
 * Graph node representation for visualization
 */
public record GraphNode(
        String nodeId,
        String nodeType,
        String handlerName,
        Map<String, Object> metadata,
        Position position
) {}