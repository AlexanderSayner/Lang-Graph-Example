package org.sandbox.langgraph.dto.graphql.input;

import jakarta.validation.constraints.NotBlank;

import java.util.Map;

public record ExecuteGraphInput(
        @NotBlank(message = "Graph ID is required")
        String graphId,

        @NotBlank(message = "Input is required")
        String input,

        Map<String, Object> context
) {}
