package org.sandbox.langgraph.core.repository;

import org.sandbox.langgraph.core.model.UserLlmCredentialEntity;
import org.springframework.data.repository.CrudRepository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * Every lookup this repository supports is scoped by {@code user_id}: there is no query
 * here that can return another user's key, which is the property the request path relies
 * on (credentials are resolved once per call, from the authenticated principal).
 */
public interface UserLlmCredentialRepository extends CrudRepository<UserLlmCredentialEntity, UUID> {

    /** All keys one user owns - the single query the execute/copilot path makes. */
    List<UserLlmCredentialEntity> findByUserId(UUID userId);

    Optional<UserLlmCredentialEntity> findByUserIdAndProvider(UUID userId, String provider);

    void deleteByUserIdAndProvider(UUID userId, String provider);
}