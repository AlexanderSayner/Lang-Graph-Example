package org.sandbox.langgraph.dto.graphql.input;

import jakarta.validation.Valid;
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

        Map<String, Object> metadata,

        /**
         * UI coordinates for this node. Persisted into the {@code coordinates} JSONB column by
         * {@code GraphPostgresService.saveGraph()}, so saving a graph from the builder writes layout
         * and definition in one call. Optional: {@code null} keeps the current behaviour (empty layout
         * for that node). Never forwarded to the Python engine over gRPC - the proto contract has no positions.
         */
        @Valid
        PositionInput position
) {}
