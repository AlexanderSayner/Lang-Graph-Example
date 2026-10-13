package org.sandbox.langgraph.core.model;

/**
 * Event types emitted while a graph executes.
 * <p>
 * Exposed as the GraphQL {@code ExecutionEventType} enum for the subscription /
 * execution contract. The names match the {@code event_type} strings the Python engine
 * emits (and the values the front end already switches on).
 */
public enum ExecutionEventType {
    START,
    NODE_START,
    NODE_END,
    END,
    WAITING_FOR_INPUT,
    ERROR;

    /**
     * Lenient parser for the {@code string}-typed gRPC {@code event_type} field.
     * These values originate from our own Python engine and are always one of the
     * constants above, so an unknown value indicates a genuine contract violation.
     *
     * @param value raw string, case-insensitive
     * @return matching {@link ExecutionEventType}
     * @throws IllegalArgumentException if the value is not a known event type
     */
    public static ExecutionEventType fromString(String value) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("Event type must not be null or blank");
        }
        String normalized = value.trim().toUpperCase();
        return switch (normalized) {
            case "START" -> START;
            case "NODE_START" -> NODE_START;
            case "NODE_END" -> NODE_END;
            case "END" -> END;
            case "WAITING_FOR_INPUT" -> WAITING_FOR_INPUT;
            case "ERROR" -> ERROR;
            default -> throw new IllegalArgumentException("Unknown execution event type: " + value);
        };
    }
}
