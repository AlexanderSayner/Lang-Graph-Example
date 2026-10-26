package org.sandbox.langgraph.service.llm;

import io.grpc.Context;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.exception.LangGraphException;
import org.sandbox.langgraph.security.CurrentUser;
import org.sandbox.langgraph.security.b2b.B2BClientContext;
import org.sandbox.langgraph.service.user.UserLlmCredentialService;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * The contract that decides whose money a run spends.
 *
 * <p>Three properties matter most: a B2B caller must never be attributed a personal key even
 * when a session happens to be visible on the thread; an anonymous caller must produce no
 * context at all (the pre-migration behaviour) - except under policy=PERSONAL_ONLY, where
 * both are refused outright instead of being served from the shared key.</p>
 */
class LlmRequestCredentialsTest {

    private static final String CREDENTIALS_JSON =
            "{\"openai\":{\"api_key\":\"sk-personal\",\"folder_id\":\"\"}}";

    private final UserLlmCredentialService credentialService = mock(UserLlmCredentialService.class);
    private final LlmRequestCredentials resolver = new LlmRequestCredentials(new CurrentUser(), credentialService);

    @AfterEach
    void clearSecurityContext() {
        SecurityContextHolder.clearContext();
    }

    private void authenticate() {
        UUID userId = UUID.fromString("11111111-1111-1111-1111-111111111111");
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken(userId.toString(), null,
                        java.util.List.of()));
    }

    @Test
    @DisplayName("a signed-in user contributes their keys, who they are, and nothing else")
    void authenticatedUserGetsKeysAndAttribution() {
        authenticate();
        when(credentialService.credentialsJson(any())).thenReturn(Optional.of(CREDENTIALS_JSON));
        when(credentialService.personalKeysRequired()).thenReturn(false);

        Map<String, String> context = resolver.forRequest(RequestOrigin.GRAPHQL);

        assertThat(context)
                .containsEntry(LlmRequestCredentials.CREDENTIALS_JSON, CREDENTIALS_JSON)
                .containsEntry(LlmRequestCredentials.USER_ID, "11111111-1111-1111-1111-111111111111")
                .doesNotContainKey(LlmRequestCredentials.REQUIRE_USER_KEY)
                .hasSize(2);
    }

    @Test
    @DisplayName("a signed-in user with no key still gets attribution, so the run is reportable")
    void userWithoutKeysIsStillAttributable() {
        authenticate();
        when(credentialService.credentialsJson(any())).thenReturn(Optional.empty());
        when(credentialService.personalKeysRequired()).thenReturn(false);

        Map<String, String> context = resolver.forRequest(RequestOrigin.GRAPHQL);

        assertThat(context)
                .containsOnlyKeys(LlmRequestCredentials.USER_ID)
                .containsEntry(LlmRequestCredentials.USER_ID, "11111111-1111-1111-1111-111111111111");
    }

    @Test
    @DisplayName("policy=REQUIRE is forwarded so the engine can refuse instead of spending")
    void requireFlagIsForwarded() {
        authenticate();
        when(credentialService.credentialsJson(any())).thenReturn(Optional.empty());
        when(credentialService.personalKeysRequired()).thenReturn(true);

        Map<String, String> context = resolver.forRequest(RequestOrigin.GRAPHQL);

        assertThat(context).containsEntry(LlmRequestCredentials.REQUIRE_USER_KEY, "1");
    }

    @Test
    @DisplayName("B2B never reads the session's keys, even when one is visible on the thread")
    void b2bNeverContributesPersonalKeys() {
        // The strongest form of the property: credentials are available AND the caller is
        // still a service account - the origin alone must win. What a B2B run may carry now
        // is only what ITS OWN call supplied (nothing here); the session is never consulted.
        authenticate();
        when(credentialService.credentialsJson(any())).thenReturn(Optional.of(CREDENTIALS_JSON));
        when(credentialService.personalKeysRequired()).thenReturn(false);

        Map<String, String> context = resolver.forRequest(RequestOrigin.B2B);

        assertThat(context)
                .doesNotContainKeys(LlmRequestCredentials.CREDENTIALS_JSON,
                        LlmRequestCredentials.REQUIRE_USER_KEY);
        verify(credentialService, never()).credentialsJson(any());
    }

    @Test
    @DisplayName("an anonymous caller contributes nothing - exactly the pre-migration request")
    void anonymousCallerIsUntouched() {
        SecurityContextHolder.clearContext();

        assertThat(resolver.forRequest(RequestOrigin.GRAPHQL)).isEmpty();

        verify(credentialService, never()).credentialsJson(any());
    }

    @Test
    @DisplayName("under PERSONAL_ONLY an anonymous caller is refused: it could never pay")
    void anonymousCallerIsRefusedWhenTheServiceKeyIsForbidden() {
        SecurityContextHolder.clearContext();
        when(credentialService.serviceKeyForbidden()).thenReturn(true);

        assertThatThrownBy(() -> resolver.forRequest(RequestOrigin.GRAPHQL))
                .isInstanceOf(LangGraphException.LlmKeyRequiredException.class)
                .hasMessageContaining("Sign in and add a personal LLM key")
                .hasMessageContaining("PERSONAL_ONLY");

        // Refused before anything is resolved, so the engine is never even asked.
        verify(credentialService, never()).credentialsJson(any());
        verify(credentialService, never()).personalKeysRequired();
    }

    @Test
    @DisplayName("REQUIRE stops a B2B call that brought no key - it used to bill the shared one")
    void b2bWithoutKeysIsRefusedUnderRequire() {
        // The asymmetry this closes: under REQUIRE a browser run was refused while the B2B
        // facade silently received an empty context and spent the operator's key.
        when(credentialService.personalKeysRequired()).thenReturn(true);

        assertThatThrownBy(() -> resolver.forRequest(RequestOrigin.B2B))
                .isInstanceOf(LangGraphException.LlmKeyRequiredException.class)
                .hasMessageContaining("llm_credentials_json");

        verify(credentialService, never()).credentialsJson(any());
    }

    @Test
    @DisplayName("PERSONAL_ONLY without a session behaves the same way it did before")
    void b2bIsRefusedWhenTheServiceKeyIsForbidden() {
        when(credentialService.personalKeysRequired()).thenReturn(true);

        assertThatThrownBy(() -> resolver.forRequest(RequestOrigin.B2B))
                .isInstanceOf(LangGraphException.LlmKeyRequiredException.class)
                .hasMessageContaining("llm_credentials_json");
    }

    @Test
    @DisplayName("PERSONAL_ONLY does not touch a signed-in user: their keys still ride along")
    void aSignedInUserStillRunsUnderPersonalOnly() {
        authenticate();
        when(credentialService.credentialsJson(any())).thenReturn(Optional.of(CREDENTIALS_JSON));
        when(credentialService.personalKeysRequired()).thenReturn(true);
        when(credentialService.serviceKeyForbidden()).thenReturn(true);

        Map<String, String> context = resolver.forRequest(RequestOrigin.GRAPHQL);

        assertThat(context)
                .containsEntry(LlmRequestCredentials.CREDENTIALS_JSON, CREDENTIALS_JSON)
                .containsEntry(LlmRequestCredentials.REQUIRE_USER_KEY, "1");
    }

    @Test
    @DisplayName("without PERSONAL_ONLY the refusals never fire, whatever else is configured")
    void refusalIsBoundToThePersonalOnlyPolicy() {
        SecurityContextHolder.clearContext();
        when(credentialService.serviceKeyForbidden()).thenReturn(false);
        when(credentialService.personalKeysRequired()).thenReturn(false);

        assertThat(resolver.forRequest(RequestOrigin.GRAPHQL)).isEmpty();
        // The B2B branch always carries its own attribution/wording - but never a credential
        // it did not bring itself, and never a refusal.
        assertThat(resolver.forRequest(RequestOrigin.B2B))
                .doesNotContainKeys(LlmRequestCredentials.CREDENTIALS_JSON,
                        LlmRequestCredentials.REQUIRE_USER_KEY)
                .containsEntry(LlmRequestCredentials.CALLER_KIND, "b2b");
    }

    @Test
    @DisplayName("the run keys it may write are exactly the ones a client is denied")
    void runKeysAreTheClientDenyList() {
        assertThat(LlmRequestCredentials.RUN_KEYS)
                .containsExactlyInAnyOrder(
                        LlmRequestCredentials.CREDENTIALS_JSON,
                        LlmRequestCredentials.REQUIRE_USER_KEY,
                        LlmRequestCredentials.USER_ID,
                        LlmRequestCredentials.CALLER_KIND,
                        // legacy single-key forms, stripped and never used
                        "llm_api_key", "yc_folder_id");
    }

    // --- B2B brings its own keys ----------------------------------------------------

    private static final String B2B_KEYS =
            "{\"openai\":{\"api_key\":\"sk-b2b\",\"folder_id\":\"\"}}";

    @Test
    @DisplayName("a B2B call carries its own keys, its kind and (with context) its attribution")
    void b2bBringTheirOwnKeys() throws Exception {
        when(credentialService.personalKeysRequired()).thenReturn(false);

        Map<String, String> context =
                Context.current().withValue(B2BClientContext.KEY,
                                B2BClientContext.of("acme", "tenant", "", List.of("*")))
                        .call(() -> resolver.forRequest(RequestOrigin.B2B, B2B_KEYS));

        assertThat(context)
                .containsEntry(LlmRequestCredentials.CREDENTIALS_JSON, B2B_KEYS)
                .containsEntry(LlmRequestCredentials.CALLER_KIND, "b2b")
                .containsEntry(LlmRequestCredentials.USER_ID, "b2b:acme")
                .doesNotContainKey(LlmRequestCredentials.REQUIRE_USER_KEY);
        // The session is never consulted - the keys came from the call.
        verify(credentialService, never()).credentialsJson(any());
    }

    @Test
    @DisplayName("supplied keys satisfy REQUIRE: the call runs with the fail-closed flag")
    void b2bKeysSatisfyThePolicy() {
        when(credentialService.personalKeysRequired()).thenReturn(true);

        Map<String, String> context = resolver.forRequest(RequestOrigin.B2B, B2B_KEYS);

        assertThat(context)
                .containsEntry(LlmRequestCredentials.CREDENTIALS_JSON, B2B_KEYS)
                .containsEntry(LlmRequestCredentials.REQUIRE_USER_KEY, "1");
    }

    @Test
    @DisplayName("a GraphQL client cannot use the credential channel - its payload is ignored")
    void graphqlNeverAcceptsASuppliedCredentialChannel() {
        authenticate();
        when(credentialService.credentialsJson(any())).thenReturn(Optional.empty());
        when(credentialService.personalKeysRequired()).thenReturn(false);

        Map<String, String> context =
                resolver.forRequest(RequestOrigin.GRAPHQL, B2B_KEYS);

        // Not the caller's payload, and not the flag either: only the B2B facade may name a
        // credential channel, and only for itself.
        assertThat(context)
                .doesNotContainKey(LlmRequestCredentials.CREDENTIALS_JSON)
                .containsKey(LlmRequestCredentials.USER_ID);
    }

    @Test
    @DisplayName("a malformed supplied payload fails at the edge, not minutes later in a node")
    void malformedSuppliedCredentialsAreRejectedAtTheEdge() {
        assertThatThrownBy(() -> resolver.forRequest(RequestOrigin.B2B, "not json"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("llm_credentials_json");
    }
}