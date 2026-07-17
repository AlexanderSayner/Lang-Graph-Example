package org.sandbox.langgraph.core.repository;

import org.sandbox.langgraph.core.model.UserThreadEntity;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.PagingAndSortingRepository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface UserThreadRepository extends CrudRepository<UserThreadEntity, UUID>, PagingAndSortingRepository<UserThreadEntity, UUID> {
    List<UserThreadEntity> findByUserIdOrderByLastActiveDesc(UUID userId);

    List<UserThreadEntity> findByUserId(UUID userId);

    Optional<UserThreadEntity> findByUserIdAndThreadId(UUID userId, String threadId);

    long deleteByUserIdAndThreadId(UUID userId, String threadId);
}
