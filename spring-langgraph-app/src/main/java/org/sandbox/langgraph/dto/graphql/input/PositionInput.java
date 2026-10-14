package org.sandbox.langgraph.dto.graphql.input;

/**
 * Canvas coordinates of a single node.
 * <p>
 * Nested inside {@link NodeInput#position()} so a build carries the layout with the definition it
 * belongs to - no parallel position list that could drift away from {@code nodes}. Optional at the
 * call site: a node without a position simply keeps an empty layout in the {@code coordinates} column.
 */
public record PositionInput(
        double x,

        double y
) {
}