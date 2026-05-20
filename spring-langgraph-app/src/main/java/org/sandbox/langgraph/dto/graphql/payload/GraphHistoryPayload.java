package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.List;

@JsonInclude(JsonInclude.Include.NON_NULL)
public record GraphHistoryPayload(
        Boolean success,
        List<StateSnapshot> history,
        String errorMessage
) {
    public static GraphHistoryPayload error(String message) {
        return new GraphHistoryPayload(false, List.of(), message);
    }
}
