package org.sandbox.langgraph.dto.pagination;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Parsing of a configured {@code allowed-graphs} scope - the rules the call-time gate and the
 * ListGraphs query both inherit, so a mistake here is a permission mistake rather than a display
 * bug.
 */
class GraphScopeFilterTest {

    @Test
    @DisplayName("an absent scope denies every graph - a mistake must deny, never open")
    void emptyScopeDeniesEverything() {
        for (GraphScopeFilter filter : List.of(
                GraphScopeFilter.of(null),
                GraphScopeFilter.of(List.of()),
                // Arrays.asList, not List.of: an entry may be null in a configured list, and that
                // entry must be skipped rather than blow up in the parser.
                GraphScopeFilter.of(java.util.Arrays.asList("", "   ", null)))) {

            assertThat(filter.unrestricted()).isFalse();
            assertThat(filter.exactIds()).isEmpty();
            assertThat(filter.prefixes()).isEmpty();
            // Including the exact id it would have matched, had it been granted anything.
            assertThat(filter.allows("agent-v1")).isFalse();
            assertThat(filter.allows(null)).isFalse();
            assertThat(filter.allows("")).isFalse();
        }
    }

    @Test
    @DisplayName("entries are trimmed, so ' * ' is the wildcard and not the prefix ' '")
    void trimsEntriesBeforeMatchingThem() {
        GraphScopeFilter wildcard = GraphScopeFilter.of(List.of(" * "));

        // Regression: untrimmed, " * " became the prefix " ", whose LIKE matches nothing - the
        // account was silently locked out of every graph while looking fully granted.
        assertThat(wildcard.unrestricted()).isTrue();
        assertThat(wildcard.allows("anything")).isTrue();

        GraphScopeFilter prefix = GraphScopeFilter.of(List.of("  acme-*  "));

        assertThat(prefix.prefixes()).containsExactly("acme-");
        assertThat(prefix.allows("acme-agent")).isTrue();
        assertThat(prefix.allows("other")).isFalse();

        GraphScopeFilter exact = GraphScopeFilter.of(List.of(" agent-v1 "));

        assertThat(exact.exactIds()).containsExactly("agent-v1");
        assertThat(exact.allows("agent-v1")).isTrue();
        assertThat(exact.allows("agent-v2")).isFalse();
    }

    @Test
    @DisplayName("a single '*' among other entries still grants everything")
    void wildcardWinsRegardlessOfPosition() {
        GraphScopeFilter filter = GraphScopeFilter.of(List.of("agent-v1", "*", "other"));

        assertThat(filter.unrestricted()).isTrue();
        assertThat(filter.allows("not-granted")).isTrue();
    }

    @Test
    @DisplayName("the prefix keeps the characters before the '*' verbatim")
    void prefixIsTheTextBeforeTheWildcard() {
        GraphScopeFilter filter = GraphScopeFilter.of(List.of("agent-*"));

        assertThat(filter.prefixes()).containsExactly("agent-");
        // "agent" itself is not under "agent-", and neither is a tenant that merely starts alike.
        assertThat(filter.allows("agent")).isFalse();
        assertThat(filter.allows("agentx")).isFalse();
        assertThat(filter.allows("agent-1")).isTrue();
    }
}