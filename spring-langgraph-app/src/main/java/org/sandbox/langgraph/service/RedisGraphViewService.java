package org.sandbox.langgraph.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.dto.graphql.payload.redis.GraphViewData;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
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

    private static final Logger log = LoggerFactory.getLogger(RedisGraphViewService.class);
    private static final String GRAPH_META_KEY_PREFIX = "graph_def:";

    private final ReactiveRedisOperations<@NonNull String, @NonNull String> redisOperations;
    private final ObjectMapper objectMapper;

    public RedisGraphViewService(
            ReactiveRedisOperations<@NonNull String, @NonNull String> redisOperations,
            ObjectMapper objectMapper) {
        this.redisOperations = redisOperations;
        this.objectMapper = objectMapper;
    }

    /**
     * Get complete graph data including nodes and edges with conditional information
     */
    public Mono<@NonNull GraphViewData> getGraphViewData(String graphId) {
        String key = GRAPH_META_KEY_PREFIX + graphId;
        return redisOperations.opsForValue().get(key)
                .switchIfEmpty(Mono.error(new RuntimeException("Graph not found: " + graphId)))
                .flatMap(data -> {
                    try {
                        // Parse the single JSON key
                        TypeReference<Map<String, Object>> typeRef = new TypeReference<>() {
                        };
                        Map<String, Object> graphData = objectMapper.readValue(data, typeRef);

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

                                    Map<String, Object> position = Optional.ofNullable(node.get("position"))
                                            .map(p -> objectMapper.convertValue(p, new TypeReference<Map<String, Object>>() {
                                            }))
                                            .orElse(new HashMap<>());

                                    Map<String, Object> nodeMap = new HashMap<>();
                                    nodeMap.put("nodeId", node.get("node_id"));
                                    nodeMap.put("nodeType", node.get("node_type"));
                                    nodeMap.put("handlerName", node.get("handler_name"));
                                    nodeMap.put("metadata", metadata);
                                    nodeMap.put("position", position);
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
                    } catch (JsonProcessingException e) {
                        log.error("Error parsing graph data for {}: {}", graphId, e.getMessage());
                        return Mono.error(new RuntimeException("Failed to parse graph data: " + e.getMessage(), e));
                    }
                });
    }

}
