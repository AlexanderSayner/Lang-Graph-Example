package org.sandbox.langgraph.core.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Tests for {@link ExecutionEventType#fromString(String)}, the parser used on the read path to
 * turn the Python engine's {@code event_type} string into the GraphQL enum.
 * <p>
 * Every value asserted here is a compatibility guarantee: dropping one makes the engine's
 * corresponding event fail the whole execution stream with
 * {@code Unknown execution event type: <value>}. {@code CANCELLED} in particular is what the
 * execution-cancel feature depends on - the engine emits it after a user cancels a run, and it
 * must stay distinct from {@code ERROR} so the UI shows "cancelled" rather than a failure.
 */
class ExecutionEventTypeTest {

    @ParameterizedTest(name = "{0} -> {1}")
    @CsvSource({
            "START, START",
            "NODE_START, NODE_START",
            "NODE_END, NODE_END",
            "END, END",
            "WAITING_FOR_INPUT, WAITING_FOR_INPUT",
            "ERROR, ERROR",
            "CANCELLED, CANCELLED",
    })
    void parsesCanonicalEnumNames(String raw, ExecutionEventType expected) {
        assertThat(ExecutionEventType.fromString(raw)).isEqualTo(expected);
    }

    @Test
    @DisplayName("parsing is case-insensitive and trims surrounding whitespace")
    void normalizesCasingAndWhitespace() {
        assertThat(ExecutionEventType.fromString("  node_end ")).isEqualTo(ExecutionEventType.NODE_END);
        assertThat(ExecutionEventType.fromString("cancelled")).isEqualTo(ExecutionEventType.CANCELLED);
    }

    @Test
    @DisplayName("CANCELLED is not folded into ERROR - the UI must tell a stop from a failure")
    void cancelledStaysDistinctFromError() {
        assertThat(ExecutionEventType.fromString("CANCELLED")).isNotEqualTo(ExecutionEventType.ERROR);
    }

    @Test
    @DisplayName("an unknown value must fail loudly - a silent default would mis-report the stream")
    void rejectsUnknownValue() {
        assertThatThrownBy(() -> ExecutionEventType.fromString("STOPPED"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Unknown execution event type");
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "   "})
    void rejectsBlankValue(String raw) {
        assertThatThrownBy(() -> ExecutionEventType.fromString(raw))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("blank");
    }

    @Test
    void rejectsNull() {
        assertThatThrownBy(() -> ExecutionEventType.fromString(null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("blank");
    }
}