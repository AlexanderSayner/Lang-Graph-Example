package org.sandbox.langgraph.dto.graphql.payload.user;

/**
 * Input for {@code saveLlmCredential}: the raw personal key for one provider.
 *
 * <p>Deliberately a plain record with no validation annotations - the rules that matter are
 * semantic (known provider, non-blank key, feature enabled) and live in
 * {@code UserLlmCredentialService}, so they produce one readable error instead of a
 * per-field constraint message.</p>
 *
 * @param provider  canonical provider id (openai | groq | deepseek | openrouter | anthropic | yandex)
 * @param apiKey    the personal API key, never returned to any client
 * @param ycFolderId YandexGPT only: the user's folder id (a non-secret identifier)
 */
public record SaveLlmCredentialInput(
        String provider,
        String apiKey,
        String ycFolderId
) {
}