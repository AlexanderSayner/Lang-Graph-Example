package org.sandbox.langgraph.service.document;

/**
 * Signals a document the store refuses to accept: over the size cap, of an unrecognised
 * format, or empty.
 *
 * <p>The check is deliberately in the SERVICE and not in either facade, so the gRPC upload
 * and the browser's multipart upload enforce exactly the same rules. A facade maps this to
 * INVALID_ARGUMENT (gRPC) or a 400 (HTTP).</p>
 */
public class DocumentRejectedException extends RuntimeException {

    /** Stable, loggable reason: {@code too_large}, {@code empty} or {@code unsupported_type}. */
    private final String reason;

    public DocumentRejectedException(String reason, String message) {
        super(message);
        this.reason = reason;
    }

    public String reason() {
        return reason;
    }
}
