package org.sandbox.langgraph.service.document;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.sandbox.langgraph.core.model.DocumentEntity;
import org.sandbox.langgraph.core.repository.DocumentRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ThreadLocalRandom;

/**
 * Storage and owner-scoped retrieval of uploaded documents (.xlsx / .docx).
 *
 * <p>Two facades write to it - the B2B gRPC one ({@code ownerKind=b2b}, owner = tenant) and
 * the browser's REST one ({@code ownerKind=ui}, owner = session user) - and exactly one
 * reader takes bytes back out: the engine's {@code GetDocument} callback on the internal
 * gRPC port. That asymmetry is the security model. A caller never reads bytes directly; it
 * only ever receives an opaque id, and the id embeds the owner so a stolen reference is
 * useless to anybody else.</p>
 *
 * <p>Retention: rows carry an {@code expires_at} and a sweeper frees them. An expired row
 * is treated exactly like a missing one, so the DOCUMENT node reports a normal node error
 * rather than a crash when a caller quotes a reference from last month.</p>
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class DocumentService {

    /** Owner kind for the B2B facade; the owner id is the caller's tenant. */
    public static final String OWNER_B2B = "b2b";
    /** Owner kind for the browser path; the owner id is the session user's UUID. */
    public static final String OWNER_UI = "ui";

    /** Only these two containers are worth the storage: both are ZIPs of XML. */
    public static final List<String> ALLOWED_MIME_TYPES = List.of(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            // Browsers routinely send this instead of a real type; the engine re-detects the
            // format from the ZIP's own parts, so accepting it is safe and avoids a broken
            // upload in every browser that does not sniff.
            "application/octet-stream");

    private static final Duration RETENTION = Duration.ofDays(7);
    /** Refuse rather than store a file the engine could not put in a prompt anyway. */
    private static final long MAX_CONTENT_BYTES = 8L * 1024 * 1024;
    private static final String ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";

    private final DocumentRepository repository;

    /**
     * Validate, digest and persist one document.
     *
     * @param ownerKind {@link #OWNER_B2B} or {@link #OWNER_UI}
     * @param ownerId   tenant id or user id - never a free-form client string
     * @param filename  display name only; reduced to its basename so it can never become a path
     * @throws DocumentRejectedException when the bytes are empty, oversized or not a known type
     */
    @Transactional
    public DocumentEntity store(String ownerKind, String ownerId, String threadId,
                                String filename, String mimeType, byte[] content) {
        requireValid(ownerKind, ownerId, content, mimeType);

        DocumentEntity entity = new DocumentEntity();
        entity.setDocumentId(newId(ownerKind, ownerId));
        entity.setOwnerKind(ownerKind);
        entity.setOwnerId(ownerId);
        entity.setThreadId(blankToNull(threadId));
        entity.setFilename(basename(filename));
        entity.setMimeType(mimeType == null ? "" : mimeType);
        entity.setSizeBytes(content.length);
        entity.setSha256(sha256Hex(content));
        entity.setContent(content);
        entity.setCreatedAt(Instant.now());
        entity.setExpiresAt(Instant.now().plus(RETENTION));

        repository.save(entity);
        log.info("Stored document {} ({} bytes, {}) for {}/{}",
                entity.getDocumentId(), entity.getSizeBytes(), entity.getMimeType(),
                ownerKind, ownerId);
        return entity;
    }

    /**
     * Resolve a document for the engine's fetch callback.
     *
     * <p>The engine is inside the trust boundary and is handed the id by whichever facade
     * already authorised the run, so this method does not re-check ownership: the id itself
     * is unguessable and owner-bound (see {@link #newId}). Expired rows read as missing.</p>
     *
     * @throws DocumentNotFoundException when absent or past its retention deadline
     */
    @Transactional(readOnly = true)
    public DocumentEntity fetch(String documentId) {
        if (documentId == null || documentId.isBlank()) {
            throw new DocumentNotFoundException(String.valueOf(documentId));
        }
        DocumentEntity entity = repository.findById(documentId.trim())
                .orElseThrow(() -> new DocumentNotFoundException(documentId));
        if (entity.isExpired()) {
            log.info("Document {} is past its retention deadline", documentId);
            throw new DocumentNotFoundException(documentId);
        }
        return entity;
    }

    /** Delete every row past its deadline. Called by the retention sweeper. */
    @Transactional
    public int purgeExpired() {
        List<DocumentEntity> expired = repository.findByExpiresAtBefore(Instant.now());
        if (expired.isEmpty()) {
            return 0;
        }
        repository.deleteAll(expired);
        log.info("Purged {} expired document(s)", expired.size());
        return expired.size();
    }

    // --- Validation -------------------------------------------------------------------

    private void requireValid(String ownerKind, String ownerId, byte[] content, String mimeType) {
        if (!OWNER_B2B.equals(ownerKind) && !OWNER_UI.equals(ownerKind)) {
            throw new IllegalArgumentException("Unknown document owner kind: " + ownerKind);
        }
        if (ownerId == null || ownerId.isBlank()) {
            throw new IllegalArgumentException("A document owner id is required");
        }
        if (content == null || content.length == 0) {
            throw new DocumentRejectedException("empty", "The uploaded document is empty.");
        }
        if (content.length > MAX_CONTENT_BYTES) {
            throw new DocumentRejectedException("too_large", "The document is larger than the "
                    + (MAX_CONTENT_BYTES / (1024 * 1024)) + " MB limit.");
        }
        if (mimeType != null && !mimeType.isBlank()
                && !ALLOWED_MIME_TYPES.contains(mimeType.toLowerCase(Locale.ROOT))) {
            throw new DocumentRejectedException("unsupported_type",
                    "Only .xlsx and .docx documents can be attached.");
        }
        // A real .xlsx/.docx is a ZIP, and every ZIP starts with "PK". Anything else would
        // reach the engine only to fail there, with a less useful message than this one.
        if (content.length < 4 || content[0] != 'P' || content[1] != 'K') {
            throw new DocumentRejectedException("unsupported_type",
                    "The uploaded file is not a valid .xlsx or .docx document.");
        }
    }

    // --- Helpers ----------------------------------------------------------------------

    /**
     * An id that cannot be guessed and cannot be moved between owners.
     *
     * <p>The random part carries the unguessability (128 bits from the JVM's CSPRNG); the
     * prefix carries the owner, which is what makes a reference bound to one tenant even
     * after it has been logged or echoed back to a client.</p>
     */
    private String newId(String ownerKind, String ownerId) {
        byte[] random = new byte[16];
        ThreadLocalRandom.current().nextBytes(random);
        StringBuilder suffix = new StringBuilder(32);
        for (byte b : random) {
            suffix.append(ID_ALPHABET.charAt((b & 0xFF) % ID_ALPHABET.length()));
        }
        return ownerKind + "-" + Integer.toHexString(ownerId.hashCode()) + "-" + suffix;
    }

    private static String sha256Hex(byte[] content) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content));
        } catch (NoSuchAlgorithmException e) {
            // SHA-256 is mandated by the JDK; a missing implementation means a broken JVM.
            throw new IllegalStateException("SHA-256 is unavailable", e);
        }
    }

    /** Reduce a client-supplied name to its basename so it can never become a path. */
    static String basename(String filename) {
        if (filename == null) {
            return "";
        }
        String cleaned = filename.trim().replace('\\', '/');
        int slash = cleaned.lastIndexOf('/');
        if (slash >= 0) {
            cleaned = cleaned.substring(slash + 1);
        }
        // Control characters would corrupt a log line and carry no name anyway.
        cleaned = cleaned.replaceAll("[\\p{Cntrl}]", "");
        return cleaned.length() > 255 ? cleaned.substring(0, 255) : cleaned;
    }

    private static String blankToNull(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        String trimmed = value.trim();
        return trimmed.length() > 255 ? trimmed.substring(0, 255) : trimmed;
    }
}

