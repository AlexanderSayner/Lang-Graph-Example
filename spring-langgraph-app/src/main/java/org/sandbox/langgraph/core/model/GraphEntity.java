package org.sandbox.langgraph.core.model;

import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.LastModifiedDate;
import org.springframework.data.annotation.Version;

import java.time.LocalDateTime;

@Entity
@Table(schema = "core", name = "graphs")
@Data
@NoArgsConstructor
@AllArgsConstructor
public class GraphEntity {
    @Id
    private String graphId;
    private String name;
    @Enumerated(EnumType.STRING)
    private GraphStatus status;
    @JdbcTypeCode(SqlTypes.JSON)
    private String definition;
    @JdbcTypeCode(SqlTypes.JSON)
    private String coordinates;
    @CreatedDate
    private LocalDateTime createdAt;
    @LastModifiedDate
    private LocalDateTime updatedAt;
    @Version
    private Long version;

}
