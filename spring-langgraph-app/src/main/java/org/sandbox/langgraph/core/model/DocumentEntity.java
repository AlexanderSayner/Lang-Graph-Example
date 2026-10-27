package org.sandbox.langgraph.core.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.Table;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.Instant;

/**
 * One uploaded .xlsx/.docx that a caller handed to a run.
 *
 * <p>A document belongs to a single RUN, not to a graph definition: graphs are persisted and
 * shared, so the reference a node reads ({@code context.document_ref}) has to be resolvable
 * for exactly one owner. That is what {@link #ownerKind} + {@link #ownerId} encode - a
 * "b2b" owner is a tenant, a "ui" owner is a session user - and it is the property the
 * fetch path enforces.</p>
 *
 * <p>The bytes are deliberately NOT encrypted here. Unlike an LLM key, a document is
 * business data the operator has explicitly agreed to store, and encrypting it would force
 * the engine's fetch path to hold the master key too. The migration documents that trade.</p>
 */
@Entity
@Table(schema = "app", name = "documents")
@Data
@NoArgsConstructor
public class DocumentEntity {

    /** Opaque id, scoped by owner so a reference is unguessable and owner-bound. */
    @Id
    @Column(name = "document_id", length = 64)
    private String documentId;

    /** "b2b" (tenant-scoped) or "ui" (session-user-scoped). */
    @Column(name = "owner_kind", length = 16, nullable = false)
    private String ownerKind;

    @Column(name = "owner_id", length = 128, nullable = false)
    private String ownerId;

    /** The thread the file was attached to - audit only, never used for lookup. */
    @Column(name = "thread_id", length = 255)
    private String threadId;

    /** Browser-supplied name, already reduced to its basename by the service. */
    @Column(name = "filename", length = 255, nullable = false)
    private String filename;

    /** Informational: the engine re-detects the format from the ZIP's own parts. */
    @Column(name = "mime_type", length = 255, nullable = false)
    private String mimeType;

    @Column(name = "size_bytes", nullable = false)
    private long sizeBytes;

    /** Hex SHA-256 of {@link #content}; audit and identity, never a lookup key. */
    @Column(name = "sha256", length = 64, nullable = false)
    private String sha256;

    // columnDefinition is load-bearing: `spring.jpa.hibernate.ddl-auto=validate` runs against
    // the real column type at boot, and a plain byte[] would be validated as blob rather than
    // the bytea the migration declares.
    @Lob
    @Column(name = "content", columnDefinition = "bytea", nullable = false)
    private byte[] content;

    @Column(name = "created_at")
    private Instant createdAt;

    /** Retention deadline; null means "kept until an operator decides otherwise". */
    @Column(name = "expires_at")
    private Instant expiresAt;

    /** True when the retention deadline has passed - the sweeper's predicate. */
    public boolean isExpired() {
        return expiresAt != null && !expiresAt.isAfter(Instant.now());
    }
}
