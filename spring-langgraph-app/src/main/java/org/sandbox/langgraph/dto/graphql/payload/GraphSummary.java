package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude;

@JsonInclude(JsonInclude.Include.NON_NULL)
public record GraphSummary(
        String graphId,
        String graphName,
        int nodeCount,
        String createdAt,
        String status
) {}
