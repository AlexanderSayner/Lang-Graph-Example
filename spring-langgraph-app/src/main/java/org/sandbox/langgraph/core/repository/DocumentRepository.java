package org.sandbox.langgraph.core.repository;

import org.sandbox.langgraph.core.model.DocumentEntity;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;

/**
 * Lookups on documents are id-only on the hot path (the engine fetches by the opaque id the
 * run's context names), so the primary key is the only index the fetch ever touches.
 */
public interface DocumentRepository extends CrudRepository<DocumentEntity, String> {

    /** Rows whose retention deadline has elapsed - the only query the sweeper makes. */
    List<DocumentEntity> findByExpiresAtBefore(Instant now);

    /**
     * The count for one owner, used to enforce a per-owner upload ceiling. Explicit JPQL
     * rather than a derived method so the owner-kind and owner-id pair stays together.
     */
    @Query("select count(d) from DocumentEntity d where d.ownerKind = :kind and d.ownerId = :id")
    long countByOwner(@Param("kind") String ownerKind, @Param("id") String ownerId);
}
