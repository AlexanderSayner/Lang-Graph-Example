package org.sandbox.langgraph.config.props;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Personal LLM API key storage (app.user_llm_credentials).
 *
 * <p>{@code master-key} is base64 for a 32-byte AES-256 key ({@code openssl rand -base64 32}).
 * An absent/blank value does NOT fail startup: it switches personal keys off entirely, so a
 * deployment can boot (and run exactly as before the migration) without the env var. Saving a
 * key while disabled fails with a readable message instead of silently storing plaintext.</p>
 *
 * <p>{@code policy} decides what happens when an authenticated user has no key for a provider
 * that a node asks for:</p>
 * <ul>
 *   <li>{@code FALLBACK} (default) - the shared service key is used and the run is reported
 *       as {@code billing=service}, so a subsidised run is visible but never fatal;</li>
 *   <li>{@code REQUIRE} - the node fails with an actionable message instead of spending the
 *       operator's credits. The switch for the future subscription tier.</li>
 * </ul>
 */
@ConfigurationProperties(prefix = "llm.credentials")
public record LlmCredentialsProperties(
        String masterKey,
        Policy policy
) {

    public enum Policy {
        FALLBACK,
        REQUIRE
    }

    public LlmCredentialsProperties {
        masterKey = (masterKey == null) ? "" : masterKey.trim();
        policy = (policy == null) ? Policy.FALLBACK : policy;
    }

    /** True when a usable master key is configured (i.e. personal keys are supported). */
    public boolean enabled() {
        return !masterKey.isBlank();
    }

    /** True when a missing personal key must stop the run rather than fall back. */
    public boolean requiresPersonalKey() {
        return policy == Policy.REQUIRE;
    }
}