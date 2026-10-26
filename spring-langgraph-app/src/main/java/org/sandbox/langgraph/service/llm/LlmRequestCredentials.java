package org.sandbox.langgraph.service.llm;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.sandbox.langgraph.security.CurrentUser;
import org.sandbox.langgraph.service.user.UserLlmCredentialService;
import org.springframework.stereotype.Component;

import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * Builds the run context the engine needs in order to bill the right account: the caller's
 * provider-keyed personal keys, who they are, and (under policy=REQUIRE / PERSONAL_ONLY) that
 * the shared key must not be used.
 *
 * <p><b>Resolved synchronously on the request thread.</b> Both the execute and copilot paths
 * read {@link CurrentUser} (a ThreadLocal {@code SecurityContextHolder}) and must therefore
 * do so <em>before</em> anything reactive - a {@code Flux} body or a gRPC callback may run on
 * a different thread where the security context is simply absent, which would silently drop
 * the keys and bill the operator instead. That silence is exactly what this feature exists
 * to prevent, so the resolution never happens downstream of a thread hop.</p>
 *
 * <p><b>It is also the one choke point deciding whether a caller may reach the engine at
 * all.</b> Under policy=PERSONAL_ONLY a caller that can never own a key - an anonymous
 * GraphQL request, or the whole B2B gRPC facade, which has no session by construction - is
 * refused here, before a single engine call is made. That is the readable half of what the
 * engine's {@code LLM_SERVICE_KEY_ENABLED=false} hard-gates underneath: that flag makes the
 * shared key unspendable, this refusal says why, at the API edge, with the operator's own
 * wording. Centralised here on purpose - a future resolver cannot forget the rule.</p>
 *
 * <p>Every value here is also <em>stripped</em> from the client-supplied context by the
 * mapper, so a caller cannot override any of them (see {@link #RUN_KEYS}).</p>
 */
@Component
@RequiredArgsConstructor
@Slf4j
public class LlmRequestCredentials {

    /** Provider-keyed personal keys: {@code {"openai": {"api_key": ..., "folder_id": ...}}}. */
    public static final String CREDENTIALS_JSON = "llm_credentials_json";

    /** "1" - the deployment's policy=REQUIRE / PERSONAL_ONLY, forwarded so the engine can
     * fail closed instead of spending the shared service key. */
    public static final String REQUIRE_USER_KEY = "require_user_key";

    /** Attribution for the engine's per-run billing log (and future metering). */
    public static final String USER_ID = "x_user_id";

    /**
     * Every key this class may write, which are therefore the keys the client may NOT
     * define. The two legacy single-key forms are listed because they must be stripped too:
     * a graph picks its provider per node, so a bare {@code llm_api_key} cannot be
     * attributed to one and is dropped rather than guessed at.
     */
    public static final Set<String> RUN_KEYS = Set.of(
            CREDENTIALS_JSON, REQUIRE_USER_KEY, USER_ID, "llm_api_key", "yc_folder_id");

    private final CurrentUser currentUser;
    private final UserLlmCredentialService credentialService;

    /** The context to merge into the engine request. Empty for anonymous and B2B callers.
     *
     * @throws IllegalArgumentException when policy=PERSONAL_ONLY and this caller could never
     *         have brought a personal key (anonymous GraphQL, B2B) - the shared key may not
     *         be spent on its behalf
     */
    public Map<String, String> forRequest(RequestOrigin origin) {
        if (origin != RequestOrigin.GRAPHQL) {
            // B2B: no session, no stored keys - service key, unchanged from before.
            refuseWhenServiceKeyForbidden(
                    "A B2B service account has no personal LLM key to run with, and this "
                            + "deployment spends personal keys only (LLM_CREDENTIALS_POLICY="
                            + "PERSONAL_ONLY). Sign in to the UI and add your key with the "
                            + "'🔑 Keys' button, or run the deployment with FALLBACK.");
            return Map.of();
        }

        UUID userId = currentUser.id().orElse(null);
        if (userId == null) {
            // Anonymous: FALLBACK behaviour by construction (nobody to attribute a key to),
            // and REQUIRE cannot apply either - there is no user who could satisfy it.
            // PERSONAL_ONLY is the exception: it is precisely about this caller.
            refuseWhenServiceKeyForbidden(
                    "Sign in and add a personal LLM key with the '🔑 Keys' button in the "
                            + "sidebar: this deployment never spends the shared service key "
                            + "(LLM_CREDENTIALS_POLICY=PERSONAL_ONLY).");
            return Map.of();
        }

        Map<String, String> context = new HashMap<>();
        context.put(USER_ID, userId.toString());

        // One query + one AES-GCM pass per key; never cached between requests, so deleting a
        // key in the UI stops it being used on the very next run.
        credentialService.credentialsJson(userId)
                .ifPresent(json -> context.put(CREDENTIALS_JSON, json));

        if (credentialService.personalKeysRequired()) {
            context.put(REQUIRE_USER_KEY, "1");
            if (!context.containsKey(CREDENTIALS_JSON)) {
                // Worth saying out loud: this deployment will refuse every run until the
                // user saves a key, and that is a policy the operator chose.
                log.warn("policy=REQUIRE and user {} has no LLM key yet; their next run will "
                        + "be refused instead of billed to the service account", userId);
            }
        }
        return Map.copyOf(context);
    }

    /**
     * Throws when the shared service key may not be spent on behalf of {@code message}'s
     * caller, i.e. policy=PERSONAL_ONLY and personal keys are usable.
     *
     * <p>Deliberately {@link IllegalArgumentException}, never {@code AccessDeniedException}:
     * {@code GraphQlExceptionHandler} replaces an access denial's text with a fixed
     * "Authentication required", which would hide the one line that explains the operator's
     * choice, and {@code GrpcErrors} would need a new mapping for it. {@code
     * IllegalArgumentException} already travels with <em>this</em> message on both surfaces -
     * {@code BAD_REQUEST} from GraphQL, {@code INVALID_ARGUMENT} from the B2B facade - exactly
     * like the feature's own "keys are disabled" refusal in
     * {@code UserLlmCredentialService.requireEnabled()}.</p>
     */
    private void refuseWhenServiceKeyForbidden(String message) {
        if (credentialService.serviceKeyForbidden()) {
            throw new IllegalArgumentException(message);
        }
    }
}