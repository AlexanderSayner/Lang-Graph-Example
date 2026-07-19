package org.sandbox.langgraph.core.repository;

import org.sandbox.langgraph.core.model.UserEntity;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.PagingAndSortingRepository;

import java.util.Optional;
import java.util.UUID;

public interface UserRepository extends CrudRepository<UserEntity, UUID>, PagingAndSortingRepository<UserEntity, UUID> {
    Optional<UserEntity> findByUsername(String username);
}
