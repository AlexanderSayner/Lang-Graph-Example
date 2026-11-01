package org.sandbox.langgraph.service.document;

/**
 * Signals a document the store refuses to accept: over the size cap, of an unrecognised
 * format, or empty.
 *
 * <p>The check is deliberately in the SERVICE and not in either facade, so the gRPC upload
 * and the browser's multipart upload enforce exactly the same rules.</p>
 *
 * <p>It extends {@link IllegalArgumentException} on purpose: an unusable file is a problem
 * with the REQUEST, so the B2B facade's {@code GrpcErrors.toStatus} already maps it to
 * {@code INVALID_ARGUMENT} with this message, and the REST facade maps it to a 400. A
 * dedicated status here would make both facades re-derive that same conclusion.</p>
 */
public class DocumentRejectedException extends IllegalArgumentException {

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
