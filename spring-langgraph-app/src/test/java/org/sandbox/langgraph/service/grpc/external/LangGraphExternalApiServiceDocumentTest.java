package org.sandbox.langgraph.service.grpc.external;

import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.sandbox.langgraph.core.model.DocumentEntity;
import org.sandbox.langgraph.dto.graphql.input.ExecuteGraphInput;
import org.sandbox.langgraph.mapper.ExternalApiMapper;
import org.sandbox.langgraph.security.b2b.B2BClientContext;
import org.sandbox.langgraph.security.b2b.B2BGraphScope;
import org.sandbox.langgraph.security.b2b.B2BThreadScope;
import org.sandbox.langgraph.service.CopilotService;
import org.sandbox.langgraph.service.GraphExecutionAggregator;
import org.sandbox.langgraph.service.LangGraphGrpcService;
import org.sandbox.langgraph.service.document.DocumentNotFoundException;
import org.sandbox.langgraph.service.document.DocumentRejectedException;
import org.sandbox.langgraph.service.document.DocumentService;
import org.sandbox.langgraph.service.llm.RequestOrigin;
import reactor.core.publisher.Flux;

import java.time.Instant;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Attachments on the B2B facade.
 *
 * <p>The behaviour these pin is the SECURITY one, not the plumbing: a document uploaded by
 * one tenant must be unusable by another, and a caller must not be able to tell a foreign id
 * from a missing one. The plumbing (does the engine get the reference?) is asserted once,
 * through the run context, because that is the only channel the reference travels by.</p>
 */
class LangGraphExternalApiServiceDocumentTest {

    private static final byte[] XLSX_BYTES = {'P', 'K', 3, 4, 0, 0, 0, 0};
    private static final String XLSX_MIME =
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

    private final LangGraphGrpcService langGraphService = mock(LangGraphGrpcService.class);
    private final DocumentService documentService = mock(DocumentService.class);
    private final LangGraphExternalApiService service = new LangGraphExternalApiService(
            langGraphService, mock(CopilotService.class), new GraphExecutionAggregator(),
            new ExternalApiMapper(), new B2BThreadScope(), new B2BGraphScope(),
            mock(jakarta.validation.Validator.class), documentService);

    private static final B2BClientContext CLIENT =
            B2BClientContext.of("orchestra", "orchestra", "", List.of("*"));

    private static DocumentEntity stored(String id, String ownerKind, String ownerId) {
        DocumentEntity entity = new DocumentEntity();
        entity.setDocumentId(id);
        entity.setOwnerKind(ownerKind);
        entity.setOwnerId(ownerId);
        entity.setFilename("q1.xlsx");
        entity.setMimeType(XLSX_MIME);
        entity.setSizeBytes(XLSX_BYTES.length);
        entity.setSha256("abc123");
        entity.setContent(XLSX_BYTES);
        entity.setExpiresAt(Instant.now().plusSeconds(3600));
        return entity;
    }

    private void allowRun() {
        when(langGraphService.executeGraphStream(any(), anyBoolean(), any(RequestOrigin.class), any()))
                .thenReturn(Flux.empty());
    }

    private void call(Runnable action) {
        io.grpc.Context.current().withValue(B2BClientContext.KEY, CLIENT).run(action);
    }

    private static StatusRuntimeException errorOf(RecordingObserver<?> observer) {
        assertThat(observer.errors).hasSize(1);
        return observer.errors.get(0);
    }

    // --- Upload -----------------------------------------------------------------------

    @Test
    @DisplayName("an upload is stored under the caller's tenant and answered with a reference")
    void uploadStoresUnderTheCallersTenant() {
        when(documentService.store(anyString(), anyString(), any(), anyString(), anyString(), any()))
                .thenAnswer(invocation -> stored("b2b-1", invocation.getArgument(1), invocation.getArgument(1)));

        var observer = new RecordingObserver<org.sandbox.langgraph.grpc.external.DocumentRef>();
        call(() -> service.uploadDocument(
                org.sandbox.langgraph.grpc.external.UploadDocumentRequest.newBuilder()
                        .setThreadId("t-1").setFilename("q1.xlsx").setMimeType(XLSX_MIME)
                        .setContent(com.google.protobuf.ByteString.copyFrom(XLSX_BYTES))
                        .build(), observer));

        ArgumentCaptor<String> owner = ArgumentCaptor.forClass(String.class);
        verify(documentService).store(anyString(), owner.capture(), any(), anyString(), anyString(), any());
        assertThat(owner.getValue()).isEqualTo("orchestra");
        assertThat(observer.outputs).hasSize(1);
        assertThat(observer.outputs.get(0).getDocumentId()).isEqualTo("b2b-1");
        // The bytes are absent by construction - DocumentRef has no content field at all,
        // which is the whole point of a reference. What travels is the size, not the file.
        assertThat(observer.outputs.get(0).getSizeBytes()).isEqualTo(XLSX_BYTES.length);
        assertThat(observer.outputs.get(0).getSha256()).isEqualTo("abc123");
    }

