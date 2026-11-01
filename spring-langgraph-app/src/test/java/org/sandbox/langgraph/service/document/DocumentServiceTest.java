package org.sandbox.langgraph.service.document;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.core.model.DocumentEntity;
import org.sandbox.langgraph.core.repository.DocumentRepository;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Documents: accepted only when they are really a spreadsheet or a word document, held under
 * an owner-bound unguessable id, and treated as GONE rather than merely absent once their
 * retention deadline passes - which is what makes the engine's fetch report a readable node
 * error instead of handing an empty body to a model to hallucinate over.
 */
class DocumentServiceTest {

    /** Stand-in for a real .xlsx: every ZIP (and therefore every OOXML file) starts with PK. */
    private static final byte[] VALID_ZIP = {'P', 'K', 3, 4, 0, 0, 0, 0};

    private final DocumentRepository repository = mock(DocumentRepository.class);
    private final DocumentService service = new DocumentService(repository);

    private static DocumentEntity stored(String id, Instant expiresAt) {
        DocumentEntity entity = new DocumentEntity();
        entity.setDocumentId(id);
        entity.setOwnerKind(DocumentService.OWNER_B2B);
        entity.setOwnerId("acme");
        entity.setFilename("report.xlsx");
        entity.setMimeType("application/octet-stream");
        entity.setSizeBytes(VALID_ZIP.length);
        entity.setSha256("deadbeef");
        entity.setContent(VALID_ZIP);
        entity.setExpiresAt(expiresAt);
        return entity;
    }

    private DocumentEntity storeAny() {
        when(repository.save(any())).thenAnswer(invocation -> invocation.getArgument(0));
        return service.store(DocumentService.OWNER_B2B, "acme", "t-1", "Q1 report.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", VALID_ZIP);
    }

    // --- Acceptance -------------------------------------------------------------------

    @Test
    @DisplayName("a valid spreadsheet is stored with an owner-bound id and a digest")
    void storesAValidDocument() {
        DocumentEntity saved = storeAny();

        assertThat(saved.getDocumentId()).startsWith("b2b-");
        assertThat(saved.getOwnerKind()).isEqualTo(DocumentService.OWNER_B2B);
        assertThat(saved.getOwnerId()).isEqualTo("acme");
        assertThat(saved.getFilename()).isEqualTo("Q1 report.xlsx");
        assertThat(saved.getSizeBytes()).isEqualTo(VALID_ZIP.length);
        assertThat(saved.getSha256()).hasSize(64);
        assertThat(saved.getExpiresAt()).isAfter(Instant.now());
        verify(repository).save(any());
    }

    @Test
    @DisplayName("a browser that sends octet-stream still uploads - the engine re-detects")
    void acceptsOctetStream() {
        when(repository.save(any())).thenAnswer(invocation -> invocation.getArgument(0));

        DocumentEntity saved = service.store(DocumentService.OWNER_UI, "user-1", null,
                "doc.docx", "application/octet-stream", VALID_ZIP);

        assertThat(saved.getOwnerKind()).isEqualTo(DocumentService.OWNER_UI);
        assertThat(saved.getThreadId()).isNull();
    }

    @Test
    @DisplayName("a path traversal attempt in the filename cannot survive as a path")
    void stripsDirectoryComponentsFromTheFilename() {
        when(repository.save(any())).thenAnswer(invocation -> invocation.getArgument(0));

        DocumentEntity saved = service.store(DocumentService.OWNER_UI, "user-1", null,
                "../../etc/passwd", "application/octet-stream", VALID_ZIP);

        assertThat(saved.getFilename()).isEqualTo("passwd");
    }

    // --- Rejection --------------------------------------------------------------------

    @Test
    @DisplayName("an empty upload is refused")
    void rejectsEmptyContent() {
        assertThatThrownBy(() -> service.store(DocumentService.OWNER_B2B, "acme", "t", "a.xlsx",
                "application/octet-stream", new byte[0]))
                .isInstanceOf(DocumentRejectedException.class)
                .extracting(error -> ((DocumentRejectedException) error).reason())
                .isEqualTo("empty");
    }

    @Test
    @DisplayName("a file over the size cap is refused rather than stored and truncated")
    void rejectsOversizedContent() {
        byte[] huge = new byte[9 * 1024 * 1024];
        huge[0] = 'P';
        huge[1] = 'K';

        assertThatThrownBy(() -> service.store(DocumentService.OWNER_B2B, "acme", "t", "big.xlsx",
                "application/octet-stream", huge))
                .isInstanceOf(DocumentRejectedException.class)
                .hasMessageContaining("MB limit");
    }

    @Test
    @DisplayName("a PNG is refused - it is not one of the two supported containers")
    void rejectsUnsupportedMimeType() {
        assertThatThrownBy(() -> service.store(DocumentService.OWNER_B2B, "acme", "t", "shot.png",
                "image/png", new byte[]{'P', 'K', 3, 4, 0}))
                .isInstanceOf(DocumentRejectedException.class)
                .hasMessageContaining(".xlsx and .docx");
    }

    @Test
    @DisplayName("bytes that are not even a ZIP are refused with a helpful message")
    void rejectsNonZipBytes() {
        assertThatThrownBy(() -> service.store(DocumentService.OWNER_B2B, "acme", "t", "a.xlsx",
                "application/octet-stream", "hello, i am plain text".getBytes()))
                .isInstanceOf(DocumentRejectedException.class)
                .hasMessageContaining("not a valid .xlsx or .docx");
    }

