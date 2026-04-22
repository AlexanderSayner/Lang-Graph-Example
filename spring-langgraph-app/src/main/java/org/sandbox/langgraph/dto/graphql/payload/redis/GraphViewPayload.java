package org.sandbox.langgraph.dto.graphql.payload.redis;

import java.util.List;

/**
 * Payload for graph visualization query.
 * Contains all data needed to render a graph with conditional edges.
 */
public record GraphViewPayload(
        boolean success,
        String graphId,
        String graphName,
        String status,
        List<GraphNode> nodes,
        List<GraphEdge> edges,
        String message
) {
    public static GraphViewPayload success(String graphId, String graphName, String status, 
                                           List<GraphNode> nodes, List<GraphEdge> edges) {
        return new GraphViewPayload(true, graphId, graphName, status, nodes, edges, "Graph loaded successfully");
    }

    public static GraphViewPayload error(String message) {
        return new GraphViewPayload(false, null, null, null, List.of(), List.of(), message);
    }
}
