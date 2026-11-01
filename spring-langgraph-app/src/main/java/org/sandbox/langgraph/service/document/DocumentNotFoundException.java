package org.sandbox.langgraph.service.document;

/**
 * Signals a document reference that cannot be resolved: never uploaded, already swept past
 * its retention deadline, or belonging to a different owner than the caller.
 *
 * <p>Deliberately a plain exception rather than a gRPC status: this service is called from
 * two facades with different error contracts (the B2B gRPC one and the browser's REST one),
 * so it names the CONDITION and lets each facade word it. Neither facade should ever leak
 * whether a foreign owner's id exists.</p>
 */
public class DocumentNotFoundException extends RuntimeException {

    private final String documentId;

    public DocumentNotFoundException(String documentId) {
        super("Document not found: " + documentId);
        this.documentId = documentId;
    }

    /** The id that failed to resolve - useful in a log line, never shown to another owner. */
    public String documentId() {
        return documentId;
    }
}