    @Test
    @DisplayName("an unknown owner kind or a blank owner id is a programming error, not a rejection")
    void rejectsBadOwner() {
        assertThatThrownBy(() -> service.store("nobody", "acme", "t", "a.xlsx",
                "application/octet-stream", VALID_ZIP))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.store(DocumentService.OWNER_B2B, "  ", "t", "a.xlsx",
                "application/octet-stream", VALID_ZIP))
                .isInstanceOf(IllegalArgumentException.class);
    }

    // --- Retrieval --------------------------------------------------------------------

    @Test
    @DisplayName("a live document resolves")
    void fetchesALiveDocument() {
        when(repository.findById("b2b-x")).thenReturn(
                Optional.of(stored("b2b-x", Instant.now().plusSeconds(3600))));

        assertThat(service.fetch("b2b-x").getDocumentId()).isEqualTo("b2b-x");
    }

    @Test
    @DisplayName("an unknown id and an expired row both read as NOT FOUND")
    void treatsExpiredAsMissing() {
        when(repository.findById("gone")).thenReturn(
                Optional.of(stored("gone", Instant.now().minusSeconds(60))));
        when(repository.findById("never")).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.fetch("gone"))
                .isInstanceOf(DocumentNotFoundException.class);
        assertThatThrownBy(() -> service.fetch("never"))
                .isInstanceOf(DocumentNotFoundException.class);
        assertThatThrownBy(() -> service.fetch("  "))
                .isInstanceOf(DocumentNotFoundException.class);
    }

    @Test
    @DisplayName("the sweeper deletes exactly the rows past their deadline")
    void purgesExpiredRows() {
        when(repository.findByExpiresAtBefore(any())).thenReturn(
                List.of(stored("a", Instant.now().minusSeconds(1))));

        assertThat(service.purgeExpired()).isEqualTo(1);
        verify(repository).deleteAll(any());
    }

    @Test
    @DisplayName("a clean sweep is a no-op, not a delete of everything")
    void purgeWithNothingToDo() {
        when(repository.findByExpiresAtBefore(any())).thenReturn(List.of());

        assertThat(service.purgeExpired()).isZero();
    }

    // --- The shape of a handle -----------------------------------------------------------
    //
    // `looksLikeDocumentId` is what both facades use to tell a document reference from an
    // ordinary context value: a graph that passes its own keys (a tenant, a date, a flag) must
    // never be sent to this store, and a reference hiding under a slot key of its own must
    // still be gated. It has to agree with `newId`, so the first assertion mints a real one.

    @Test
    @DisplayName("an id this service minted is recognised - both owner kinds")
    void anIdTheServiceMintedIsRecognised() {
        assertThat(DocumentService.looksLikeDocumentId(storeAny().getDocumentId())).isTrue();
        assertThat(storeAny().getDocumentId()).startsWith("b2b-");

        when(repository.save(any())).thenAnswer(invocation -> invocation.getArgument(0));
        DocumentEntity fromUi = service.store(DocumentService.OWNER_UI, "user-1", "t-1",
                "doc.docx", "application/octet-stream", VALID_ZIP);

        assertThat(DocumentService.looksLikeDocumentId(fromUi.getDocumentId())).isTrue();
    }

    @Test
    @DisplayName("an ordinary context value is never mistaken for a handle")
    void anOrdinaryValueIsNotAHandle() {
        assertThat(DocumentService.looksLikeDocumentId("acme")).isFalse();
        assertThat(DocumentService.looksLikeDocumentId("2026-Q1")).isFalse();
        assertThat(DocumentService.looksLikeDocumentId("ui-123")).isFalse();   // too short
        assertThat(DocumentService.looksLikeDocumentId(null)).isFalse();
        assertThat(DocumentService.looksLikeDocumentId("   ")).isFalse();
    }

    @Test
    @DisplayName("a handle is a handle only when every part is well formed")
    void everyPartMustBeWellFormed() {
        String suffix = "a".repeat(32);

        assertThat(DocumentService.looksLikeDocumentId("ui-1-" + suffix)).isTrue();
        assertThat(DocumentService.looksLikeDocumentId("  ui-1-" + suffix + "  ")).as("trimmed").isTrue();
        assertThat(DocumentService.looksLikeDocumentId("ui-11111111-" + "a".repeat(31))).isFalse();
        assertThat(DocumentService.looksLikeDocumentId("ui-11111111-" + "a".repeat(33))).isFalse();
        assertThat(DocumentService.looksLikeDocumentId("ui-11111111-" + "A".repeat(32))).as("case").isFalse();
        assertThat(DocumentService.looksLikeDocumentId("ui-11111111-" + "-".repeat(32))).as("charset").isFalse();
        assertThat(DocumentService.looksLikeDocumentId("ui-11111111")).as("no suffix").isFalse();
        assertThat(DocumentService.looksLikeDocumentId("ui-" + suffix)).as("no owner hash").isFalse();
    }

    @Test
    @DisplayName("the owner hash may be up to 8 hex chars - a negative hashCode produces 8")
    void aLongOwnerHashIsStillRecognised() {
        // Integer.toHexString(ownerId.hashCode()) yields 8 characters for any negative hash,
        // so a rule that assumed "hash is always short" would stop gating real references.
        when(repository.save(any())).thenAnswer(invocation -> invocation.getArgument(0));
        DocumentEntity saved = service.store(DocumentService.OWNER_UI,
                "a-user-id-whose-hash-is-negative", "t-1", "doc.docx",
                "application/octet-stream", VALID_ZIP);

        assertThat(saved.getDocumentId().split("-")[1]).hasSize(8);
        assertThat(DocumentService.looksLikeDocumentId(saved.getDocumentId())).isTrue();
    }
}

