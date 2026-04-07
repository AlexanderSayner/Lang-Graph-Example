package org.sandbox.langgraph.dto.graphql.input;

import jakarta.validation.constraints.NotBlank;

public record EdgeInput(
        @NotBlank(message = "Source is required")
        String source,

        @NotBlank(message = "Target is required")
        String target,

        String condition
) {}
