package org.sandbox.langgraph.dto.pagination;

import java.util.ArrayList;
import java.util.List;

/**
 * A parsed {@code allowed-graphs} scope, in the two shapes the persistence layer can
 * actually push into a query.
 * <p>
 * The B2B facade must not filter a page of graphs in memory: {@code total_count},
 * {@code has_next_page} and {@code next_page_token} are computed from the page, so a
 * post-filter would hand the caller wrong paging. The scope is therefore turned into
 * predicates ({@code graph_id IN (...)} / {@code graph_id LIKE 'prefix%'}) and applied
 * by {@code GraphPostgresService.listGraphs}.
 * <p>
 * Three shapes, deliberately ordered by how dangerous they are:
 * <ul>
 *   <li>{@code unrestricted} - the account may see every graph (a single {@code *} in
 *       the configuration). Used by the UI/GraphQL path as well, which has no tenant
 *       concept at all.</li>
 *   <li>{@code exactIds} + {@code prefixes} - the account may see only the graphs it
 *       was granted.</li>
 *   <li>neither of the above while not unrestricted - <b>no graph at all</b>. An empty
 *       scope is a configuration mistake, and a mistake must deny rather than open:
 *       an empty JPA {@code Specification} would match everything.</li>
 * </ul>
 *
 * @param exactIds      graph ids granted verbatim
 * @param prefixes      entries that ended with {@code *} - the {@code *} is stripped
 *                      and the rest is matched with {@code LIKE 'rest%'}
 * @param unrestricted  true when the account was granted {@code *} (all graphs)
 */
public record GraphScopeFilter(List<String> exactIds, List<String> prefixes, boolean unrestricted) {

    private static final String WILDCARD = "*";

    public GraphScopeFilter {
        exactIds = exactIds == null ? List.of() : List.copyOf(exactIds);
        prefixes = prefixes == null ? List.of() : List.copyOf(prefixes);
    }

    /**
     * The scope of the UI/GraphQL path and of an account granted {@code *} (all graphs).
     * <p>
     * Named {@code anyGraph} rather than {@code unrestricted} because a record may not
     * declare a static method that shadows its own component accessor.
     */
    public static GraphScopeFilter anyGraph() {
        return new GraphScopeFilter(List.of(), List.of(), true);
    }

    /**
     * Parses a configured scope. See {@link #allows(String)} for the matching rules - parsing and
     * matching intentionally share this one split so a graph can never be visible to a call that
     * would be denied by the gate, or vice versa.
     * <p>
     * Every entry is trimmed first: a value configured as {@code " * "} (YAML list spacing, a
     * shell variable with a stray space) means the wildcard, not the prefix {@code " "}, which
     * would silently grant nothing.
     *
     * @param scope the configured entries; null/empty means no graph at all
     * @return the parsed scope
     */
    public static GraphScopeFilter of(List<String> scope) {
        if (scope == null || scope.isEmpty()) {
            return new GraphScopeFilter(List.of(), List.of(), false);
        }
        List<String> exact = new ArrayList<>();
        List<String> prefixes = new ArrayList<>();
        for (String entry : scope) {
            String trimmed = entry == null ? "" : entry.trim();
            if (trimmed.isEmpty()) {
                continue;
            }
            if (trimmed.equals(WILDCARD)) {
                return anyGraph();
            }
            if (trimmed.endsWith(WILDCARD)) {
                prefixes.add(trimmed.substring(0, trimmed.length() - 1));
            } else {
                exact.add(trimmed);
            }
        }
        return new GraphScopeFilter(exact, prefixes, false);
    }

    /**
     * The single implementation of "may this account touch this graph", used by the call-time
     * gate. Shares its parsing with {@link #of(List)} so the gate and the query can never
     * disagree; on the facade the parsed instance is carried on {@code B2BClientContext} and
     * answered from there, so it is computed once per call rather than once per check.
     *
     * @param graphId the graph the request names
     * @return true only when the account is granted the graph
     */
    public boolean allows(String graphId) {
        if (graphId == null || graphId.isBlank()) {
            return false;
        }
        if (unrestricted()) {
            return true;
        }
        if (exactIds.contains(graphId)) {
            return true;
        }
        for (String prefix : prefixes) {
            if (graphId.startsWith(prefix)) {
                return true;
            }
        }
        return false;
    }
}