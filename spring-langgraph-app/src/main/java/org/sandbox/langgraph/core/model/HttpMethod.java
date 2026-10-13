package org.sandbox.langgraph.core.model;

/**
 * HTTP methods supported by the TOOL node and the {@code debugTool} mutation.
 * <p>
 * Exposed as the GraphQL {@code HttpMethod} enum. The constant names match
 * {@link org.springframework.http.HttpMethod}, so the value can be handed straight to
 * the tool executor. The Python engine upper-cases the configured method, so this stays
 * compatible with the {@code string}-typed gRPC field.
 */
public enum HttpMethod {
    GET,
    POST,
    PUT,
    PATCH,
    DELETE,
    HEAD,
    OPTIONS;

    /**
     * Lenient parser for legacy/robust handling of the {@code string}-typed method.
     * Input writes are already constrained by the GraphQL enum, so this is only used
     * when a method arrives as a plain string (e.g. persisted metadata).
     *
     * @param value raw string, case-insensitive
     * @return matching {@link HttpMethod}
     * @throws IllegalArgumentException if the value is not a supported method
     */
    public static HttpMethod fromString(String value) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("HTTP method must not be null or blank");
        }
        String normalized = value.trim().toUpperCase();
        return switch (normalized) {
            case "GET" -> GET;
            case "POST" -> POST;
            case "PUT" -> PUT;
            case "PATCH" -> PATCH;
            case "DELETE" -> DELETE;
            case "HEAD" -> HEAD;
            case "OPTIONS" -> OPTIONS;
            default -> throw new IllegalArgumentException("Unsupported HTTP method: " + value);
        };
    }
}
