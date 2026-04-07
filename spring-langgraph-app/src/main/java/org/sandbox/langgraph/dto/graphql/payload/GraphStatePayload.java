package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.List;
import java.util.Map; /**
 * Avoids collision with org.sandbox.langgraph.grpc.GetGraphStateResponse
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record GraphStatePayload(
        boolean success,
        Map<String, Object> state,
        String currentNode,
        List<String> nodeHistory
) {}
