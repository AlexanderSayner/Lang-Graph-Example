package org.sandbox.langgraph.controller;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.dto.graphql.payload.user.LlmBillingSettingsPayload;
import org.sandbox.langgraph.security.CurrentUser;
import org.sandbox.langgraph.security.SessionSecurityContext;
import org.sandbox.langgraph.service.LangGraphGrpcService;
import org.sandbox.langgraph.service.user.UserLlmCredentialService;
import org.sandbox.langgraph.service.user.UserService;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * The billing-settings query merges two independent sources - the backend's own policy and
 * the engine's settings - and the UI words its pre-run hint from the result. Both halves must
 * arrive untouched, including the "engine unknown" case, which the UI renders as a vaguer
 * note rather than a wrong promise.
 */
class UserGraphQLControllerTest {

    private final UserService userService = mock(UserService.class);
    private final UserLlmCredentialService credentialService = mock(UserLlmCredentialService.class);
    private final SessionSecurityContext sessionSecurityContext = mock(SessionSecurityContext.class);
    private final LangGraphGrpcService langGraphService = mock(LangGraphGrpcService.class);

    private UserGraphQLController controller() {
        return new UserGraphQLController(userService, credentialService, new CurrentUser(),
                sessionSecurityContext, langGraphService);
    }

    @Test
    @DisplayName("the payload carries the policy and the engine's settings side by side")
    void mergesPolicyAndEngineFacts() {
        when(credentialService.policyName()).thenReturn("PERSONAL_ONLY");
        when(credentialService.personalKeysEnabled()).thenReturn(true);
        when(credentialService.serviceKeyForbidden()).thenReturn(true);
        when(langGraphService.getLlmSettings())
                .thenReturn(new LangGraphGrpcService.LlmEngineSettings("yandex", "yandexgpt", false));

        LlmBillingSettingsPayload payload = controller().llmBillingSettings();

        assertThat(payload.policy()).isEqualTo("PERSONAL_ONLY");
        assertThat(payload.personalKeysEnabled()).isTrue();
        assertThat(payload.serviceKeyForbidden()).isTrue();
        assertThat(payload.defaultProvider()).isEqualTo("yandex");
        // The engine's kill switch is reported even when the policy itself would allow the
        // shared key: the UI has to know it cannot promise a shared-key run either.
        assertThat(payload.serviceKeyEnabled()).isFalse();
    }

    @Test
    @DisplayName("an unknown engine default is passed through as unknown, never as a guess")
    void unknownEngineDefaultIsPreserved() {
        when(credentialService.policyName()).thenReturn("FALLBACK");
        when(credentialService.personalKeysEnabled()).thenReturn(true);
        when(credentialService.serviceKeyForbidden()).thenReturn(false);
        when(langGraphService.getLlmSettings())
                .thenReturn(LangGraphGrpcService.LlmEngineSettings.unknown());

        LlmBillingSettingsPayload payload = controller().llmBillingSettings();

        assertThat(payload.defaultProvider()).isEmpty();
        assertThat(payload.serviceKeyEnabled()).isTrue();
        assertThat(payload.serviceKeyForbidden()).isFalse();
    }
}