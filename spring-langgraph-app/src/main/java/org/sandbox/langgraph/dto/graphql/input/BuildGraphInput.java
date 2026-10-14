package org.sandbox.langgraph.dto.graphql.input;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.util.List;
import java.util.Map;

public record BuildGraphInput(
        @NotBlank(message = "Graph ID is required")
        @Size(max = 100)
        String graphId,

        @NotBlank(message = "Graph name is required")
        @Size(max = 200)
        String graphName,

        @Valid
        @Size(min = 1, message = "At least one node is required")
        List<NodeInput> nodes,

        @Valid
        List<EdgeInput> edges,

        Map<String, Object> config,

        /**
         * UI node coordinates for this build. Persisted into the {@code coordinates} JSONB column,
         * so saving a graph from the builder writes layout and definition in one call.
         * Optional: {@code null}/empty keeps the current behaviour (empty layout).
         * Never forwarded to the Python engine over gRPC - the proto contract has no positions.
         */
        @Valid
        List<NodePositionInput> positions
) {
}
