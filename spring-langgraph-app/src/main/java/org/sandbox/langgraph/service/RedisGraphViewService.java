package org.sandbox.langgraph.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.dto.graphql.payload.redis.GraphViewData;
import org.sandbox.langgraph.service.ui.RedisGraphCoordinatesService;
import org.springframework.data.redis.core.ReactiveRedisOperations;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

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

        Mono<@NonNull Map<String, Object>> graphDataMono = redisOperations.opsForValue().get(key)
                .switchIfEmpty(Mono.error(new RuntimeException("Graph not found: " + graphId)))
                .flatMap(data -> {
                    try {
                        TypeReference<Map<String, Object>> typeRef = new TypeReference<>() {
                        };
                        return Mono.just(objectMapper.readValue(data, typeRef));
                    } catch (JsonProcessingException e) {
                        return Mono.error(e);
                    }
                });

        Mono<@NonNull Map<String, Map<String, Double>>> coordsMono = coordinatesService.getCoordinates(graphId);

        return Mono.zip(graphDataMono, coordsMono)
                .flatMap(tuple -> {
                    Map<String, Object> graphData = tuple.getT1();
                    Map<String, Map<String, Double>> coords = tuple.getT2();

                    // Parse nodes and edges
                    TypeReference<List<Map<String, Object>>> nodesTypeRef = new TypeReference<>() {
                    };
                    List<Map<String, Object>> rawNodes = objectMapper.convertValue(graphData.get("nodes"), nodesTypeRef);

                    List<Map<String, Object>> nodes = rawNodes.stream()
                            .map(node -> {
                                if (node == null) {
                                    return new HashMap<String, Object>();
                                }

                                Map<String, Object> metadata = Optional.ofNullable(node.get("metadata"))
                                        .map(m -> objectMapper.convertValue(m, new TypeReference<Map<String, Object>>() {
                                        }))
                                        .orElse(new HashMap<>());

                                Map<String, Object> nodeMap = new HashMap<>();
                                String nodeId = (String) node.get("node_id");
                                nodeMap.put("nodeId", nodeId);
                                nodeMap.put("nodeType", node.get("node_type"));
                                nodeMap.put("handlerName", node.get("handler_name"));
                                nodeMap.put("metadata", metadata);

                                Map<String, Double> pos = coords.getOrDefault(nodeId, new HashMap<>());
                                Map<String, Object> positionMap = new HashMap<>();
                                positionMap.put("x", pos.getOrDefault("x", 0.0));
                                positionMap.put("y", pos.getOrDefault("y", 0.0));

                                // Note: If x/y are 0.0, the frontend might apply auto-layout
                                nodeMap.put("position", positionMap);
                                return nodeMap;
                            })
                            .toList();

                    TypeReference<List<Map<String, Object>>> edgesTypeRef = new TypeReference<>() {
                    };
                    List<Map<String, Object>> edges = objectMapper.convertValue(graphData.get("edges"), edgesTypeRef);

                    return Mono.just(new GraphViewData(
                            graphId,
                            (String) graphData.get("name"),
                            (String) graphData.get("status"),
                            nodes,
                            edges
                    ));
                });
    }

}
