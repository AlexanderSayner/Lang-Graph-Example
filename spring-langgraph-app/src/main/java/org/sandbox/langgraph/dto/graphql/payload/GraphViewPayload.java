package org.sandbox.langgraph.dto.graphql.payload;

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

/**
 * Graph node representation for visualization
 */
record GraphNode(
        String nodeId,
        String nodeType,
        String handlerName,
        Map<String, Object> metadata,
        Position position
) {}

/**
 * Graph edge representation for visualization with conditional support
 */
record GraphEdge(
        String source,
        String target,
        String condition,
        String label
) {}

/**
 * 2D position for node layout
 */
record Position(
        Float x,
        Float y
) {}
