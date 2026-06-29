package org.sandbox.langgraph.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.dto.GraphDefinition;
import org.sandbox.langgraph.dto.graphql.payload.redis.GraphViewData;
import org.sandbox.langgraph.exception.LangGraphException;
import org.sandbox.langgraph.service.ui.RedisGraphCoordinatesService;
import org.springframework.data.redis.core.ReactiveRedisOperations;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Service for reading graph data from Redis in read-only mode.
 */
@Service
public class RedisGraphViewService {

    private static final String GRAPH_META_KEY_PREFIX = "graph_def:";

    private final ReactiveRedisOperations<@NonNull String, @NonNull String> redisOperations;
    private final ObjectMapper objectMapper;
    private final RedisGraphCoordinatesService coordinatesService;

    public RedisGraphViewService(
            ReactiveRedisOperations<@NonNull String, @NonNull String> redisOperations,
            ObjectMapper objectMapper,
            RedisGraphCoordinatesService coordinatesService) {
        this.redisOperations = redisOperations;
        this.objectMapper = objectMapper;
        this.coordinatesService = coordinatesService;
    }

    /**
     * Get complete graph data including nodes and edges with conditional information
     */
    public Mono<@NonNull GraphViewData> getGraphViewData(String graphId) {
        String key = GRAPH_META_KEY_PREFIX + graphId;

        Mono<@NonNull GraphDefinition> graphDataMono = redisOperations.opsForValue().get(key)
                .switchIfEmpty(Mono.error(new LangGraphException.GraphNotFoundException("Graph not found: " + graphId)))
                .flatMap(this::parseGraphDefinition);

        Mono<@NonNull Map<String, Map<String, Double>>> coordsMono =
                coordinatesService.getCoordinates(graphId);

        // Mono.zip runs both subscriptions in parallel.
        // .map() (not .flatMap()) is correct here because mapToGraphViewData is synchronous.
        return Mono.zip(graphDataMono, coordsMono)
                .map(tuple -> mapToGraphViewData(graphId, tuple.getT1(), tuple.getT2()));
    }

    private Mono<@NonNull GraphDefinition> parseGraphDefinition(@NonNull String json) {
        try {
            return Mono.just(objectMapper.readValue(json, GraphDefinition.class));
        } catch (JsonProcessingException e) {
            return Mono.error(new IllegalArgumentException("Failed to parse graph definition JSON", e));
        }
    }

    private @NonNull GraphViewData mapToGraphViewData(
            @NonNull String graphId,
            @NonNull GraphDefinition graphDef,
            @NonNull Map<String, Map<String, Double>> coords) {

        List<Map<String, Object>> nodes = graphDef.nodes().stream()
                .filter(Objects::nonNull)
                .map(node -> mapNode(node, coords))
                .toList();

        // EdgeDefinition.toMap() preserves ALL fields (source, target, condition, + extras)
        List<Map<String, Object>> edges = graphDef.edges().stream()
                .filter(Objects::nonNull)
                .map(GraphDefinition.EdgeDefinition::toMap)
                .toList();

        return new GraphViewData(
                graphId,
                graphDef.name(),
                graphDef.status(),
                nodes,
                edges
        );
    }

    private @NonNull Map<String, Object> mapNode(
            GraphDefinition.NodeDefinition node,
            @NonNull Map<String, Map<String, Double>> coordinates) {

        Map<String, Double> pos = coordinates.getOrDefault(node.nodeId(), Map.of());

        // Note: If x/y are 0.0, the frontend might apply auto-layout.
        // LinkedHashMap is used (instead of Map.of) to allow null values,
        // preserving the exact behavior of the original HashMap-based code.
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
