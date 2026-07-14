package org.sandbox.langgraph.core.model;

import io.r2dbc.postgresql.codec.Json;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.Id;
import org.springframework.data.annotation.LastModifiedDate;
import org.springframework.data.annotation.Version;
import org.springframework.data.relational.core.mapping.Table;

import java.time.LocalDateTime;

@Table(schema = "core", name = "graphs")
public record GraphEntity(
        @Id String graphId,
        String name,
        String status,
        Json definition,
        Json coordinates,
        @CreatedDate
        LocalDateTime createdAt,
        @LastModifiedDate
        LocalDateTime updatedAt,
        @Version
        Long version
) {
}
