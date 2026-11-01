package org.sandbox.langgraph.security;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContext;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.context.HttpSessionSecurityContextRepository;
import org.springframework.security.web.context.SecurityContextRepository;

import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The property every session-scoped feature rests on: after a login the <em>next</em> request is
 * authenticated.
 *
 * <p>Spring Security 7's filter loads the context and clears it again without ever saving it, so
 * before this class existed the authentication died with the login request - which is exactly why
 * {@code llmCredentials} answered "Failed to get user id from auth context" while the browser
 * still looked signed in.</p>
 */
class SessionSecurityContextTest {

    private static final UUID USER_ID = UUID.fromString("11111111-1111-1111-1111-111111111111");

    private final SecurityContextRepository repository = new HttpSessionSecurityContextRepository();
    private final SessionSecurityContext sessionSecurityContext = new SessionSecurityContext(repository);

    @AfterEach
    void clearSecurityContext() {
        SecurityContextHolder.clearContext();
    }

    private void signIn() {
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken(USER_ID.toString(), null, List.of()));
    }

    @Test
    @DisplayName("a login is visible to the next request - the regression behind the AccessDenied error")
    void loginIsVisibleToTheNextRequest() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        signIn();

        sessionSecurityContext.persist(request, new MockHttpServletResponse());

        MockHttpServletRequest nextRequest = new MockHttpServletRequest();
        nextRequest.setSession((MockHttpSession) request.getSession(false));
        SecurityContext reloaded = repository.loadDeferredContext(nextRequest).get();

        assertThat(reloaded.getAuthentication()).isNotNull();
        assertThat(reloaded.getAuthentication().getName()).isEqualTo(USER_ID.toString());
    }

    @Test
    @DisplayName("an anonymous caller is never given a session")
    void anonymousCallerGetsNoSession() {
        MockHttpServletRequest request = new MockHttpServletRequest();

        sessionSecurityContext.persist(request, new MockHttpServletResponse());

        assertThat(request.getSession(false)).isNull();
    }

    @Test
    @DisplayName("signing out drops the session, so the cookie cannot be replayed")
    void clearInvalidatesTheSession() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        signIn();
        sessionSecurityContext.persist(request, new MockHttpServletResponse());
        MockHttpSession session = (MockHttpSession) request.getSession(false);

        sessionSecurityContext.clear(request);

        assertThat(session.isInvalid()).isTrue();
        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNull();
    }

    @Test
    @DisplayName("a transport without a servlet request (a WebSocket message) is a no-op, not a failure")
    void persistWithoutARequestDoesNothing() {
        signIn();

        sessionSecurityContext.persist(null, null);

        // No exception, and nothing was cleared: there was simply nowhere to write to.
        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNotNull();
    }

    @Test
    @DisplayName("signing out without a servlet request still clears the thread")
    void clearWithoutARequestOnlyClearsTheThread() {
        signIn();

        sessionSecurityContext.clear(null);

        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNull();
    }
}