package org.sandbox.langgraph.dto.graphql.payload;

public record CopilotGraphPayload(
        Boolean success,
        String aiResponse,
        Integer totalTokens,
        String errorMessage,
        /**
         * "user" when the answer was paid for with the asker's personal key, "service" with
         * the shared key. Shown in the UI so a subsidised answer is never silent.
         */
        String llmBilling
) {
}
