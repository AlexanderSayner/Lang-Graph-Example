package org.sandbox.langgraph.core.model;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(schema = "app", name = "user_threads")
@Data
@NoArgsConstructor
@AllArgsConstructor
public class UserThreadEntity {
    @Id
    private UUID id;
    private UUID userId;
    private String threadId;
    private String graphId;
    private String title;
    private Instant lastActive;

}
