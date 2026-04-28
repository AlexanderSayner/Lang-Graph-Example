package org.sandbox.langgraph.service.ui;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.jspecify.annotations.NonNull;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.ReactiveRedisOperations;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;

import java.util.HashMap;
import java.util.Map;

@Service
public class RedisGraphCoordinatesService {
    private static final Logger log = LoggerFactory.getLogger(RedisGraphCoordinatesService.class);
    private static final String COORDS_KEY_PREFIX = "graph_coords:";

    private final ReactiveRedisOperations<@NonNull String, @NonNull String> redisOperations;
    private final ObjectMapper objectMapper;

    public RedisGraphCoordinatesService(
            ReactiveRedisOperations<@NonNull String, @NonNull String> redisOperations,
            ObjectMapper objectMapper) {
        this.redisOperations = redisOperations;
        this.objectMapper = objectMapper;
    }

    /**
     * Saves coordinates map to Redis.
     * Key format: graph_coords:graph-001
     * Value format: { "nodeId": {"x": 100, "y": 200}, ... }
     */
    public Mono<@NonNull Boolean> saveCoordinates(String graphId, Map<String, Map<String, Double>> coordinates) {
        String key = COORDS_KEY_PREFIX + graphId;
        try {
            String json = objectMapper.writeValueAsString(coordinates);
            log.info("Saving coordinates for graph: {}", graphId);
            return redisOperations.opsForValue().set(key, json);
        } catch (JsonProcessingException e) {
            log.error("Failed to serialize coordinates: {}", e.getMessage());
            return Mono.error(new RuntimeException("Failed to serialize coordinates", e));
        }
    }

    /**
     * Loads coordinates map from Redis.
     * Returns an empty map if not found.
     */
    public Mono<@NonNull Map<String, Map<String, Double>>> getCoordinates(String graphId) {
        String key = COORDS_KEY_PREFIX + graphId;
        return redisOperations.opsForValue().get(key)
                .map(json -> {
                    try {
                        TypeReference<Map<String, Map<String, Double>>> typeRef = new TypeReference<>() {
                        };
                        return objectMapper.readValue(json, typeRef);
                    } catch (JsonProcessingException e) {
                        log.warn("Failed to parse coordinates for {}: {}", graphId, e.getMessage());
                        return new HashMap<String, Map<String, Double>>();
                    }
                })
                .defaultIfEmpty(new HashMap<>());
    }
}
