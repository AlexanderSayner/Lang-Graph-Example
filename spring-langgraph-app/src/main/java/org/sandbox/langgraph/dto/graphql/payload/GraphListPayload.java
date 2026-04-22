package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude;
import org.sandbox.langgraph.dto.graphql.payload.meta.PageInfo;

import java.util.List;

/**
 * Replaces Map<String, Object> return types and avoids collision with
 * org.sandbox.langgraph.grpc.ListGraphsResponse
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record GraphListPayload(
        List<GraphSummary> graphs,
        PageInfo pageInfo,
        int totalCount
) {
}
