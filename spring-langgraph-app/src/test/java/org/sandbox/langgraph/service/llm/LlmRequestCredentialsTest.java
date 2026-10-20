package org.sandbox.langgraph.service.llm;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.security.CurrentUser;
import org.sandbox.langgraph.service.user.UserLlmCredentialService;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;

import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * The contract that decides whose money a run spends.
 *
 * <p>Two properties matter most: a B2B caller must never be attributed a personal key even
 * when a session happens to be visible on the thread, and an anonymous caller must produce
 * no context at all (the pre-migration behaviour).</p>
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
    @DisplayName("B2B contributes nothing even when a session is visible on the thread")
    void b2bNeverContributesPersonalKeys() {
        // The strongest form of the property: credentials are available AND the caller is
        // still a service account - the origin alone must win.
        authenticate();
        when(credentialService.credentialsJson(any())).thenReturn(Optional.of(CREDENTIALS_JSON));
        when(credentialService.personalKeysRequired()).thenReturn(true);

        assertThat(resolver.forRequest(RequestOrigin.B2B)).isEmpty();

        verify(credentialService, never()).credentialsJson(any());
        verify(credentialService, never()).personalKeysRequired();
    }

    @Test
    @DisplayName("an anonymous caller contributes nothing - exactly the pre-migration request")
    void anonymousCallerIsUntouched() {
        SecurityContextHolder.clearContext();

        assertThat(resolver.forRequest(RequestOrigin.GRAPHQL)).isEmpty();

        verify(credentialService, never()).credentialsJson(any());
    }

    @Test
    @DisplayName("the run keys it may write are exactly the ones a client is denied")
    void runKeysAreTheClientDenyList() {
        assertThat(LlmRequestCredentials.RUN_KEYS)
                .containsExactlyInAnyOrder(
                        LlmRequestCredentials.CREDENTIALS_JSON,
                        LlmRequestCredentials.REQUIRE_USER_KEY,
                        LlmRequestCredentials.USER_ID,
                        // legacy single-key forms, stripped and never used
                        "llm_api_key", "yc_folder_id");
    }
}