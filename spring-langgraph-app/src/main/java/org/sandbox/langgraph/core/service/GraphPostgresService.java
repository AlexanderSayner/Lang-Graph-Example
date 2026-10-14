package org.sandbox.langgraph.core.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.sandbox.langgraph.core.model.GraphEntity;
import org.sandbox.langgraph.core.model.GraphStatus;
import org.sandbox.langgraph.core.repository.GraphRepository;
import org.sandbox.langgraph.dto.graphql.input.BuildGraphInput;
import org.sandbox.langgraph.dto.graphql.input.NodePositionInput;
import org.sandbox.langgraph.dto.pagination.PageData;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@Service
@RequiredArgsConstructor
public class GraphPostgresService {

    private final GraphRepository repository;
    private final ObjectMapper objectMapper;

    public Optional<GraphEntity> findById(String graphId) {
        return repository.findById(graphId);
    }

    /**
     * Persists the graph definition together with its UI layout in a single row write.
     * <p>
     * The {@code coordinates} JSONB column is derived from {@link BuildGraphInput#positions()} instead
     * of being reset to {@code "{}"}, so a build saves the layout atomically with the definition and no
     * longer depends on a follow-up {@code saveGraphCoordinates} call. When the client sends no
     * positions (non-UI callers) the behaviour is unchanged: the layout stays empty.
     */
    @Transactional
    public void saveGraph(BuildGraphInput input) {
        try {
            String definitionJson = objectMapper.writeValueAsString(input);
            String coordinatesJson = coordinatesJson(input.positions());

            GraphEntity entity = new GraphEntity(
                    input.graphId(),
                    input.graphName(),
                    GraphStatus.ACTIVE,
                    definitionJson,
                    coordinatesJson,
                    LocalDateTime.now(),
                    LocalDateTime.now(),
                    0L
            );

            repository.save(entity);
        } catch (JsonProcessingException e) {
            throw new RuntimeException("Failed to serialize graph definition or coordinates", e);
        } catch (Exception e) {
            throw new RuntimeException("Failed to persist graph", e);
        }
    }

    /**
     * Maps the incoming positions to the persisted coordinate shape
     * {@code {"node_1": {"x": 100.0, "y": 200.0}}} - the exact structure
     * {@code PostgresGraphCoordinatesService} writes and {@code PostgresGraphViewService} reads.
     *
     * @return {@code "{}"} when no positions are supplied, otherwise the serialized coordinate map
     */
    private String coordinatesJson(List<NodePositionInput> positions) throws JsonProcessingException {
        if (positions == null || positions.isEmpty()) {
            return "{}";
        }

        Map<String, Map<String, Double>> coordinates = new LinkedHashMap<>();
        for (NodePositionInput position : positions) {
            if (position.nodeId() == null || position.nodeId().isBlank()) {
                continue;
            }
            coordinates.put(position.nodeId(), Map.of("x", position.x(), "y", position.y()));
        }
        return objectMapper.writeValueAsString(coordinates);
    }

    public PageData listGraphs(int limit, int offset) {
        int page = offset / limit;
        List<GraphEntity> entities = repository.findAll(PageRequest.of(page, limit)).getContent();
        long totalCount = repository.count();

        return new PageData(entities, (int) totalCount);
    }

    @Transactional
    public boolean deleteGraph(String graphId) {
        if (!repository.existsById(graphId)) {
            return false;
        }
        repository.deleteById(graphId);
        return true;
    }
}

