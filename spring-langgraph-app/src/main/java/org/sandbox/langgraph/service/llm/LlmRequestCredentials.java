package org.sandbox.langgraph.service.llm;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.sandbox.langgraph.exception.LangGraphException;
import org.sandbox.langgraph.security.CurrentUser;
import org.sandbox.langgraph.security.b2b.B2BClientContext;
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
 * all.</b> The B2B facade brings its own keys on the call; when it brings none and the
 * deployment does not spend its shared key (REQUIRE / PERSONAL_ONLY), it is refused here -
 * before a single engine call - as is an anonymous browser under PERSONAL_ONLY. That is the
 * readable half of what the engine's {@code LLM_SERVICE_KEY_ENABLED=false} hard-gates
 * underneath: that flag makes the shared key unspendable, these refusals say why, at the API
 * edge, with an instruction the caller can act on. Centralised here on purpose - a future
 * resolver cannot forget the rule.</p>
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
     * Who is asking: {@code "b2b"} for the B2B facade (unset for an interactive session, whose
     * wording is already the right one). The engine uses it only to pick the phrasing of the
     * "no key for this provider" failure - an integrator must be told to send
     * {@code llm_credentials_json}, not to click a 🔑 Keys button in a UI it never sees.
     */
    public static final String CALLER_KIND = "caller_kind";

    /**
     * Every key this class may write, which are therefore the keys the client may NOT
     * define. The two legacy single-key forms are listed because they must be stripped too:
     * a graph picks its provider per node, so a bare {@code llm_api_key} cannot be
     * attributed to one and is dropped rather than guessed at.
     */
    public static final Set<String> RUN_KEYS = Set.of(
            CREDENTIALS_JSON, REQUIRE_USER_KEY, USER_ID, CALLER_KIND, "llm_api_key", "yc_folder_id");

    private final CurrentUser currentUser;
    private final UserLlmCredentialService credentialService;

    /**
     * The context to merge into the engine request, with no caller-supplied credentials - the
     * shape every GraphQL path uses.
     */
    public Map<String, String> forRequest(RequestOrigin origin) {
        return forRequest(origin, null);
    }

    /**
     * Builds the run context for one request.
     *
     * @param suppliedCredentialsJson keys the B2B caller sent with THIS call
     *                                ({@code llm_credentials_json} on the facade's own field).
     *                                <b>Ignored on the GRAPHQL path</b>: a browser client is
     *                                never trusted with a credential channel, so those keys can
     *                                only enter the engine from this server-built map
     * @return the context to merge into the engine request
     * @throws LangGraphException.LlmKeyRequiredException when the deployment does not spend its
     *         shared key and this caller brought no key: an anonymous browser under
     *         {@code PERSONAL_ONLY}, or a B2B call without {@code llm_credentials_json} under
     *         {@code REQUIRE} / {@code PERSONAL_ONLY}
     * @throws IllegalArgumentException when a supplied payload is malformed (the B2B facade
     *                                 maps it to {@code INVALID_ARGUMENT})
     */
    public Map<String, String> forRequest(RequestOrigin origin, String suppliedCredentialsJson) {
        if (origin != RequestOrigin.GRAPHQL) {
            return forB2b(suppliedCredentialsJson);
        }

        UUID userId = currentUser.id().orElse(null);
        if (userId == null) {
            // Anonymous: FALLBACK behaviour by construction (nobody to attribute a key to),
            // and REQUIRE cannot apply either - there is no user who could satisfy it.
            // PERSONAL_ONLY is the exception: it is precisely about this caller.
            if (credentialService.serviceKeyForbidden()) {
                throw new LangGraphException.LlmKeyRequiredException(
                        "Sign in and add a personal LLM key with the '🔑 Keys' button in the "
                                + "sidebar: this deployment never spends the shared service key "
                                + "(LLM_CREDENTIALS_POLICY=PERSONAL_ONLY).");
            }
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
     * The B2B facade: no session exists here by construction, so everything comes from the
     * call itself - the credentials the integrator sent, and the account identity the
     * interceptor resolved.
     *
     * <p>Three properties, in order of importance: a key can only arrive through the facade's
     * own {@code llm_credentials_json} field (validated, so a typo fails at the edge rather
     * than minutes later inside a node); a deployment that refuses to spend its shared key
     * refuses <em>here</em>, with an instruction an integrator can act on, instead of
     * silently billing the operator (which is what REQUIRE used to do for B2B - the policy
     * never reached this branch); and every B2B run carries {@code b2b:<clientId>} as its
     * attribution, so shared-key spend is attributable to an account.</p>
     */
    private Map<String, String> forB2b(String suppliedCredentialsJson) {
        String credentials = LlmCredentialsJson.validate(suppliedCredentialsJson);
        boolean serviceKeyDenied = credentialService.personalKeysRequired();

        if (credentials == null && serviceKeyDenied) {
            throw new LangGraphException.LlmKeyRequiredException(
                    "This request carries no llm_credentials_json and this deployment does not "
                            + "run B2B calls on its shared service key. Send your keys with the "
                            + "call: llm_credentials_json = "
                            + "{\"openai\":{\"api_key\":\"sk-...\",\"folder_id\":\"\"}} "
                            + "- one entry per provider the graph uses.");
        }

        Map<String, String> context = new HashMap<>();
        B2BClientContext b2b = B2BClientContext.current();
        if (b2b != null) {
            context.put(USER_ID, "b2b:" + b2b.clientId());
        }
        // Always, keyed or not: any refusal the ENGINE raises afterwards must address an
        // integrator - and the engine's own kill switch can refuse even when this policy
        // would have let the shared key through.
        context.put(CALLER_KIND, "b2b");
        if (credentials != null) {
            context.put(CREDENTIALS_JSON, credentials);
            log.debug("B2B caller {} supplied personal LLM keys for this run",
                    b2b == null ? "<unknown>" : b2b.clientId());
        }
        if (serviceKeyDenied) {
            context.put(REQUIRE_USER_KEY, "1");
        }
        return Map.copyOf(context);
    }
}