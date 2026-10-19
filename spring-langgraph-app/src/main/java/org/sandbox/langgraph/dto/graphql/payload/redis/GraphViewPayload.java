package org.sandbox.langgraph.dto.graphql.payload.redis;

import org.sandbox.langgraph.core.model.GraphStatus;

import java.util.List;
import java.util.Map;

/**
 * Payload for graph visualization query.
 * Contains all data needed to render a graph with conditional edges.
 */
public record GraphViewPayload(
        boolean success,
        String graphId,
        String graphName,
        GraphStatus status,
        List<GraphNode> nodes,
        List<GraphEdge> edges,
        String message,
        Map<String, Object> config
) {
    public static GraphViewPayload success(String graphId, String graphName, GraphStatus status,
                                           List<GraphNode> nodes, List<GraphEdge> edges,
                                           Map<String, Object> config) {
        return new GraphViewPayload(true, graphId, graphName, status, nodes, edges,
                "Graph loaded successfully", config != null ? config : Map.of());
    }

    public static GraphViewPayload error(String message) {
        return new GraphViewPayload(false, null, null, null, List.of(), List.of(), message, Map.of());
    }
}
