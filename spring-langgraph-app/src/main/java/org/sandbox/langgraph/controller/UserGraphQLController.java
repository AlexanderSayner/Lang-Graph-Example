package org.sandbox.langgraph.controller;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.dto.graphql.payload.user.AuthPayload;
import org.sandbox.langgraph.dto.graphql.payload.user.ClaimThreadInput;
import org.sandbox.langgraph.dto.graphql.payload.user.ClaimThreadsPayload;
import org.sandbox.langgraph.dto.graphql.payload.user.LlmBillingSettingsPayload;
import org.sandbox.langgraph.dto.graphql.payload.user.LlmCredentialPayload;
import org.sandbox.langgraph.dto.graphql.payload.user.SaveLlmCredentialInput;
import org.sandbox.langgraph.dto.graphql.payload.user.UserPayload;
import org.sandbox.langgraph.dto.graphql.payload.user.UserThread;
import org.sandbox.langgraph.security.CurrentUser;
import org.sandbox.langgraph.security.SessionSecurityContext;
import org.sandbox.langgraph.security.ServletRequestGraphQlInterceptor;
import org.sandbox.langgraph.service.LangGraphGrpcService;
import org.sandbox.langgraph.service.user.UserLlmCredentialService;
import org.sandbox.langgraph.service.user.UserService;
import org.springframework.graphql.data.method.annotation.Argument;
import org.springframework.graphql.data.method.annotation.ContextValue;
import org.springframework.graphql.data.method.annotation.MutationMapping;
import org.springframework.graphql.data.method.annotation.QueryMapping;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.stereotype.Controller;

import java.util.List;

@Controller
@RequiredArgsConstructor
public class UserGraphQLController {

    private final UserService userService;
    private final UserLlmCredentialService llmCredentialService;
    private final CurrentUser currentUser;
    private final SessionSecurityContext sessionSecurityContext;
    private final LangGraphGrpcService langGraphService;

    @QueryMapping
    public @NonNull UserPayload getCurrentUser() {
        return userService.getCurrentUser();
    }

    // --- Public Auth Mutations ---
    //
    // The servlet request and response are handed in from ServletRequestGraphQlInterceptor: a
    // data fetcher runs on Boot's task executor, where RequestContextHolder is not bound, so a
    // login cannot find the session it must write on its own. Both are optional - a WebSocket
    // message carries no servlet request, and then there is simply no session to write to.

    @MutationMapping
    public @NonNull AuthPayload register(
            @Argument String username,
            @Argument String password,
            @ContextValue(name = ServletRequestGraphQlInterceptor.REQUEST, required = false)
            HttpServletRequest request,
            @ContextValue(name = ServletRequestGraphQlInterceptor.RESPONSE, required = false)
            HttpServletResponse response) {
        AuthPayload payload = userService.register(username, password);
        persistSession(payload, request, response);
        return payload;
    }

    @MutationMapping
    public @NonNull AuthPayload login(
            @Argument String username,
            @Argument String password,
            @ContextValue(name = ServletRequestGraphQlInterceptor.REQUEST, required = false)
            HttpServletRequest request,
            @ContextValue(name = ServletRequestGraphQlInterceptor.RESPONSE, required = false)
            HttpServletResponse response) {
        AuthPayload payload = userService.login(username, password);
        persistSession(payload, request, response);
        return payload;
    }

    @MutationMapping
    public @NonNull Boolean logout(
            @ContextValue(name = ServletRequestGraphQlInterceptor.REQUEST, required = false)
            HttpServletRequest request) {
        // The session is what makes the next request authenticated, so signing out has to drop
        // it too: clearing only the ThreadLocal would leave a live cookie behind.
        sessionSecurityContext.clear(request);
        return userService.logout();
    }

    /**
     * Only a <em>successful</em> sign-in is worth writing: a rejected password must never
     * open a session for the caller who just failed to authenticate.
     */
    private void persistSession(AuthPayload payload, HttpServletRequest request, HttpServletResponse response) {
        if (Boolean.TRUE.equals(payload.success())) {
            sessionSecurityContext.persist(request, response);
        }
    }

    // --- Protected Thread Operations ---
    //
    // "!isAnonymous()" and not "isAuthenticated()": Spring Security reports an anonymous
    // AuthenticationToken as authenticated, so the latter would wave a signed-out caller
    // straight through to requireId(), which fails there with a 500 instead of a refusal.

    @PreAuthorize("!isAnonymous()")
    @QueryMapping
    public @NonNull List<UserThread> getUserThreads() {
        return userService.getUserThreads();
    }

    @PreAuthorize("!isAnonymous()")
    @MutationMapping
    public @NonNull ClaimThreadsPayload claimLocalThreads(@Argument List<ClaimThreadInput> threads) {
        return userService.claimLocalThreads(threads);
    }

    @PreAuthorize("!isAnonymous()")
    @MutationMapping
    public @NonNull UserThread renameUserThread(@Argument String threadId, @Argument String title) {
        return userService.renameUserThread(threadId, title);
    }

    @PreAuthorize("!isAnonymous()")
    @MutationMapping
    public @NonNull Boolean deleteUserThread(@Argument String threadId) {
        return userService.deleteUserThread(threadId);
    }

    // --- Personal LLM API keys ---
    //
    // Masked for display (the key itself is never decrypted to answer any of these).
    // Requires a session: keys are strictly per-user, and the request path resolves them
    // from this same principal on every execute/copilot call.

    @PreAuthorize("!isAnonymous()")
    @QueryMapping
    public @NonNull List<LlmCredentialPayload> llmCredentials() {
        return llmCredentialService.listMasked(currentUser.requireId());
    }

    /**
     * Deployment-level billing facts, deliberately readable without a session: the UI needs
     * them before it can word its pre-run hint, and an anonymous visitor is exactly the
     * caller PERSONAL_ONLY refuses - so hiding them behind login would hide them from the
     * person who most needs to read them. Nothing here is user data or a secret.
     *
     * <p>Two sources merged: Spring's own policy (what it refuses) and the engine's settings
     * (what a graph that inherits a provider will actually run on). The engine half fails soft
     * inside {@code getLlmSettings} - an unreachable engine yields an empty provider rather
     * than a broken query, and the UI then states its hint without naming a provider it does
     * not know.</p>
     */
    @QueryMapping
    public @NonNull LlmBillingSettingsPayload llmBillingSettings() {
        LangGraphGrpcService.LlmEngineSettings engine = langGraphService.getLlmSettings();
        return new LlmBillingSettingsPayload(
                llmCredentialService.policyName(),
                llmCredentialService.personalKeysEnabled(),
                llmCredentialService.serviceKeyForbidden(),
                engine.defaultProvider(),
                engine.serviceKeyEnabled());
    }

    /** Create or replace the key for one provider. */
    @PreAuthorize("!isAnonymous()")
    @MutationMapping
    public @NonNull LlmCredentialPayload saveLlmCredential(@Argument SaveLlmCredentialInput input) {
        return llmCredentialService.save(currentUser.requireId(), input);
    }

    /** Delete one provider's key. Takes effect on the very next run (nothing is cached). */
    @PreAuthorize("!isAnonymous()")
    @MutationMapping
    public @NonNull Boolean deleteLlmCredential(@Argument String provider) {
        return llmCredentialService.delete(currentUser.requireId(), provider);
    }
}