    @Test
    @DisplayName("an upload with no thread_id is refused before any bytes are stored")
    void uploadRequiresAThreadId() {
        var observer = new RecordingObserver<org.sandbox.langgraph.grpc.external.DocumentRef>();

        call(() -> service.uploadDocument(
                org.sandbox.langgraph.grpc.external.UploadDocumentRequest.newBuilder()
                        .setFilename("q1.xlsx").setContent(
                                com.google.protobuf.ByteString.copyFrom(XLSX_BYTES))
                        .build(), observer));

        assertThat(errorOf(observer).getStatus().getCode()).isEqualTo(Status.Code.INVALID_ARGUMENT);
        verify(documentService, never()).store(anyString(), anyString(), any(), anyString(), anyString(), any());
    }

    @Test
    @DisplayName("a rejected file surfaces as INVALID_ARGUMENT carrying the reason")
    void uploadRejectsAnUnusableFile() {
        when(documentService.store(anyString(), anyString(), any(), anyString(), anyString(), any()))
                .thenThrow(new DocumentRejectedException("unsupported_type", "Only .xlsx and .docx documents can be attached."));

        var observer = new RecordingObserver<org.sandbox.langgraph.grpc.external.DocumentRef>();
        call(() -> service.uploadDocument(
                org.sandbox.langgraph.grpc.external.UploadDocumentRequest.newBuilder()
                        .setThreadId("t-1").setFilename("shot.png").setMimeType("image/png")
                        .setContent(com.google.protobuf.ByteString.copyFrom(XLSX_BYTES))
                        .build(), observer));

        StatusRuntimeException error = errorOf(observer);
        assertThat(error.getStatus().getCode()).isEqualTo(Status.Code.INVALID_ARGUMENT);
        assertThat(error.getMessage()).contains(".xlsx and .docx");
    }

    @Test
    @DisplayName("a chunked upload reassembles the bytes and takes the name from the last frame")
    void uploadStreamReassemblesChunks() {
        when(documentService.store(anyString(), anyString(), any(), anyString(), anyString(), any()))
                .thenAnswer(invocation ->
                        stored("b2b-stream", invocation.getArgument(1), invocation.getArgument(1)));

        var observer = new RecordingObserver<org.sandbox.langgraph.grpc.external.DocumentRef>();
        call(() -> {
            var requests = service.uploadDocumentStream(observer);
            requests.onNext(chunk("t-1", "", "", new byte[]{'P', 'K'}));
            requests.onNext(chunk("t-1", "big.xlsx", XLSX_MIME, new byte[]{3, 4}));
            requests.onCompleted();
        });

        ArgumentCaptor<byte[]> content = ArgumentCaptor.forClass(byte[].class);
        verify(documentService).store(anyString(), anyString(), any(), anyString(), anyString(), content.capture());
        assertThat(content.getValue()).containsExactly('P', 'K', 3, 4);
        assertThat(observer.outputs).hasSize(1);
        assertThat(observer.completed).isTrue();
    }

    private static org.sandbox.langgraph.grpc.external.DocumentChunk chunk(
            String threadId, String filename, String mimeType, byte[] data) {
        return org.sandbox.langgraph.grpc.external.DocumentChunk.newBuilder()
                .setThreadId(threadId).setFilename(filename).setMimeType(mimeType)
                .setContent(com.google.protobuf.ByteString.copyFrom(data)).build();
    }

    // --- Attaching a document to a run ------------------------------------------------

    private org.sandbox.langgraph.grpc.external.ExecuteGraphRequest runRequest(
            com.google.protobuf.Struct context, byte[] inline) {
        var builder = org.sandbox.langgraph.grpc.external.ExecuteGraphRequest.newBuilder()
                .setGraphId("g1").setThreadId("abc").setInput("summarise it")
                .setContext(context);
        if (inline != null) {
            builder.setDocument(com.google.protobuf.ByteString.copyFrom(inline))
                    .setDocumentFilename("inline.xlsx")
                    .setDocumentMimeType(XLSX_MIME);
        }
        return builder.build();
    }

