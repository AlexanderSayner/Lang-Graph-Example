package org.sandbox.langgraph.security;

import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;

import java.util.Optional;
import java.util.UUID;

/**
 * The authenticated caller, read from the standard ThreadLocal security context.
 *
 * <p>Extracted from {@code UserService} because several components need to know <em>who is
 * calling</em> without pulling in thread-management concerns: the execute and copilot paths
 * resolve this user's personal LLM keys on every request (they are never cached between
 * requests, so deleting a key takes effect on the very next run).</p>
 *
 * <p>Anonymous callers are a normal state, not an error - {@link #id()} returns empty and the
 * request proceeds on the shared service key. Only {@link #requireId()} refuses.</p>
 */
@Component
public class CurrentUser {

    /** The authenticated user's id, or empty when anonymous / unauthenticated. */
    public Optional<UUID> id() {
        Authentication authentication = current();
        if (!isUsable(authentication)) {
            return Optional.empty();
        }
        try {
            return Optional.of(UUID.fromString(authentication.getName()));
        } catch (IllegalArgumentException e) {
            return Optional.empty();
        }
    }

    /** Like {@link #id()} but refuses when there is no authenticated user. */
    public UUID requireId() {
        Authentication authentication = current();
        if (!isUsable(authentication)) {
            throw new AccessDeniedException("Failed to get user id from auth context");
        }
        try {
            return UUID.fromString(authentication.getName());
        } catch (IllegalArgumentException e) {
            throw new AccessDeniedException(
                    "Invalid user ID format in auth context: " + authentication.getName());
        }
    }

    private Authentication current() {
        return SecurityContextHolder.getContext().getAuthentication();
    }

    private boolean isUsable(Authentication authentication) {
        return authentication != null
                && authentication.isAuthenticated()
                && !"anonymousUser".equals(authentication.getName());
    }
}