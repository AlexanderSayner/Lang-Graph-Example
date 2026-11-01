package org.sandbox.langgraph.dto.graphql.payload.user;

/**
 * How this deployment resolves an LLM call that has no personal key for its provider.
 *
 * <p>Deployment-level facts only - no user data, nothing secret - which is why the query
 * needs no session: the UI must be able to word its pre-run hint correctly for an anonymous
 * visitor too, and that visitor's run is the one PERSONAL_ONLY refuses outright.</p>
 *
 * @param policy              {@code FALLBACK} / {@code REQUIRE} / {@code PERSONAL_ONLY}
 *                            ({@code LLM_CREDENTIALS_POLICY})
 * @param personalKeysEnabled whether a key can be stored at all (master key configured);
 *                            when false every policy behaves like {@code FALLBACK} for runs
 *                            that cannot carry a key
 * @param serviceKeyForbidden true when a run without a personal key will be refused instead
 *                            of billed to the shared service key - what the UI should say
 *                            instead of "these nodes will run on the shared service key"
 * @param defaultProvider     the engine's resolved {@code LLM_PROVIDER}: what a graph that
 *                            selects no provider of its own actually runs on. Empty when the
 *                            engine could not be reached, so the UI names no provider rather
 *                            than guessing one
 * @param defaultModel        the model that same default provider resolves to
 *                            ({@code LLM_MODEL} / {@code <PROVIDER>_MODEL} / the preset). It
 *                            belongs to {@code defaultProvider} alone - a graph pinning a
 *                            different provider inherits that provider's own default - and is
 *                            empty when the engine could not be reached, so the UI names no
 *                            model rather than the wrong one
 * @param serviceKeyEnabled   false when the engine's {@code LLM_SERVICE_KEY_ENABLED} makes the
 *                            shared key unspendable - a run without a matching personal key
 *                            then fails instead of billing the operator
 */
public record LlmBillingSettingsPayload(
        String policy,
        Boolean personalKeysEnabled,
        Boolean serviceKeyForbidden,
        String defaultProvider,
        String defaultModel,
        Boolean serviceKeyEnabled
) {
}