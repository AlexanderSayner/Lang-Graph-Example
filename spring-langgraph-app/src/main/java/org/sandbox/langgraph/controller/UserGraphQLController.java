package org.sandbox.langgraph.controller;

import lombok.RequiredArgsConstructor;
import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.dto.graphql.payload.user.AuthPayload;
import org.sandbox.langgraph.dto.graphql.payload.user.ClaimThreadInput;
import org.sandbox.langgraph.dto.graphql.payload.user.ClaimThreadsPayload;
import org.sandbox.langgraph.dto.graphql.payload.user.LlmCredentialPayload;
import org.sandbox.langgraph.dto.graphql.payload.user.SaveLlmCredentialInput;
import org.sandbox.langgraph.dto.graphql.payload.user.UserPayload;
import org.sandbox.langgraph.dto.graphql.payload.user.UserThread;
import org.sandbox.langgraph.security.CurrentUser;
import org.sandbox.langgraph.service.user.UserLlmCredentialService;
import org.sandbox.langgraph.service.user.UserService;
import org.springframework.graphql.data.method.annotation.Argument;
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

    @QueryMapping
    public @NonNull UserPayload getCurrentUser() {
        return userService.getCurrentUser();
    }

    // --- Public Auth Mutations ---

    @MutationMapping
    public @NonNull AuthPayload register(@Argument String username, @Argument String password) {
        return userService.register(username, password);
    }

    @MutationMapping
    public @NonNull AuthPayload login(@Argument String username, @Argument String password) {
        return userService.login(username, password);
    }

    @MutationMapping
    public @NonNull Boolean logout() {
        return userService.logout();
    }

    // --- Protected Thread Operations ---

    @PreAuthorize("isAuthenticated()")
    @QueryMapping
    public @NonNull List<UserThread> getUserThreads() {
        return userService.getUserThreads();
    }

    @PreAuthorize("isAuthenticated()")
    @MutationMapping
    public @NonNull ClaimThreadsPayload claimLocalThreads(@Argument List<ClaimThreadInput> threads) {
        return userService.claimLocalThreads(threads);
    }

    @PreAuthorize("isAuthenticated()")
    @MutationMapping
    public @NonNull UserThread renameUserThread(@Argument String threadId, @Argument String title) {
        return userService.renameUserThread(threadId, title);
    }

    @PreAuthorize("isAuthenticated()")
    @MutationMapping
    public @NonNull Boolean deleteUserThread(@Argument String threadId) {
        return userService.deleteUserThread(threadId);
    }

    // --- Personal LLM API keys ---
    //
    // Masked for display (the key itself is never decrypted to answer any of these).
    // Requires a session: keys are strictly per-user, and the request path resolves them
    // from this same principal on every execute/copilot call.

    @PreAuthorize("isAuthenticated()")
    @QueryMapping
    public @NonNull List<LlmCredentialPayload> llmCredentials() {
        return llmCredentialService.listMasked(currentUser.requireId());
    }

    /** Create or replace the key for one provider. */
    @PreAuthorize("isAuthenticated()")
    @MutationMapping
    public @NonNull LlmCredentialPayload saveLlmCredential(@Argument SaveLlmCredentialInput input) {
        return llmCredentialService.save(currentUser.requireId(), input);
    }

    /** Delete one provider's key. Takes effect on the very next run (nothing is cached). */
    @PreAuthorize("isAuthenticated()")
    @MutationMapping
    public @NonNull Boolean deleteLlmCredential(@Argument String provider) {
        return llmCredentialService.delete(currentUser.requireId(), provider);
    }
}
