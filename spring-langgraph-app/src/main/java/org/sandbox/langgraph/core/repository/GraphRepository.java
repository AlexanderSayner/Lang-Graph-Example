package org.sandbox.langgraph.core.repository;

import org.sandbox.langgraph.core.model.GraphEntity;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.PagingAndSortingRepository;

import java.util.Optional;

/**
 * {@link JpaSpecificationExecutor} is what lets {@code GraphPostgresService} push an
 * account's graph scope ({@code allowed-graphs}) into the SQL itself. Filtering a page
 * of graphs in Java instead would corrupt the paging the B2B caller sees: total_count,
 * has_next_page and next_page_token are all derived from the page that was fetched.
 */
public interface GraphRepository extends CrudRepository<GraphEntity, String>,
        PagingAndSortingRepository<GraphEntity, String>, JpaSpecificationExecutor<GraphEntity> {

    @Modifying
    @Query(value = "UPDATE core.graphs SET coordinates = CAST(:coords AS jsonb), updated_at = NOW(), version = version + 1 WHERE graph_id = :graphId", nativeQuery = true)
    int updateCoordinates(String graphId, String coords);

    @Query("SELECT coordinates FROM GraphEntity g WHERE g.graphId = :graphId")
    Optional<String> findCoordinatesByGraphId(String graphId);

}