    private static com.google.protobuf.Struct contextWith(String key, String value) {
        return com.google.protobuf.Struct.newBuilder()
                .putFields(key, com.google.protobuf.Value.newBuilder().setStringValue(value).build())
                .build();
    }

    @Test
    @DisplayName("an inline document is stored and its id reaches the run context")
    void inlineDocumentReachesTheRunContext() {
        when(documentService.store(anyString(), anyString(), any(), anyString(), anyString(), any()))
                .thenAnswer(invocation -> stored("b2b-inline", invocation.getArgument(1), invocation.getArgument(1)));
        allowRun();

        var observer = new RecordingObserver<org.sandbox.langgraph.grpc.external.ExecuteGraphResponse>();
        call(() -> service.executeGraph(runRequest(com.google.protobuf.Struct.getDefaultInstance(), XLSX_BYTES), observer));

        ArgumentCaptor<ExecuteGraphInput> captor = ArgumentCaptor.forClass(ExecuteGraphInput.class);
        verify(langGraphService).executeGraphStream(captor.capture(), anyBoolean(), any(RequestOrigin.class), any());
        assertThat(captor.getValue().context())
                .as("the reference must be in the context, which is the only channel the DOCUMENT node reads")
                .containsEntry("document_ref", "b2b-inline");
        assertThat(observer.errors).isEmpty();
    }

    @Test
    @DisplayName("a reference the caller uploaded is checked for ownership and passes through")
    void suppliedReferenceIsOwnershipChecked() {
        when(documentService.requireOwnedBy("b2b-mine", "b2b", "orchestra"))
                .thenReturn(stored("b2b-mine", "b2b", "orchestra"));
        allowRun();

        var observer = new RecordingObserver<org.sandbox.langgraph.grpc.external.ExecuteGraphResponse>();
        call(() -> service.executeGraph(
                runRequest(contextWith("document_ref", "b2b-mine"), null), observer));

        verify(documentService).requireOwnedBy("b2b-mine", "b2b", "orchestra");
        assertThat(observer.errors).isEmpty();
    }

    @Test
    @DisplayName("another tenant's reference is refused, and reads as NOT_FOUND - never as 'forbidden'")
    void foreignReferenceIsRefusedIndistinguishably() {
        when(documentService.requireOwnedBy(anyString(), anyString(), anyString()))
                .thenThrow(new DocumentNotFoundException("b2b-theirs"));
        allowRun();

        var observer = new RecordingObserver<org.sandbox.langgraph.grpc.external.ExecuteGraphResponse>();
        call(() -> service.executeGraph(
                runRequest(contextWith("document_ref", "b2b-theirs"), null), observer));

        StatusRuntimeException error = errorOf(observer);
        assertThat(error.getStatus().getCode()).isEqualTo(Status.Code.NOT_FOUND);
        assertThat(error.getStatus().getDescription()).doesNotContain("tenant");
        verify(langGraphService, never())
                .executeGraphStream(any(), anyBoolean(), any(RequestOrigin.class), any());
    }

    @Test
    @DisplayName("sending both an inline document and a reference is a request error, not a precedence rule")
    void bothAttachmentFormsAreRejected() {
        var observer = new RecordingObserver<org.sandbox.langgraph.grpc.external.ExecuteGraphResponse>();
        call(() -> service.executeGraph(
                runRequest(contextWith("document_ref", "b2b-x"), XLSX_BYTES), observer));

        StatusRuntimeException error = errorOf(observer);
        assertThat(error.getStatus().getCode()).isEqualTo(Status.Code.INVALID_ARGUMENT);
        assertThat(error.getStatus().getDescription()).contains("mutually exclusive");
        verify(documentService, never()).store(anyString(), anyString(), any(), anyString(), anyString(), any());
        verify(langGraphService, never())
                .executeGraphStream(any(), anyBoolean(), any(RequestOrigin.class), any());
    }

    @Test
    @DisplayName("a run with no attachment never touches the document store")
    void noAttachmentMeansNoDocumentWork() {
        allowRun();

        var observer = new RecordingObserver<org.sandbox.langgraph.grpc.external.ExecuteGraphResponse>();
        call(() -> service.executeGraph(
                runRequest(com.google.protobuf.Struct.getDefaultInstance(), null), observer));

        verify(documentService, never()).store(anyString(), anyString(), any(), anyString(), anyString(), any());
        verify(documentService, never()).requireOwnedBy(anyString(), anyString(), anyString());
        assertThat(observer.errors).isEmpty();
    }
}

