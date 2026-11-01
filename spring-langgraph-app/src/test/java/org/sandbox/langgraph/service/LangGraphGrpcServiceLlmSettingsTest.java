package org.sandbox.langgraph.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.grpc.Status;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.core.service.GraphPostgresService;
import org.sandbox.langgraph.grpc.GetLlmSettingsRequest;
import org.sandbox.langgraph.grpc.GetLlmSettingsResponse;
import org.sandbox.langgraph.grpc.LangGraphServiceGrpc;
import org.sandbox.langgraph.mapper.GraphGrpcMapper;
import org.sandbox.langgraph.security.CurrentUser;
import org.sandbox.langgraph.service.document.DocumentService;
import org.sandbox.langgraph.service.llm.LlmRequestCredentials;
import org.sandbox.langgraph.service.user.UserLlmCredentialService;

import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * Pins how the engine's LLM settings reach the UI, and - just as importantly - what the
 * backend says when the engine cannot be asked.
 *
 * <p>The UI names the default provider to a user <em>before</em> their run, so a wrong value
 * is a broken promise: the mapping must carry exactly what the engine reported. And because
 * this probe runs on every page load, an outage must degrade to "unknown" instead of failing
 * the whole {@code llmBillingSettings} query the chat panel needs.</p>
 */
class LangGraphGrpcServiceLlmSettingsTest {

    private final GraphGrpcMapper mapper = mock(GraphGrpcMapper.class);
    private final GraphPostgresService store = mock(GraphPostgresService.class);

    private LangGraphGrpcService service(GetLlmSettingsResponse response, boolean fail) {
        LangGraphServiceGrpc.LangGraphServiceBlockingStub stub =
                mock(LangGraphServiceGrpc.LangGraphServiceBlockingStub.class);
        when(stub.withDeadlineAfter(anyLong(), any(TimeUnit.class))).thenReturn(stub);
        if (fail) {
            when(stub.getLlmSettings(any(GetLlmSettingsRequest.class)))
                    .thenThrow(Status.UNAVAILABLE.withDescription("engine down").asRuntimeException());
        } else {
            when(stub.getLlmSettings(any(GetLlmSettingsRequest.class))).thenReturn(response);
        }
        return new LangGraphGrpcService(stub, mapper, new ObjectMapper(), store,
                new LlmRequestCredentials(new CurrentUser(), mock(UserLlmCredentialService.class)),
                mock(DocumentService.class), new CurrentUser());
    }

    @Test
    @DisplayName("the engine's default provider and kill switch travel unchanged")
    void carriesWhatTheEngineReported() {
        LangGraphGrpcService service = service(GetLlmSettingsResponse.newBuilder()
                .setDefaultProvider("openrouter")
                .setDefaultModel("openai/gpt-4o-mini")
                .setServiceKeyEnabled(false)
                .build(), false);

        LangGraphGrpcService.LlmEngineSettings settings = service.getLlmSettings();

        assertThat(settings.defaultProvider()).isEqualTo("openrouter");
        assertThat(settings.defaultModel()).isEqualTo("openai/gpt-4o-mini");
        assertThat(settings.serviceKeyEnabled()).isFalse();
    }

    @Test
    @DisplayName("an unreachable engine reports 'unknown' instead of failing the query")
    void degradesToUnknownWhenTheEngineIsDown() {
        LangGraphGrpcService.LlmEngineSettings settings =
                service(null, true).getLlmSettings();

        // Empty provider = the UI names no provider (it may not guess one); shared key assumed
        // spendable, because assuming otherwise would warn every run of a deployment that is
        // simply mid-restart.
        assertThat(settings.defaultProvider()).isEmpty();
        assertThat(settings.defaultModel()).isEmpty();
        assertThat(settings.serviceKeyEnabled()).isTrue();
        assertThat(settings).isEqualTo(LangGraphGrpcService.LlmEngineSettings.unknown());
    }
}