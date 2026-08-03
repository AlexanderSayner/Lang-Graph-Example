package org.sandbox.langgraph.service.ui;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.sandbox.langgraph.core.repository.GraphRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.HashMap;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class PostgresGraphCoordinatesService {
    private static final Logger log = LoggerFactory.getLogger(PostgresGraphCoordinatesService.class);

    private final GraphRepository repository;
    private final ObjectMapper objectMapper;

    @Transactional
    public boolean saveCoordinates(String graphId, Map<String, Map<String, Double>> coordinates) {
        try {
            String json = objectMapper.writeValueAsString(coordinates);
            log.info("Saving coordinates for graph: {}", graphId);

            int rowsAffected = repository.updateCoordinates(graphId, json);
            return rowsAffected > 0;
        } catch (JsonProcessingException e) {
            log.error("Failed to serialize coordinates: {}", e.getMessage());
            throw new RuntimeException("Failed to serialize coordinates", e);
        }
    }

    public Map<String, Map<String, Double>> getCoordinates(String graphId) {
        String json = repository.findCoordinatesByGraphId(graphId).orElse("{}");
        try {
            return objectMapper.readValue(
                    json,
                    new TypeReference<>() {
                    }
            );
        } catch (JsonProcessingException e) {
            log.warn("Failed to parse coordinates for {}: {}", graphId, e.getMessage());
            return new HashMap<>();
        }
    }
}
