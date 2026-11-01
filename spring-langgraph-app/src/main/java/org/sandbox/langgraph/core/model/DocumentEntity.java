package org.sandbox.langgraph.core.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.Data;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

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

    // No @Lob here, and that is load-bearing. On PostgreSQL the dialect registers
    // Types.BLOB -> BlobJdbcType.BLOB_BINDING (it maps BLOB to `oid`, a large object), and that
    // binder calls PreparedStatement.setBlob: pgjdbc answers by creating a server-side large
    // object and binding its OID with setLong, so the parameter is declared `bigint` and
    // PostgreSQL refuses it against this `bytea` column:
    //     ERROR: column "content" is of type bytea but expression is of type bigint
    // `ddl-auto=validate` cannot catch that: the columnDefinition below fixes the DDL type, so
    // the mismatch exists only in the bind and surfaces at the first INSERT.
    //
    // byte[] without @Lob resolves to SqlTypes.VARBINARY, which the PostgreSQL dialect maps to
    // `bytea` and binds with setBytes - exactly what the migration declares. The explicit
    // @JdbcTypeCode keeps a later @Lob from silently reintroducing the oid path; the pinning is
    // asserted by DocumentEntityMappingTest.
    @JdbcTypeCode(SqlTypes.VARBINARY)
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
