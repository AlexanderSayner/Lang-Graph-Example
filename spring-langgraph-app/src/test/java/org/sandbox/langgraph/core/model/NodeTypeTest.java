package org.sandbox.langgraph.core.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Tests for {@link NodeType#fromString(String)}, the lenient parser used on the <b>read</b> path.
 * <p>
 * That parser is what turns a persisted graph definition into GraphQL output, so every alias
 * asserted here is a compatibility guarantee: dropping one makes every stored graph that still
 * uses it un-loadable, because a single unknown value fails the whole {@code getGraphView} with
 * {@code Unknown node type: <value>}. The {@code TOOL} -> {@code HTTP_REQUEST} alias is the exact
 * regression that motivated this test.
 */
class NodeTypeTest {

    @ParameterizedTest(name = "{0} -> {1}")
    @CsvSource({
            "START, START",
            "END, END",
            "ACTION, ACTION",
            "HUMAN, HUMAN",
            "GRAPH, GRAPH",
            "HTTP_REQUEST, HTTP_REQUEST",
    })
    void parsesCanonicalEnumNames(String raw, NodeType expected) {
        assertThat(NodeType.fromString(raw)).isEqualTo(expected);
    }

    @ParameterizedTest(name = "{0} -> {1}")
    @CsvSource({
            "TOOL, HTTP_REQUEST", // node type before the Tool -> HTTP_REQUEST rename
            "HTTP, HTTP_REQUEST",
            "AGENT, ACTION",
            "LLM, ACTION",
            "CHAT, ACTION",
            "HUMAN_INPUT, HUMAN",
            "INTERRUPT, HUMAN",
            "SUBGRAPH, GRAPH",
    })
    void keepsDeprecatedAliasesWorking(String raw, NodeType expected) {
        assertThat(NodeType.fromString(raw)).isEqualTo(expected);
    }

    @Test
    @DisplayName("parsing is case-insensitive and trims surrounding whitespace")
    void normalizesCasingAndWhitespace() {
        assertThat(NodeType.fromString("  http_request ")).isEqualTo(NodeType.HTTP_REQUEST);
        assertThat(NodeType.fromString("tool")).isEqualTo(NodeType.HTTP_REQUEST);
        assertThat(NodeType.fromString("human_input")).isEqualTo(NodeType.HUMAN);
    }

    @Test
    @DisplayName("an unknown value must fail loudly - a silent default would mis-execute the node")
    void rejectsUnknownValue() {
        assertThatThrownBy(() -> NodeType.fromString("TOOLS"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Unknown node type");
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "   "})
    void rejectsBlankValue(String raw) {
        assertThatThrownBy(() -> NodeType.fromString(raw))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("blank");
    }

    @Test
    void rejectsNull() {
        assertThatThrownBy(() -> NodeType.fromString(null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("blank");
    }
}
