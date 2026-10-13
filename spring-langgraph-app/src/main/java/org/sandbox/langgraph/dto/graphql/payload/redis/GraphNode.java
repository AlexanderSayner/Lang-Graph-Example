package org.sandbox.langgraph.dto.graphql.payload.redis;

import org.sandbox.langgraph.core.model.NodeType;

import java.util.Map;

/**
 * Graph node representation for visualization
 */
public record GraphNode(
        String nodeId,
        NodeType nodeType,
        String handlerName,
        Map<String, Object> metadata,
        Position position
) {}