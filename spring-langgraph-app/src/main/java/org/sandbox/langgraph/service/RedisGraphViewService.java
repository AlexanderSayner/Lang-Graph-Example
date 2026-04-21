package org.sandbox.langgraph.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.ReactiveRedisOperations;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Service for reading graph data from Redis in read-only mode.
 * Graphs are stored with the following structure:
 * - Graph metadata: graph:{graphId}:meta -> JSON with graphName, nodeCount, status, createdAt
 * - Nodes: graph:{graphId}:nodes -> JSON array of node definitions
 * - Edges: graph:{graphId}:edges -> JSON array of edge definitions
 */
@Service
public class RedisGraphViewService {

    private static final Logger log = LoggerFactory.getLogger(RedisGraphViewService.class);
    private static final String GRAPH_META_KEY_PREFIX = "graph:";
    private static final String GRAPH_META_SUFFIX = ":meta";
    private static final String GRAPH_NODES_SUFFIX = ":nodes";
    private static final String GRAPH_EDGES_SUFFIX = ":edges";
    private static final String ALL_GRAPHS_INDEX_KEY = "graphs:index";

    private final ReactiveRedisOperations<String, String> redisOperations;
    private final ObjectMapper objectMapper;

    public RedisGraphViewService(
            ReactiveRedisOperations<String, String> redisOperations,
            ObjectMapper objectMapper) {
        this.redisOperations = redisOperations;
        this.objectMapper = objectMapper;
    }

    /**
     * Get all graph summaries from Redis
     */
    public Mono<List<GraphSummary>> getAllGraphSummaries() {
        return redisOperations.opsForSet().members(ALL_GRAPHS_INDEX_KEY)
                .collectList()
                .flatMapMany(graphIds -> Flux.fromIterable(graphIds)
                        .concatMap(this::getGraphSummaryById)
                        .filter(summary -> summary != null)
                        .collectList())
                .defaultIfEmpty(List.of());
    }

    /**
     * Get a single graph summary by ID
     */
    public Mono<GraphSummary> getGraphSummaryById(String graphId) {
        String metaKey = GRAPH_META_KEY_PREFIX + graphId + GRAPH_META_SUFFIX;
        return redisOperations.opsForValue().get(metaKey)
                .flatMap(metaJson -> {
                    try {
                        Map<String, Object> meta = objectMapper.readValue(metaJson, Map.class);
                        return Mono.just(new GraphSummary(
                                graphId,
                                (String) meta.get("graphName"),
                                ((Number) meta.get("nodeCount")).intValue(),
                                (String) meta.get("createdAt"),
                                (String) meta.get("status")
                        ));
                    } catch (JsonProcessingException e) {
                        log.error("Error parsing graph metadata for {}: {}", graphId, e.getMessage());
                        return Mono.empty();
                    }
                })
                .onErrorResume(e -> {
                    log.error("Error retrieving graph summary for {}: {}", graphId, e.getMessage());
                    return Mono.empty();
                });
    }

    /**
     * Get complete graph data including nodes and edges with conditional information
     */
    public Mono<GraphViewData> getGraphViewData(String graphId) {
        String metaKey = GRAPH_META_KEY_PREFIX + graphId + GRAPH_META_SUFFIX;
        String nodesKey = GRAPH_META_KEY_PREFIX + graphId + GRAPH_NODES_SUFFIX;
        String edgesKey = GRAPH_META_KEY_PREFIX + graphId + GRAPH_EDGES_SUFFIX;

        Mono<String> metaMono = redisOperations.opsForValue().get(metaKey);
        Mono<String> nodesMono = redisOperations.opsForValue().get(nodesKey);
        Mono<String> edgesMono = redisOperations.opsForValue().get(edgesKey);

        return Mono.zip(metaMono, nodesMono, edgesMono)
                .map(tuple -> {
                    String metaJson = tuple.getT1();
                    String nodesJson = tuple.getT2();
                    String edgesJson = tuple.getT3();

                    try {
                        Map<String, Object> meta = objectMapper.readValue(metaJson, Map.class);
                        List<Map<String, Object>> nodes = objectMapper.readValue(
                                nodesJson, 
                                new com.fasterxml.jackson.core.type.TypeReference<List<Map<String, Object>>>() {}
                        );
                        List<Map<String, Object>> edges = objectMapper.readValue(
                                edgesJson,
                                new com.fasterxml.jackson.core.type.TypeReference<List<Map<String, Object>>>() {}
                        );

                        return new GraphViewData(
                                graphId,
                                (String) meta.get("graphName"),
                                (String) meta.get("status"),
                                nodes,
                                edges
                        );
                    } catch (JsonProcessingException e) {
                        log.error("Error parsing graph data for {}: {}", graphId, e.getMessage());
                        throw new RuntimeException("Failed to parse graph data", e);
                    }
                })
                .onErrorResume(e -> {
                    log.error("Error retrieving graph view data for {}: {}", graphId, e.getMessage());
                    return Mono.empty();
                });
    }

    /**
     * Check if a graph exists in Redis
     */
    public Mono<Boolean> graphExists(String graphId) {
        String metaKey = GRAPH_META_KEY_PREFIX + graphId + GRAPH_META_SUFFIX;
        return redisOperations.opsForValue().get(metaKey)
                .hasElement()
                .onErrorReturn(false);
    }

    /**
     * DTO for complete graph view data
     */
    public record GraphViewData(
            String graphId,
            String graphName,
            String status,
            List<Map<String, Object>> nodes,
            List<Map<String, Object>> edges
    ) {}

    /**
     * DTO for graph summary (matches existing GraphQL type)
     */
    public record GraphSummary(
            String graphId,
            String graphName,
            int nodeCount,
            String createdAt,
            String status
    ) {}
}
