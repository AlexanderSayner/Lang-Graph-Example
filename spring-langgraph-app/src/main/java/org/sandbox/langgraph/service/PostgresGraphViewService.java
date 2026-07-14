package org.sandbox.langgraph.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.r2dbc.postgresql.codec.Json;
import lombok.RequiredArgsConstructor;
import org.sandbox.langgraph.core.model.GraphEntity;
import org.sandbox.langgraph.core.repository.GraphRepository;
import org.sandbox.langgraph.dto.GraphDefinition;
import org.sandbox.langgraph.dto.graphql.payload.redis.GraphViewData;
import org.sandbox.langgraph.exception.LangGraphException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

@Service
@RequiredArgsConstructor
public class PostgresGraphViewService {

    private static final Logger log = LoggerFactory.getLogger(PostgresGraphViewService.class);

    private final GraphRepository repository;
    private final ObjectMapper objectMapper;

    /**
     * Get complete graph data including nodes and edges with conditional information.
     * 🔥 OPTIMIZATION: Because the blueprint and coordinates are in the SAME ROW in Postgres,
     * we no longer need Mono.zip to fetch them from two different places!
     */
    public Mono<GraphViewData> getGraphViewData(String graphId) {
        return repository.findById(graphId)
                .switchIfEmpty(Mono.error(new LangGraphException.GraphNotFoundException("Graph not found: " + graphId)))
                .map(this::mapEntityToViewData);
    }

    private GraphViewData mapEntityToViewData(GraphEntity entity) {
        // Parse the 'definition' JSONB column into the GraphDefinition DTO
        GraphDefinition graphDef = parseDefinition(entity.definition());

        // Parse the 'coordinates' JSONB column into a Map
        Map<String, Map<String, Double>> coords = parseCoordinates(entity.coordinates().asString());

        // Map to the final GraphViewData (Logic preserved exactly from your original class)
        List<Map<String, Object>> nodes = graphDef.nodes().stream()
                .filter(Objects::nonNull)
                .map(node -> mapNode(node, coords))
                .toList();

        List<Map<String, Object>> edges = graphDef.edges().stream()
                .filter(Objects::nonNull)
                .map(GraphDefinition.EdgeDefinition::toMap)
                .toList();

        return new GraphViewData(
                entity.graphId(),
                graphDef.name(),
                entity.status(), // Use the status directly from the Postgres entity
                nodes,
                edges
        );
    }

    private GraphDefinition parseDefinition(Json json) {
        try {
            // Extract the raw string from the R2DBC Json wrapper and parse it
            return objectMapper.readValue(json.asString(), GraphDefinition.class);
        } catch (JsonProcessingException e) {
            log.error("Failed to parse graph definition JSON", e);
            throw new IllegalArgumentException("Failed to parse graph definition JSON", e);
        }
    }

    private Map<String, Map<String, Double>> parseCoordinates(String json) {
        if (json == null || json.isBlank()) {
            return new LinkedHashMap<>();
        }
        try {
            return objectMapper.readValue(
                    json,
                    new TypeReference<>() {
                    }
            );
        } catch (JsonProcessingException e) {
            log.warn("Failed to parse coordinates JSON", e);
            return new LinkedHashMap<>();
        }
    }

    private Map<String, Object> mapNode(
            GraphDefinition.NodeDefinition node,
            Map<String, Map<String, Double>> coordinates) {

        Map<String, Double> pos = coordinates.getOrDefault(node.nodeId(), Map.of());

        Map<String, Object> positionMap = new LinkedHashMap<>();
        positionMap.put("x", pos.getOrDefault("x", 0.0));
        positionMap.put("y", pos.getOrDefault("y", 0.0));

        Map<String, Object> nodeMap = new LinkedHashMap<>();
        nodeMap.put("nodeId", node.nodeId());
        nodeMap.put("nodeType", node.nodeType());
        nodeMap.put("handlerName", node.handlerName());
        nodeMap.put("metadata", node.metadata() != null ? node.metadata() : new LinkedHashMap<>());
        nodeMap.put("position", positionMap);

        return nodeMap;
    }
}
