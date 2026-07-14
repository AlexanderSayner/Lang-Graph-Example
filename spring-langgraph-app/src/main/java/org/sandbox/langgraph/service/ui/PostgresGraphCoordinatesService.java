package org.sandbox.langgraph.service.ui;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.sandbox.langgraph.core.repository.GraphRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;

import java.util.HashMap;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class PostgresGraphCoordinatesService {
    private static final Logger log = LoggerFactory.getLogger(PostgresGraphCoordinatesService.class);

    private final GraphRepository repository;
    private final ObjectMapper objectMapper;

    /**
     * Saves coordinates map directly to the 'coordinates' JSONB column in Postgres.
     */
    public Mono<Boolean> saveCoordinates(String graphId, Map<String, Map<String, Double>> coordinates) {
        try {
            String json = objectMapper.writeValueAsString(coordinates);
            log.info("Saving coordinates for graph: {}", graphId);

            // Executes the custom query, updating JSONB and bumping the @Version
            return repository.updateCoordinates(graphId, json)
                    .map(rowsAffected -> rowsAffected > 0);
        } catch (JsonProcessingException e) {
            log.error("Failed to serialize coordinates: {}", e.getMessage());
            return Mono.error(new RuntimeException("Failed to serialize coordinates", e));
        }
    }

    /**
     * Loads coordinates map from the 'coordinates' JSONB column in Postgres.
     */
    public Mono<Map<String, Map<String, Double>>> getCoordinates(String graphId) {
        return repository.findCoordinatesByGraphId(graphId)
                .map(json -> {
                    try {
                        return objectMapper.readValue(
                                json.asString(),
                                new TypeReference<Map<String, Map<String, Double>>>() {
                                }
                        );
                    } catch (JsonProcessingException e) {
                        log.warn("Failed to parse coordinates for {}: {}", graphId, e.getMessage());
                        return new HashMap<String, Map<String, Double>>();
                    }
                })
                .defaultIfEmpty(new HashMap<>());
    }
}
