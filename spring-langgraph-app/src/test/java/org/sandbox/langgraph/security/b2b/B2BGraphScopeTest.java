package org.sandbox.langgraph.security.b2b;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.dto.pagination.GraphScopeFilter;
import org.sandbox.langgraph.exception.GrpcErrors;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatIllegalArgumentException;
import static org.assertj.core.api.Assertions.catchThrowable;

/**
 * Graph scoping of B2B calls.
 * <p>
 * The account's {@code allowed-graphs} is the only thing standing between one B2B
 * tenant and another's graphs (core.graphs has no owner column), so the rules here
 * are the rules {@code DeleteGraph}, {@code ExecuteGraph} and every other graph-keyed
 * RPC inherit - and the same parsing feeds {@code ListGraphs}, so a graph can never be
 * listed but not usable, or usable but not listed.
 */
class B2BGraphScopeTest {

    private final B2BGraphScope scope = new B2BGraphScope();

    private static B2BClientContext client(List<String> allowedGraphs) {
        return B2BClientContext.of("acme", "acme", "", allowedGraphs);
    }

    @Test
    @DisplayName("an exact id in the scope is allowed, any other id is denied")
    void matchesExactIds() {
        B2BClientContext context = client(List.of("agent-v1", "agent-v2"));

        assertThat(B2BGraphScope.allows(context, "agent-v1")).isTrue();
        assertThat(B2BGraphScope.allows(context, "agent-v3")).isFalse();
    }

    @Test
    @DisplayName("a trailing-* entry grants everything after the text it precedes")
    void matchesPrefixes() {
        B2BClientContext context = client(List.of("acme-*"));

        // "acme-*" -> prefix "acme-": the '-' is part of the grant, which is what stops
        // the grant from bleeding into a tenant whose id merely starts with "acme".
        assertThat(B2BGraphScope.allows(context, "acme-agent")).isTrue();
        assertThat(B2BGraphScope.allows(context, "acme-agent-v2")).isTrue();
        assertThat(B2BGraphScope.allows(context, "acme")).isFalse();
        assertThat(B2BGraphScope.allows(context, "acme2-agent")).isFalse();
        assertThat(B2BGraphScope.allows(context, "other")).isFalse();
    }

    @Test
    @DisplayName("* grants every graph (the single-tenant default)")
    void wildcardGrantsEverything() {
        assertThat(B2BGraphScope.allows(client(List.of("*")), "anything")).isTrue();
    }

    @Test
    @DisplayName("an account with no scope may touch no graph at all - fail closed")
    void emptyScopeDeniesEverything() {
        assertThat(B2BGraphScope.allows(client(List.of()), "agent-v1")).isFalse();
        assertThat(B2BGraphScope.allows(client(null), "agent-v1")).isFalse();
        assertThat(scope.filter(client(List.of())).unrestricted()).isFalse();
    }

    @Test
    @DisplayName("a blank graph id is a contract violation, not a permission question")
    void blankGraphIdIsInvalidArgument() {
        assertThatIllegalArgumentException()
                .isThrownBy(() -> scope.requireAccess(client(List.of("*")), ""))
                .withMessage("graph_id is required");
        assertThatIllegalArgumentException()
                .isThrownBy(() -> scope.requireAccess(client(List.of("*")), null));
    }

    @Test
    @DisplayName("a denial is PERMISSION_DENIED carrying GRAPH_ACCESS_DENIED, not NOT_FOUND")
    void denialCarriesTheDocumentedTrailer() {
        Throwable denial = catchThrowable(() ->
                scope.requireAccess(client(List.of("acme-*")), "other-tenant-graph"));

        assertThat(denial).isInstanceOf(io.grpc.StatusRuntimeException.class);
        io.grpc.StatusRuntimeException status = (io.grpc.StatusRuntimeException) denial;
        assertThat(status.getStatus().getCode()).isEqualTo(io.grpc.Status.Code.PERMISSION_DENIED);
        assertThat(status.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("GRAPH_ACCESS_DENIED");
        // Never hint at existence: the caller is told it is out of scope, nothing more.
        assertThat(status.getStatus().getDescription()).contains("not allowed to access");
    }

    @Test
    @DisplayName("an in-scope graph passes the gate without a status")
    void inScopeGraphPasses() {
        assertThat(catchThrowable(() -> scope.requireAccess(client(List.of("acme-*")), "acme-agent")))
                .isNull();
    }

    @Test
    @DisplayName("the filter handed to the query is parsed exactly like the gate decides")
    void filterParsingMatchesTheGate() {
        GraphScopeFilter exact = scope.filter(client(List.of("a", "b-*")));

        assertThat(exact.unrestricted()).isFalse();
        assertThat(exact.exactIds()).containsExactly("a");
        assertThat(exact.prefixes()).containsExactly("b-");
        assertThat(exact.allows("a")).isTrue();
        assertThat(exact.allows("b-1")).isTrue();
        assertThat(exact.allows("b")).isFalse();
        assertThat(exact.allows("c")).isFalse();

        assertThat(scope.filter(client(List.of("*"))).unrestricted()).isTrue();
    }
}