package org.sandbox.langgraph.dto.graphql.input;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record NodePositionInput(
        @NotBlank(message = "Node ID is required")
        @Size(max = 100)
        String nodeId,

        double x,

        double y
) {
}
