package org.sandbox.langgraph.core.repository;

import io.r2dbc.postgresql.codec.Json;
import org.sandbox.langgraph.core.model.GraphEntity;
import org.springframework.data.r2dbc.repository.Modifying;
import org.springframework.data.r2dbc.repository.Query;
import org.springframework.data.repository.reactive.ReactiveCrudRepository;
import reactor.core.publisher.Mono;

public interface GraphRepository extends ReactiveCrudRepository<GraphEntity, String> {

    @Modifying
    @Query("UPDATE core.graphs SET coordinates = CAST(:coords AS jsonb), updated_at = NOW(), version = version + 1 WHERE graph_id = :graphId")
    Mono<Integer> updateCoordinates(String graphId, String coords);

    @Query("SELECT coordinates FROM core.graphs WHERE graph_id = :graphId")
    Mono<Json> findCoordinatesByGraphId(String graphId);

}
