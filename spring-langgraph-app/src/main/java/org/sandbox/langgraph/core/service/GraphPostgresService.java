package org.sandbox.langgraph.core.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.sandbox.langgraph.core.model.GraphEntity;
import org.sandbox.langgraph.core.model.GraphStatus;
import org.sandbox.langgraph.core.repository.GraphRepository;
import org.sandbox.langgraph.dto.graphql.input.BuildGraphInput;
import org.sandbox.langgraph.dto.pagination.PageData;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

@Service
@RequiredArgsConstructor
public class GraphPostgresService {

    private final GraphRepository repository;
    private final ObjectMapper objectMapper;

    public Optional<GraphEntity> findById(String graphId) {
        return repository.findById(graphId);
    }

    @Transactional
    public void saveGraph(BuildGraphInput input) {
        try {
            String definitionJson = objectMapper.writeValueAsString(input);
            String coordinatesJson = "{}";

            GraphEntity entity = new GraphEntity(
                    input.graphId(),
                    input.graphName(),
                    GraphStatus.ACTIVE, // Use the enum directly
                    definitionJson,
                    coordinatesJson,
                    LocalDateTime.now(),
                    LocalDateTime.now(),
                    0L
            );

            repository.save(entity);
        } catch (Exception e) {
            throw new RuntimeException("Failed to serialize graph definition", e);
        }
    }

    public PageData listGraphs(int limit, int offset) {
        // Since offset increments by pageSize, it will always be a clean multiple of limit
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

