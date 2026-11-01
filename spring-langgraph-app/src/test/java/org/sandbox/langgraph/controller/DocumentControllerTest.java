package org.sandbox.langgraph.controller;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.core.model.DocumentEntity;
import org.sandbox.langgraph.security.CurrentUser;
import org.sandbox.langgraph.service.document.DocumentRejectedException;
import org.sandbox.langgraph.service.document.DocumentService;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * The browser's upload endpoint.
 *
 * <p>What matters here is that it is the SAME gate the B2B facade uses: the request is
 * validated by {@link DocumentService}, so a wrong-type or oversize file is refused with a
 * reason the UI can display, and the identity is the signed-in user - never the file name or
 * any header the caller controls.</p>
 */
class DocumentControllerTest {

    /** Stand-in for a real .xlsx: every ZIP (and therefore every OOXML file) starts with PK. */
    private static final byte[] XLSX_BYTES = {'P', 'K', 3, 4, 0, 0, 0, 0};
    private static final UUID USER = UUID.fromString("22222222-2222-2222-2222-222222222222");

    private final DocumentService documentService = mock(DocumentService.class);
    private final DocumentController controller =
            new DocumentController(documentService, new CurrentUser());

    /** Sign the test in as a real user id, which is what requireId() parses. */
    private static void signIn() {
        // The THREE-argument constructor is the one that marks the token authenticated; the
        // two-argument form deliberately does not, and CurrentUser.isUsable() would refuse it.
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken(
                        USER.toString(), "n/a", java.util.List.of()));
    }

    @AfterEach
    void clearContext() {
        SecurityContextHolder.clearContext();
    }

    private static MockMultipartFile file(String name, String contentType, byte[] content) {
        return new MockMultipartFile("file", name, contentType, content);
    }

    private static DocumentEntity stored(String filename) {
        DocumentEntity entity = new DocumentEntity();
        entity.setDocumentId("ui-1");
        entity.setOwnerKind(DocumentService.OWNER_UI);
        entity.setOwnerId(USER.toString());
        entity.setFilename(filename);
        entity.setMimeType("application/octet-stream");
        entity.setSizeBytes(XLSX_BYTES.length);
        entity.setSha256("abc123");
        entity.setContent(XLSX_BYTES);
        entity.setExpiresAt(Instant.now().plusSeconds(3600));
        return entity;
    }

    @Test
    @DisplayName("an upload is stored for the signed-in user and answered with a reference")
    void storesForTheSignedInUser() {
        signIn();
        when(documentService.store(anyString(), anyString(), any(), anyString(), anyString(), any()))
                .thenAnswer(invocation -> stored(invocation.getArgument(3)));

        ResponseEntity<Map<String, Object>> response = controller.upload(
                file("q1.xlsx", "application/octet-stream", XLSX_BYTES), "t-1");

        assertThat(response.getStatusCode().is2xxSuccessful()).isTrue();
        assertThat(response.getBody())
                .containsEntry("success", true)
                .containsEntry("documentId", "ui-1")
                .containsEntry("filename", "q1.xlsx")
                .containsEntry("sha256", "abc123");
        // The retention deadline travels too, so the caller can see how long this reference
        // stays usable instead of having to know the 7-day rule - the B2B DocumentRef
        // already returns it, and the two surfaces must not disagree about what a reference is.
        assertThat(response.getBody().get("expiresAt").toString()).isNotBlank();
        // The bytes are never echoed back: the UI already has them, and the point of a
        // reference is that the file travels exactly once.
        assertThat(response.getBody()).doesNotContainKey("content");

        verify(documentService).store(eq(DocumentService.OWNER_UI), eq(USER.toString()),
                eq("t-1"), eq("q1.xlsx"), anyString(), any());
    }

    @Test
    @DisplayName("an octet-stream upload still succeeds - the browser's type is not trusted")
    void treatsTheDeclaredTypeAsAdvisory() {
        signIn();
        when(documentService.store(anyString(), anyString(), any(), anyString(), anyString(), any()))
                .thenAnswer(invocation -> stored("doc.docx"));

        ResponseEntity<Map<String, Object>> response = controller.upload(
                file("doc.docx", "application/octet-stream", XLSX_BYTES), null);

        assertThat(response.getStatusCode().is2xxSuccessful()).isTrue();
        verify(documentService).store(anyString(), anyString(), any(), anyString(),
                eq("application/octet-stream"), any());
    }

    @Test
    @DisplayName("a refused file answers 400 with the reason, so the UI can show it")
    void refusesAnUnusableFile() {
        signIn();
        when(documentService.store(anyString(), anyString(), any(), anyString(), anyString(), any()))
                .thenThrow(new DocumentRejectedException("unsupported_type",
                        "Only .xlsx and .docx documents can be attached."));

        ResponseEntity<Map<String, Object>> response = controller.upload(
                file("shot.png", "image/png", XLSX_BYTES), null);

        assertThat(response.getStatusCode().value()).isEqualTo(400);
        assertThat(response.getBody())
                .containsEntry("success", false)
                .containsEntry("reason", "unsupported_type")
                .containsEntry("errorMessage", "Only .xlsx and .docx documents can be attached.");
    }

    @Test
    @DisplayName("the owner is the session user, never anything the caller chose")
    void ownerComesFromTheSession() {
        signIn();
        when(documentService.store(anyString(), anyString(), any(), anyString(), anyString(), any()))
                .thenAnswer(invocation -> stored("a.xlsx"));

        controller.upload(file("a.xlsx", "application/octet-stream", XLSX_BYTES), null);

        verify(documentService).store(eq(DocumentService.OWNER_UI), eq(USER.toString()),
                any(), anyString(), anyString(), any());
    }

    @Test
    @DisplayName("an upload with no usable session is refused before anything is stored")
    void refusesAnAnonymousUpload() {
        // No security context at all: requireId() throws AccessDeniedException, and nothing
        // reaches the service.
        org.assertj.core.api.Assertions.assertThatThrownBy(
                        () -> controller.upload(file("a.xlsx", "application/octet-stream", XLSX_BYTES), null))
                .isInstanceOf(org.springframework.security.access.AccessDeniedException.class);
        verify(documentService, never()).store(anyString(), anyString(), any(), anyString(), anyString(), any());
    }
}

