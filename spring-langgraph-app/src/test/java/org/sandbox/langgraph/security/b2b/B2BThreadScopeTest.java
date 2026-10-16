package org.sandbox.langgraph.security.b2b;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatIllegalArgumentException;

/**
 * Tenant scoping of thread ids.
 * <p>
 * A thread is the address of a run's checkpoint, state, history and cancellation,
 * and nothing in the engine owns one - so without this prefix one B2B client could
 * read and cancel another tenant's conversation.
 */
class B2BThreadScopeTest {

    private final B2BThreadScope scope = new B2BThreadScope();

    private static final B2BClientContext CLIENT =
            B2BClientContext.of("orchestra", "orchestra", "", List.of("*"));

    @Test
    @DisplayName("a bare thread id is namespaced with the caller's tenant")
    void prefixesBareThreadId() {
        assertThat(scope.toInternal(CLIENT, "abc")).isEqualTo("orchestra:abc");
    }

    @Test
    @DisplayName("an already scoped id from the same tenant is passed through")
    void keepsOwnPrefix() {
        assertThat(scope.toInternal(CLIENT, "orchestra:abc")).isEqualTo("orchestra:abc");
    }

    @Test
    @DisplayName("another tenant's prefix is rejected instead of silently re-prefixed")
    void rejectsForeignPrefix() {
        assertThatIllegalArgumentException()
                .isThrownBy(() -> scope.toInternal(CLIENT, "other:abc"))
                .withMessage("thread_id is not scoped to this tenant");
    }

    @Test
    @DisplayName("a blank thread id is rejected - the thread addresses the run")
    void rejectsBlankThreadId() {
        assertThatIllegalArgumentException()
                .isThrownBy(() -> scope.toInternal(CLIENT, "  "))
                .withMessage("thread_id is required");
    }

    @Test
    @DisplayName("the tenant prefix is stripped again on the way out")
    void stripsOwnPrefixOnResponse() {
        assertThat(scope.toExternal(CLIENT, "orchestra:abc")).isEqualTo("abc");
        assertThat(scope.toExternal(CLIENT, "unprefixed")).isEqualTo("unprefixed");
        assertThat(scope.toExternal(CLIENT, null)).isNull();
    }
}
