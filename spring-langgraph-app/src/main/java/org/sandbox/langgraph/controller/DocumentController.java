package org.sandbox.langgraph.controller;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.sandbox.langgraph.core.model.DocumentEntity;
import org.sandbox.langgraph.security.CurrentUser;
import org.sandbox.langgraph.service.document.DocumentRejectedException;
import org.sandbox.langgraph.service.document.DocumentService;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * The browser's document upload.
 *
 * <p>Deliberately REST/multipart rather than a GraphQL mutation: the file is binary, GraphQL
 * carries JSON, and a base64-in-JSON round trip would inflate every upload by a third and hit
 * the GraphQL message limit for no benefit. The transport differs from the B2B gRPC facade;
 * the RULES do not - both funnel into {@link DocumentService}, so an oversize or wrong-type
 * file is refused identically whichever door it comes through.</p>
 *
 * <p>The identity is the HTTP session, exactly like the rest of the UI's mutations, so
 * {@code owner_kind=ui} and the owner is the signed-in user's id.</p>
 */
@RestController
@RequiredArgsConstructor
@Slf4j
public class DocumentController {

    /** Echoed in the response so the caller can render a filename it recognises. */
    private static final MediaType APPLICATION_OCTET_STREAM =
            MediaType.parseMediaType("application/octet-stream");

    private final DocumentService documentService;
    private final CurrentUser currentUser;

    /**
     * Store one attachment and return the reference the UI then quotes in a run.
     *
     * <p>The response is deliberately small: the id, the name, the size and the digest. The
     * bytes never come back - the UI has them already, and the point of the reference is that
     * the file is uploaded once and then named.</p>
     */
    @PreAuthorize("!isAnonymous()")
    @PostMapping(path = "/api/documents", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<Map<String, Object>> upload(
            @RequestParam("file") MultipartFile file,
            @RequestParam(value = "threadId", required = false) String threadId) {

        String userId = currentUser.requireId().toString();

        byte[] content;
        try {
            content = file.getBytes();
        } catch (IOException e) {
            log.warn("Could not read the uploaded document", e);
            return ResponseEntity.badRequest().body(Map.of(
                    "success", false,
                    "errorMessage", "The uploaded file could not be read."));
        }

        try {
            DocumentEntity stored = documentService.store(
                    DocumentService.OWNER_UI, userId, threadId,
                    file.getOriginalFilename(), contentTypeOf(file), content);
            return ResponseEntity.ok(response(stored));
        } catch (DocumentRejectedException e) {
            // A rejected file is the caller's problem, not a fault: report the reason so the
            // UI can show it, and log it so an operator sees which rule is biting.
            log.info("Rejected document upload from user {}: {} ({})",
                    userId, e.reason(), e.getMessage());
            return ResponseEntity.badRequest().body(Map.of(
                    "success", false,
                    "reason", e.reason(),
                    "errorMessage", e.getMessage()));
        }
    }

    /**
     * The declared content type, falling back to octet-stream.
     *
     * <p>Browsers are unreliable here - many send octet-stream for anything they do not
     * recognise - which is exactly why the service treats the type as advisory and re-detects
     * the format from the file's own ZIP parts.</p>
     */
    private static String contentTypeOf(MultipartFile file) {
        String type = file.getContentType();
        return type == null || type.isBlank() ? APPLICATION_OCTET_STREAM.toString() : type;
    }

    private static Map<String, Object> response(DocumentEntity stored) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("success", true);
        body.put("documentId", stored.getDocumentId());
        body.put("filename", stored.getFilename());
        body.put("sizeBytes", stored.getSizeBytes());
        body.put("sha256", stored.getSha256());
        return body;
    }
}
