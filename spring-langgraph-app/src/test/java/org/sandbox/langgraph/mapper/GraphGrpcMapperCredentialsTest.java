package org.sandbox.langgraph.mapper;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.dto.graphql.input.ExecuteGraphInput;
import org.sandbox.langgraph.service.llm.LlmRequestCredentials;

import java.util.HashMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Pins that <b>the server decides whose LLM key a run spends</b>, never the payload.
 *
 * <p>A client can put anything in {@code ExecuteGraphInput.context}, and that map is handed
 * to the engine verbatim. Left unguarded, {@code llm_api_key} would reach
 * {@code RunnableConfig} and - because the engine renders {@code state["context"]} into the
 * prompt - end up in the request sent to the model provider. The run-context keys are
 * therefore stripped and re-added from the session-resolved values.</p>
 */
class GraphGrpcMapperCredentialsTest {

    private final GraphGrpcMapper mapper = new GraphGrpcMapper() {
    };

    private static ExecuteGraphInput input(Map<String, Object> context) {
        return new ExecuteGraphInput("g1", "hi", context, "t1");
    }

    @Test
    @DisplayName("client-supplied run keys never reach the engine")
    void stripsClientSuppliedRunKeys() {
        Map<String, Object> context = new HashMap<>();
        context.put("tenant", "acme");                          // real graph input
        context.put("llm_api_key", "sk-injected");
        context.put("yc_folder_id", "injected-folder");
        context.put("llm_credentials_json", "{\"openai\":{\"api_key\":\"sk-attacker\"}}");
        context.put("require_user_key", "0");
        context.put("x_user_id", "someone-elses-uuid");

        var request = mapper.toExecuteGraphRequest(input(context), Map.of());

        assertThat(request.getContextMap())
                .containsEntry("tenant", "acme")
                .doesNotContainKeys("llm_api_key", "yc_folder_id", "llm_credentials_json",
                        "require_user_key", "x_user_id");
    }

    @Test
    @DisplayName("the server's own run context is added, and wins over anything the client sent")
    void serverRunContextWins() {
        Map<String, Object> context = new HashMap<>();
        context.put("llm_credentials_json", "attacker-value");
        context.put("require_user_key", "0");

        String resolved = "{\"anthropic\":{\"api_key\":\"sk-real\"}}";
        var request = mapper.toExecuteGraphRequest(input(context),
                Map.of(LlmRequestCredentials.CREDENTIALS_JSON, resolved,
                        LlmRequestCredentials.REQUIRE_USER_KEY, "1"));

        assertThat(request.getContextMap())
                .containsEntry(LlmRequestCredentials.CREDENTIALS_JSON, resolved)
                .containsEntry(LlmRequestCredentials.REQUIRE_USER_KEY, "1");
    }

    @Test
    @DisplayName("with no session there is no run context at all - the legacy request shape")
    void noRunContextIsTheLegacyRequest() {
        var request = mapper.toExecuteGraphRequest(
                input(Map.of("tenant", "acme")), Map.of());

        assertThat(request.getContextMap()).containsOnlyKeys("tenant");
    }

    @Test
    @DisplayName("a null context is not an error")
    void handlesNullContext() {
        var request = mapper.toStreamExecuteGraphRequest(input(null),
                Map.of(LlmRequestCredentials.USER_ID, "u-1"));

        assertThat(request.getContextMap())
                .hasSize(1)
                .containsEntry(LlmRequestCredentials.USER_ID, "u-1");
        assertThat(request.getStreamOutput()).isTrue();
    }

    @Test
    @DisplayName("the single-argument overload still strips (nothing to add back)")
    void singleArgOverloadStripsToo() {
        var request = mapper.toExecuteGraphRequest(input(Map.of("llm_api_key", "sk-x")));

        assertThat(request.getContextMap()).isEmpty();
    }

    @Test
    @DisplayName("an execution event carries through who paid for it")
    void mapsLlmBilling() {
        var response = org.sandbox.langgraph.grpc.ExecuteGraphResponse.newBuilder()
                .setEventType("NODE_END")
                .setNodeId("n1")
                .setLlmBilling("service")
                .build();

        assertThat(mapper.toGraphExecutionEventPayload(response).llmBilling())
                .isEqualTo("service");
    }
}