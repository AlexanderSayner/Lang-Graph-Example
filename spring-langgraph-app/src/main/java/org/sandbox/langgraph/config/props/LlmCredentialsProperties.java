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
 * that a node asks for, and how far the shared service key may be spent at all:</p>
 * <ul>
 *   <li>{@code FALLBACK} (default) - the shared service key is used and the run is reported
 *       as {@code billing=service}, so a subsidised run is visible but never fatal;</li>
 *   <li>{@code REQUIRE} - the node fails with an actionable message instead of spending the
 *       operator's credits. The switch for the future subscription tier;</li>
 *   <li>{@code PERSONAL_ONLY} - everything {@code REQUIRE} does for a signed-in user, plus the
 *       service key becomes unspendable for callers who have no user to attribute one to: an
 *       anonymous GraphQL request and the whole B2B gRPC facade are refused with an
 *       actionable message instead of silently billing the operator. Pair it with the
 *       engine's {@code LLM_SERVICE_KEY_ENABLED=false}, which is the guarantee behind this
 *       (no path can read the shared key) - the policy is what turns that into a refusal the
 *       caller can read at the API edge rather than a node error.</li>
 * </ul>
 * <p>Every switch is inert while {@code master-key} is blank: nobody can own a personal key
 * then, so refusing would break runs that cannot possibly be satisfied - fail open, exactly
 * as {@code REQUIRE} does today (see {@code UserLlmCredentialService#personalKeysRequired()}).</p>
 */
@ConfigurationProperties(prefix = "llm.credentials")
public record LlmCredentialsProperties(
        String masterKey,
        Policy policy
) {

    public enum Policy {
        FALLBACK,
        REQUIRE,
        PERSONAL_ONLY
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
        return policy != Policy.FALLBACK;
    }

    /**
     * True when even callers that can own no key (anonymous GraphQL, B2B service accounts)
     * must be refused rather than served from the shared service key.
     */
    public boolean personalKeysOnly() {
        return policy == Policy.PERSONAL_ONLY;
    }
}