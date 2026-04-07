package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude; /**
 * Avoids collision with org.sandbox.langgraph.grpc.BuildGraphResponse
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record BuildGraphPayload(
        boolean success,
        String graphId,
        String message
) {}
