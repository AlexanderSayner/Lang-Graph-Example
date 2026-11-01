package org.sandbox.langgraph.service.llm;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * The contract of the one credential channel a B2B caller owns.
 *
 * <p>The engine is deliberately tolerant (a malformed map degrades to "no keys"), which is
 * right for a browser session whose keys the server built and wrong for an integrator: their
 * payload must be accepted, normalised or rejected <em>at the edge</em>, with a message that
 * names the field - before a single node of their graph runs.</p>
 */
class LlmCredentialsJsonTest {

    @Test
    @DisplayName("a well-formed payload is accepted and canonicalised")
    void acceptsAWellFormedPayload() {
        String json = LlmCredentialsJson.validate(
                "{\"openai\":{\"api_key\":\"  sk-1  \",\"folder_id\":\"stale\",\"extra\":\"x\"}}");

        // Trimmed, non-Yandex folder id dropped, unknown value fields dropped - the engine
        // sees one clean shape whatever the caller spelled.
        assertThat(json).isEqualTo(
                "{\"openai\":{\"api_key\":\"sk-1\",\"folder_id\":\"\"}}");
    }

    @Test
    @DisplayName("aliases normalise exactly like the UI path, so one provider stays one entry")
    void normalisesAliases() {
        String json = LlmCredentialsJson.validate("{\"claude\":{\"api_key\":\"sk-c\"}}");

        assertThat(json).contains("\"anthropic\"").doesNotContain("\"claude\"");
    }

    @Test
    @DisplayName("a yandex entry keeps its folder id - it is the one provider it means anything to")
    void keepsTheYandexFolderId() {
        String json = LlmCredentialsJson.validate(
                "{\"yandex\":{\"api_key\":\"yc\",\"folder_id\":\"my-folder\"}}");

        assertThat(json).contains("\"folder_id\":\"my-folder\"");
    }

    @Test
    @DisplayName("absent or empty means 'no keys', not an error")
    void absentMeansNoKeys() {
        assertThat(LlmCredentialsJson.validate(null)).isNull();
        assertThat(LlmCredentialsJson.validate("   ")).isNull();
        assertThat(LlmCredentialsJson.validate("{}")).isNull();
    }

    @Test
    @DisplayName("an unknown provider is rejected here, not as a node error minutes later")
    void rejectsAnUnknownProvider() {
        assertThatThrownBy(() -> LlmCredentialsJson.validate(
                "{\"telepathy\":{\"api_key\":\"sk\"}}"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Unknown LLM provider 'telepathy'");
    }

    @Test
    @DisplayName("a provider entry without a key is rejected")
    void rejectsAMissingKey() {
        assertThatThrownBy(() -> LlmCredentialsJson.validate("{\"openai\":{}}"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("no non-blank api_key");
    }

    @Test
    @DisplayName("a non-object payload is rejected with the shape it must have")
    void rejectsANonObjectPayload() {
        assertThatThrownBy(() -> LlmCredentialsJson.validate("[\"sk\"]"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("JSON object");
        assertThatThrownBy(() -> LlmCredentialsJson.validate("not json"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("llm_credentials_json");
    }

    @Test
    @DisplayName("two spellings of one provider are rejected instead of silently overwriting")
    void rejectsDuplicateProvidersUnderAnAlias() {
        assertThatThrownBy(() -> LlmCredentialsJson.validate(
                "{\"claude\":{\"api_key\":\"sk-1\"},\"anthropic\":{\"api_key\":\"sk-2\"}}"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("twice");
    }

    @Test
    @DisplayName("an oversized payload is rejected before it reaches anything")
    void rejectsAnOversizedPayload() {
        String oversized = "{\"openai\":{\"api_key\":\"" + "x".repeat(LlmCredentialsJson.MAX_LENGTH) + "\"}}";

        assertThatThrownBy(() -> LlmCredentialsJson.validate(oversized))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("at most");
    }
}