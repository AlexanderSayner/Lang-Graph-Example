package org.sandbox.langgraph.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.core.service.GraphPostgresService;
import org.sandbox.langgraph.dto.graphql.input.ExecuteGraphInput;
import org.sandbox.langgraph.grpc.ExecuteGraphRequest;
import org.sandbox.langgraph.grpc.LangGraphServiceGrpc;
import org.sandbox.langgraph.mapper.GraphGrpcMapper;
import org.sandbox.langgraph.security.CurrentUser;
import org.sandbox.langgraph.service.document.DocumentNotFoundException;
import org.sandbox.langgraph.service.document.DocumentService;
import org.sandbox.langgraph.service.llm.LlmRequestCredentials;
import org.sandbox.langgraph.service.llm.RequestOrigin;
import org.sandbox.langgraph.service.user.UserLlmCredentialService;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.assertThatNoException;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * The GraphQL half of the attachment ownership rule.
 *
 * <p>The B2B facade's half is pinned by {@code LangGraphExternalApiServiceDocumentTest}; what
 * this class pins is the door that used to have no rule at all. {@code DocumentService.fetch}
 * trusts the id it is handed, which is only safe if every facade checks the reference first -
 * so a {@code context.document_ref} sent over GraphQL must resolve to a document the caller
 * owns, or the engine is never contacted. A foreign reference is refused exactly like a
 * missing one, because a caller must not be able to probe for ids that exist.</p>
 */
class LangGraphGrpcServiceDocumentTest {

    private static final String OWN_REF = "ui-11111111-mine";
    private static final String THEIR_REF = "ui-22222222-theirs";
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000001111");

    private final GraphGrpcMapper mapper = mock(GraphGrpcMapper.class);
    private final DocumentService documentService = mock(DocumentService.class);
    /** A real resolver: with no authenticated user it yields an empty run context. */
    private final LlmRequestCredentials requestCredentials =
            new LlmRequestCredentials(new CurrentUser(), mock(UserLlmCredentialService.class));

    @AfterEach
    void clearTheSecurityContext() {
        SecurityContextHolder.clearContext();
    }

