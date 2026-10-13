package org.sandbox.langgraph.core.model;

public enum GraphStatus {
    ACTIVE,
    INACTIVE,
    ERROR,
    BUILDING;

    /**
     * Lenient parser for the {@code string}-typed gRPC {@code status} field.
     * <p>
     * The GraphQL {@code GraphInfo.status} field is a non-null {@code GraphStatus!}
     * enum, but the backend may emit the defensive {@code "UNKNOWN"} sentinel when the
     * persisted status could not be read. Rather than 500 the listing, unknown/blank
     * values fall back to {@link #ACTIVE} - the status the persistence layer assigns on
     * save, and therefore the least surprising "known" state.
     *
     * @param value raw string, case-insensitive
     * @return matching {@link GraphStatus}, or {@link #ACTIVE} when unrecognized
     */
    public static GraphStatus fromString(String value) {
        if (value == null || value.isBlank()) {
            return ACTIVE;
        }
        String normalized = value.trim().toUpperCase();
        return switch (normalized) {
            case "ACTIVE" -> ACTIVE;
            case "INACTIVE" -> INACTIVE;
            case "ERROR" -> ERROR;
            case "BUILDING" -> BUILDING;
            default -> ACTIVE;
        };
    }
}

