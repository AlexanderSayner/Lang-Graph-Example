package org.sandbox.langgraph.core.service;

import io.r2dbc.postgresql.codec.Json;
import lombok.RequiredArgsConstructor;
import org.sandbox.langgraph.core.model.GraphEntity;
import org.sandbox.langgraph.core.repository.GraphRepository;
import org.sandbox.langgraph.dto.graphql.input.BuildGraphInput;
import org.sandbox.langgraph.dto.pagination.PageData;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;
import tools.jackson.databind.ObjectMapper;

import java.util.List;

@Service
@RequiredArgsConstructor
public class GraphPostgresService {
    private final GraphRepository repository;
    private final ObjectMapper objectMapper;

    public Mono<GraphEntity> saveGraph(BuildGraphInput input) {
        // 1. Check if the graph already exists
        return repository.findById(input.graphId())
                .flatMap(existingEntity -> {
                    // --- UPDATE SCENARIO ---
                    try {
                        String defJson = objectMapper.writeValueAsString(input);
                        GraphEntity updatedEntity = new GraphEntity(
                                existingEntity.graphId(),
                                input.graphName(),
                                existingEntity.status(),
                                Json.of(defJson),
                                existingEntity.coordinates(),
                                existingEntity.createdAt(),
                                null,
                                existingEntity.version()
                        );
                        return repository.save(updatedEntity);
                    } catch (Exception e) {
                        return Mono.error(new RuntimeException("Failed to serialize graph definition", e));
                    }
                })
                .switchIfEmpty(Mono.defer(() -> {
                    try {
                        String defJson = objectMapper.writeValueAsString(input);
                        GraphEntity newEntity = new GraphEntity(
                                input.graphId(),
                                input.graphName(),
                                "ACTIVE",
                                Json.of(defJson),
                                Json.of("{}"),
                                null,
                                null,
                                null
                        );
                        return repository.save(newEntity);
                    } catch (Exception e) {
                        return Mono.error(new RuntimeException("Failed to serialize graph definition", e));
                    }
                }));
    }

    public Mono<PageData> listGraphs(int limit, int offset) {
        Mono<List<GraphEntity>> dataMono = repository.findAll()
                .skip(offset)
                .take(limit)
                .collectList();

        Mono<Long> countMono = repository.count();

        return Mono.zip(dataMono, countMono)
                .map(tuple -> new PageData(tuple.getT1(), tuple.getT2().intValue()));
    }

    public Mono<Boolean> deleteGraph(String graphId) {
        return repository.existsById(graphId)
                .flatMap(exists -> {
                    if (!exists) {
                        return Mono.just(false);
                    }
                    return repository.deleteById(graphId).then(Mono.just(true));
                });
    }

}