    private void signIn() {
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken(USER.toString(), "n/a", List.of()));
    }

    private LangGraphGrpcService service() {
        LangGraphServiceGrpc.LangGraphServiceBlockingStub stub =
                mock(LangGraphServiceGrpc.LangGraphServiceBlockingStub.class);
        when(stub.withDeadlineAfter(anyLong(), any(TimeUnit.class))).thenReturn(stub);
        when(mapper.toExecuteGraphRequest(any(ExecuteGraphInput.class), any()))
                .thenReturn(ExecuteGraphRequest.newBuilder().setGraphId("g1").build());
        when(mapper.toStreamExecuteGraphRequest(any(ExecuteGraphInput.class), any()))
                .thenReturn(ExecuteGraphRequest.newBuilder().setGraphId("g1").build());
        return new LangGraphGrpcService(stub, mapper, new ObjectMapper(),
                mock(GraphPostgresService.class), requestCredentials,
                documentService, new CurrentUser());
    }

    private static ExecuteGraphInput inputWith(String documentRef) {
        return new ExecuteGraphInput("g1", "go",
                documentRef == null ? null : Map.of("document_ref", documentRef), "t-1");
    }

    private static ExecuteGraphInput inputWithContext(Map<String, Object> context) {
        return new ExecuteGraphInput("g1", "go", context, "t-1");
    }

    /**
     * A reference in the shape {@link DocumentService#looksLikeDocumentId} recognises: the
     * two facades gate by shape when the key is not the default one, because a graph that
     * reads several files names each slot after its role.
     */
    private static String realShapedRef(String suffix) {
        return "ui-11111111-" + suffix.repeat(32);
    }

    @Test
    @DisplayName("a reference the caller owns is checked and the run proceeds")
    void aReferenceTheCallerOwnsIsAccepted() {
        signIn();
        when(documentService.requireOwnedBy(OWN_REF, DocumentService.OWNER_UI, USER.toString()))
                .thenReturn(null);

        assertThatNoException().isThrownBy(() ->
                service().executeGraphStream(inputWith(OWN_REF), false, RequestOrigin.GRAPHQL));

        verify(documentService).requireOwnedBy(OWN_REF, DocumentService.OWNER_UI, USER.toString());
        verify(mapper).toExecuteGraphRequest(any(ExecuteGraphInput.class), any());
    }

    @Test
    @DisplayName("another owner's reference is refused, and the engine is never asked to run")
    void aForeignReferenceIsRefused() {
        signIn();
        when(documentService.requireOwnedBy(anyString(), anyString(), anyString()))
                .thenThrow(new DocumentNotFoundException(THEIR_REF));

        assertThatThrownBy(() -> service().executeGraphStream(
                inputWith(THEIR_REF), false, RequestOrigin.GRAPHQL))
                .isInstanceOf(DocumentNotFoundException.class);

        verify(documentService).requireOwnedBy(THEIR_REF, DocumentService.OWNER_UI, USER.toString());
    }

    @Test
    @DisplayName("an anonymous caller cannot quote a reference at all - it can own no document")
    void anAnonymousCallerIsRefused() {
        assertThatThrownBy(() -> service().executeGraphStream(
                inputWith(OWN_REF), false, RequestOrigin.GRAPHQL))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Sign in");

        verify(documentService, never())
                .requireOwnedBy(anyString(), anyString(), anyString());
    }

    @Test
    @DisplayName("a run with no reference does no ownership work, signed in or not")
    void noAttachmentMeansNoDocumentWork() {
        service().executeGraphStream(inputWith(null), false, RequestOrigin.GRAPHQL);

        verify(documentService, never())
                .requireOwnedBy(anyString(), anyString(), anyString());
    }

    @Test
    @DisplayName("the B2B facade keeps its own gate - this one must not double-check it")
    void theB2bPathIsLeftAlone() {
        // A B2B caller's identity comes from the API-key interceptor, not from Spring Security,
        // so this gate has to stay out of the way: LangGraphExternalApiService.withAttachment()
        // runs the same rule against the tenant before the run exists.
        assertThatNoException().isThrownBy(() ->
                service().executeGraphStream(inputWith(THEIR_REF), false, RequestOrigin.B2B));

        verify(documentService, never())
                .requireOwnedBy(anyString(), anyString(), anyString());
    }

    @Test
    @DisplayName("the streaming subscription enforces the same rule as the blocking mutation")
    void theSubscriptionIsCoveredToo() {
        signIn();
        when(documentService.requireOwnedBy(anyString(), anyString(), anyString()))
                .thenThrow(new DocumentNotFoundException(THEIR_REF));

        // stream=true is the subscription's path. The gate runs before the Flux exists (that
        // is the point - a subscriber must not be able to reach the engine first), so the
        // refusal is eager and can be asserted directly instead of through a terminal signal.
        assertThatThrownBy(() -> service().executeGraphStream(
                inputWith(THEIR_REF), true, RequestOrigin.GRAPHQL))
                .isInstanceOf(DocumentNotFoundException.class);

        verify(documentService).requireOwnedBy(THEIR_REF, DocumentService.OWNER_UI, USER.toString());
    }

    // --- Several slots in one run --------------------------------------------------------
    //
    // A graph may read more than one document in one run: each DOCUMENT node names its own
    // context key, so `document_ref` is only the default spelling. The gate must follow,
    // or a second slot becomes the hole the first one was closed for.

    @Test
    @DisplayName("a second slot's reference is ownership-checked just like the default key")
    void aSecondSlotIsGatedToo() {
        signIn();
        String timesheets = realShapedRef("a");
        String report = realShapedRef("b");
        when(documentService.requireOwnedBy(anyString(), anyString(), anyString()))
                .thenReturn(null);

        assertThatNoException().isThrownBy(() -> service().executeGraphStream(
                inputWithContext(Map.of("timesheets_ref", timesheets, "report_ref", report)),
                false, RequestOrigin.GRAPHQL));

        verify(documentService).requireOwnedBy(timesheets, DocumentService.OWNER_UI, USER.toString());
        verify(documentService).requireOwnedBy(report, DocumentService.OWNER_UI, USER.toString());
    }

    @Test
    @DisplayName("one foreign reference under a slot key stops the whole run")
    void aForeignSlotReferenceIsRefused() {
        signIn();
        String mine = realShapedRef("a");
        String theirs = realShapedRef("b");
        // The caller owns the first one; the second belongs to somebody else.
        when(documentService.requireOwnedBy(mine, DocumentService.OWNER_UI, USER.toString()))
                .thenReturn(null);
        when(documentService.requireOwnedBy(theirs, DocumentService.OWNER_UI, USER.toString()))
                .thenThrow(new DocumentNotFoundException(theirs));

        assertThatThrownBy(() -> service().executeGraphStream(
                inputWithContext(Map.of("timesheets_ref", mine, "report_ref", theirs)),
                false, RequestOrigin.GRAPHQL))
                .isInstanceOf(DocumentNotFoundException.class);

        verify(mapper, never()).toExecuteGraphRequest(any(ExecuteGraphInput.class), any());
    }

    @Test
    @DisplayName("an ordinary context value is a graph's own business, never a document")
    void anOrdinaryContextValueIsNotAReference() {
        signIn();

        // The context is a free-form map: a tenant, a date or a flag must never be sent to
        // the document store, or every graph that passes its own keys would break.
        assertThatNoException().isThrownBy(() -> service().executeGraphStream(
                inputWithContext(Map.of("tenant", "orchestra", "period", "2026-Q1")),
                false, RequestOrigin.GRAPHQL));

        verify(documentService, never())
                .requireOwnedBy(anyString(), anyString(), anyString());
    }

    @Test
    @DisplayName("an anonymous caller is refused for a slot key too")
    void anAnonymousCallerIsRefusedForASecondSlot() {
        assertThatThrownBy(() -> service().executeGraphStream(
                inputWithContext(Map.of("timesheets_ref", realShapedRef("a"))),
                false, RequestOrigin.GRAPHQL))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Sign in");
    }
}
