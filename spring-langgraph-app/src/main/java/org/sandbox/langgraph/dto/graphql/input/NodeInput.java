package org.sandbox.langgraph.dto.graphql.input;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import org.sandbox.langgraph.core.model.NodeType;

import java.util.Map;

public record NodeInput(
        @NotBlank(message = "Node ID is required")
        @Size(max = 100)
        String nodeId,

        @NotNull(message = "Node type is required")
        NodeType nodeType,

        @NotBlank(message = "Handler name is required")
        String handlerName,

        Map<String, Object> metadata
) {}
