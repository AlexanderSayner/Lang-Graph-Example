package org.sandbox.langgraph.core.model;

/**
 * Canonical node types for a LangGraph definition.
 * <p>
 * Exposed as the GraphQL {@code NodeType} enum so the contract between the Spring
 * gateway, the Vite front end and any external client is strict: an unknown value is
 * rejected at the GraphQL edge instead of being forwarded to the Python engine where it
 * would fail opaquely.
 * <p>
 * The constant names intentionally match the strings the Python engine and the graph
 * builder palette already use, so serialization to/from the {@code string}-typed gRPC
 * field is a plain {@link #name()}.
 */
public enum NodeType {
    START,
    END,
    ACTION,
    HUMAN,
    GRAPH,
    HTTP_REQUEST;

    /**
     * Lenient parser used on the <b>read</b> path (persisted graph definitions coming
     * back from Redis/Postgres, which may contain legacy casing or old README-style
     * names). Input writes never use this - the GraphQL enum already guarantees a
     * valid value.
     *
     * @param value raw string, case-insensitive
     * @return matching {@link NodeType}
     * @throws IllegalArgumentException if the value is not a known type
     */
    public static NodeType fromString(String value) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("Node type must not be null or blank");
        }
        String normalized = value.trim().toUpperCase();
        return switch (normalized) {
            case "START" -> START;
            case "END" -> END;
            case "ACTION", "AGENT", "LLM", "CHAT" -> ACTION;
            case "HUMAN", "HUMAN_INPUT", "INTERRUPT" -> HUMAN;
            case "GRAPH", "SUBGRAPH" -> GRAPH;
            case "HTTP_REQUEST", "HTTP" -> HTTP_REQUEST;
            default -> throw new IllegalArgumentException("Unknown node type: " + value);
        };
    }
}
