package org.sandbox.langgraph.dto.graphql.input;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.sandbox.langgraph.core.model.HttpMethod;

public record ToolDebugInput(
        @NotNull(message = "HTTP method is required")
        HttpMethod method,
        @NotBlank(message = "URL is required")
        String url,
        String headers,    // JSON String
        String body,
        String variables   // JSON String
) {
}
