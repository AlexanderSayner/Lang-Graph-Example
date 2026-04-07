package com.example.langgraph.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.Map;

/**
 * DTO for building a graph.
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class BuildGraphRequest {
    private String graphId;
    private String graphName;
    private List<NodeDefinition> nodes;
    private List<EdgeDefinition> edges;
    private Map<String, String> config;

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class NodeDefinition {
        private String nodeId;
        private String nodeType;
        private String handlerName;
        private Map<String, String> metadata;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class EdgeDefinition {
        private String source;
        private String target;
        private String condition;
    }
}
