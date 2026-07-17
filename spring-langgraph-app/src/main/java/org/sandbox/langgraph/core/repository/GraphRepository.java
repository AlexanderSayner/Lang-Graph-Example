package org.sandbox.langgraph.core.repository;

import org.sandbox.langgraph.core.model.GraphEntity;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.PagingAndSortingRepository;

import java.util.Optional;

public interface GraphRepository extends CrudRepository<GraphEntity, String>, PagingAndSortingRepository<GraphEntity, String> {

    @Modifying
    @Query(value = "UPDATE core.graphs SET coordinates = CAST(:coords AS jsonb), updated_at = NOW(), version = version + 1 WHERE graph_id = :graphId", nativeQuery = true)
    int updateCoordinates(String graphId, String coords);

    @Query("SELECT coordinates FROM GraphEntity g WHERE g.graphId = :graphId")
    Optional<String> findCoordinatesByGraphId(String graphId);

}
