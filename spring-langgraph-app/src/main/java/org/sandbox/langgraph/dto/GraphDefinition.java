package org.sandbox.langgraph.dto;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Getter;
import lombok.Setter;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Jackson-friendly representation of a graph definition.
 * <p>
 * Mirrors the structure defined in the gRPC contract ({@code langgraph.proto})
 * but uses Java-native types suitable for JSON deserialization via Jackson.
 * Protobuf-generated classes cannot be directly deserialized by Jackson
 * without a custom module or {@code protobuf-java-util}, so this DTO
 * acts as the canonical JSON-facing contract.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record GraphDefinition(
        String name,
        String status,
        List<NodeDefinition> nodes,
        List<EdgeDefinition> edges) {

    /**
     * Compact constructor that guarantees non-null lists,
     * preventing NPEs downstream when the JSON omits nodes/edges.
     */
    public GraphDefinition {
        nodes = nodes != null ? nodes : List.of();
        edges = edges != null ? edges : List.of();
    }

    // ------------------------------------------------------------------
    // NodeDefinition — mirrors gRPC NodeDefinition message
    // ------------------------------------------------------------------

    /**
     * Metadata is typed as {@code Map<String, Object>} rather than the gRPC
     * {@code map<string, string>} to preserve the original behavior where
     * Redis may contain nested/non-string values in the metadata field.
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record NodeDefinition(
            @JsonAlias({"nodeId", "node_id"})
            @JsonProperty("nodeId")
            String nodeId,
            @JsonAlias({"nodeType", "node_type"})
            @JsonProperty("nodeType")
            String nodeType,
            @JsonAlias({"handlerName", "handler_name"})
            @JsonProperty("handlerName")
            String handlerName,
            Map<String, Object> metadata) {
    }

    // ------------------------------------------------------------------
    // EdgeDefinition — mirrors gRPC EdgeDefinition message
    // ------------------------------------------------------------------

    /**
     * Implemented as a mutable class (rather than a record) so that
     * {@link JsonAnySetter} can capture any extra fields present in the
     * Redis JSON. This preserves the original behavior where edges were
     * passed through as raw {@code List<Map<String, Object>>} without
     * losing unknown fields.
     */
    @Getter
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static final class EdgeDefinition {

        @Setter
        private String source;
        @Setter
        private String target;
        @Setter
        private String condition;
        private final Map<String, Object> additionalProperties = new LinkedHashMap<>();

        public EdgeDefinition() {
        }

        @JsonAnySetter
        public void setAdditionalProperty(String key, Object value) {
            additionalProperties.put(key, value);
        }

        /**
         * Flattens this edge into a single map — known fields first,
         * then any additional properties — matching the original
         * pass-through behavior of the un-refactored service.
         */
        public Map<String, Object> toMap() {
            Map<String, Object> map = new LinkedHashMap<>();
            if (source != null) {
                map.put("source", source);
            }
            if (target != null) {
                map.put("target", target);
            }
            if (condition != null) {
                map.put("condition", condition);
            }
            map.putAll(additionalProperties);
            return map;
        }

        @Override
        public boolean equals(Object o) {
            if (this == o) {
                return true;
            }
            if (!(o instanceof EdgeDefinition that)) {
                return false;
            }
            return Objects.equals(source, that.source)
                    && Objects.equals(target, that.target)
                    && Objects.equals(condition, that.condition)
                    && Objects.equals(additionalProperties, that.additionalProperties);
        }

        @Override
        public int hashCode() {
            return Objects.hash(source, target, condition, additionalProperties);
        }

        @Override
        public String toString() {
            return "EdgeDefinition{source='%s', target='%s', condition='%s', extra=%s}"
                    .formatted(source, target, condition, additionalProperties);
        }
    }
}
