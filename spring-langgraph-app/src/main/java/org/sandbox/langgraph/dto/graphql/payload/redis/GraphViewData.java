package org.sandbox.langgraph.dto.graphql.payload.redis;

import org.sandbox.langgraph.core.model.GraphStatus;

import java.util.List;
import java.util.Map;

/**
 * DTO for complete graph view data
 */
public record GraphViewData(
        String graphId,
        String graphName,
        GraphStatus status,
        List<Map<String, Object>> nodes,
        List<Map<String, Object>> edges
) {
}
