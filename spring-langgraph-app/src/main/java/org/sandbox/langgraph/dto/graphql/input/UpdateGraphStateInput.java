package org.sandbox.langgraph.dto.graphql.input;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.util.Map;

public record UpdateGraphStateInput(
        @NotBlank(message = "Graph ID is required")
        String graphId,

        String threadId,

        @NotNull(message = "State updates are required")
        @Size(min = 1, message = "At least one state update is required")
        Map<String, Object> stateUpdates
) {}
