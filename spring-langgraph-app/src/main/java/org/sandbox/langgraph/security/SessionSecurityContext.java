package org.sandbox.langgraph.security;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.servlet.http.HttpSession;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.security.core.context.SecurityContext;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.context.SecurityContextRepository;
import org.springframework.stereotype.Component;

/**
 * Carries the authenticated caller into (and out of) the HTTP session.
 *
 * <p><b>Why this exists.</b> Spring Security 7's {@code SecurityContextHolderFilter} is configured
 * with {@code requireExplicitSave = true}, so nothing ever saves a context for us: a login that
 * only sets {@link SecurityContextHolder} authenticates the request it runs in and every later
 * request is anonymous. That is precisely what made {@code llmCredentials} fail with
 * "Failed to get user id from auth context" even though the browser was signed in - the
 * authentication died with the login request. Saving explicitly with a
 * {@link SecurityContextRepository} is the documented way out.</p>
 *
 * <p><b>Why the request is an argument.</b> The login mutation is a GraphQL data fetcher, and
 * Boot 4 runs those on the application task executor rather than on the servlet thread. Only the
 * security context is propagated to that thread (spring-graphql's
 * {@code SecurityContextThreadLocalAccessor}); the request attributes are not, so nothing in a
 * data fetcher can find the {@code HttpServletRequest} on its own. {@link
 * ServletRequestGraphQlInterceptor} therefore publishes it into the GraphQL context on the
 * servlet thread and the controller passes it in.</p>
 */
@Component
@RequiredArgsConstructor
@Slf4j
public class SessionSecurityContext {

    private final SecurityContextRepository repository;

    /**
     * Persist the caller so the <em>next</em> request is authenticated.
     *
     * <p>A no-op for an anonymous caller (never store an empty context) and for a transport with
     * no servlet request (a WebSocket message has no session to write to).</p>
     */
    public void persist(HttpServletRequest request, HttpServletResponse response) {
        if (request == null || response == null) {
            return;
        }
        SecurityContext context = SecurityContextHolder.getContext();
        if (context.getAuthentication() == null) {
            return;
        }
        // No response wrapper is installed around us, so this writes straight into the session
        // - creating one when there is none - instead of deferring to the end of the request.
        repository.saveContext(context, request, response);
        log.debug("Stored the security context of user {} in the HTTP session",
                context.getAuthentication().getName());
    }

    /**
     * Sign out: drop the session so the cookie cannot be replayed, then clear the thread.
     *
     * <p>Invalidating the session matters: clearing only the ThreadLocal would leave a live
     * JSESSIONID carrying the caller's identity.</p>
     */
    public void clear(HttpServletRequest request) {
        if (request != null) {
            HttpSession session = request.getSession(false);
            if (session != null) {
                session.invalidate();
                log.debug("Invalidated the HTTP session on sign-out");
            }
        }
        SecurityContextHolder.clearContext();
    }
}