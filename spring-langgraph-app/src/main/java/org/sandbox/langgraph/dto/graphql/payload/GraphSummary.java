package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude;
import org.sandbox.langgraph.core.model.GraphStatus;

/**
 * DTO for graph summary (matches existing GraphQL type)
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record GraphSummary(
        String graphId,
        String graphName,
        int nodeCount,
        String createdAt,
        GraphStatus status
) {}
