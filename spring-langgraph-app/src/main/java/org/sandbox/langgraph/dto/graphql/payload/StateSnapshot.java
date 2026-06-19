package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude;
import org.sandbox.langgraph.util.JsonDiffUtil;

import java.util.Map;

@JsonInclude(JsonInclude.Include.NON_NULL)
public record StateSnapshot(
        String nodeId,
        Map<String, Object> stateJson,
        String timestamp,
        JsonDiffUtil.StateDiff diff,
        Integer tokensUsed,
        Integer totalTokens
) {
}
