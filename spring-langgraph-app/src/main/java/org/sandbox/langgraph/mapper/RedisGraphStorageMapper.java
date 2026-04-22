package org.sandbox.langgraph.mapper;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.sandbox.langgraph.dto.graphql.payload.redis.*;
import org.springframework.stereotype.Component;

import java.util.Map;

@Component
public class RedisGraphStorageMapper {
    private final ObjectMapper objectMapper;

    public RedisGraphStorageMapper(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    public GraphViewPayload toPayload(GraphViewData data) {
        return GraphViewPayload.success(
                data.graphId(),
                data.graphName(),
                data.status(),
                data.nodes().stream()
                        .map(nodeMap -> {
                            Map<String, Object> metadata = objectMapper.convertValue(
                                    nodeMap.get("metadata"),
                                    new TypeReference<>() {
                                    }
                            );
                            Map<String, Object> positionMap = objectMapper.convertValue(
                                    nodeMap.get("position"),
                                    new TypeReference<>() {
                                    }
                            );

                            // Build Position object (nullable, handle null x/y)
                            Position position = null;
                            if (positionMap != null) {
                                Float x = null;
                                Float y = null;
                                if (positionMap.get("x") != null) {
                                    x = ((Number) positionMap.get("x")).floatValue();
                                }
                                if (positionMap.get("y") != null) {
                                    y = ((Number) positionMap.get("y")).floatValue();
                                }
                                if (x != null && y != null) {
                                    position = new Position(x, y);
                                }
                            }

                            return new GraphNode(
                                    (String) nodeMap.get("nodeId"),
                                    (String) nodeMap.get("nodeType"),
                                    (String) nodeMap.get("handlerName"),
                                    metadata,
                                    position
                            );
                        })
                        .toList(),
                data.edges().stream()
                        .map(edgeMap -> new GraphEdge(
                                (String) edgeMap.get("source"),
                                (String) edgeMap.get("target"),
                                (String) edgeMap.get("condition"),
                                (String) edgeMap.get("label")
                        ))
                        .toList()
        );
    }
}
