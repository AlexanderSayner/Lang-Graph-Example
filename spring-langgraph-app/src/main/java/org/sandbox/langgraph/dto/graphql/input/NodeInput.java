package org.sandbox.langgraph.dto.graphql.input;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.util.Map;

public record NodeInput(
        @NotBlank(message = "Node ID is required")
        @Size(max = 100)
        String nodeId,

        @NotBlank(message = "Node type is required")
        String nodeType,

        @NotBlank(message = "Handler name is required")
        String handlerName,

        Map<String, Object> metadata
) {}
