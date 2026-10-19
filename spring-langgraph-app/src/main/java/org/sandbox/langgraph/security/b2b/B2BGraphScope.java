package org.sandbox.langgraph.security.b2b;

import org.sandbox.langgraph.dto.pagination.GraphScopeFilter;
import org.sandbox.langgraph.exception.GrpcErrors;
import org.springframework.stereotype.Component;

/**
 * Graph scoping of B2B calls.
 * <p>
 * Threads are namespaced by {@link B2BThreadScope}, but the graph a call addresses is
 * chosen by the caller, and {@code core.graphs} carries no owner or tenant column - so
 * without this check any authenticated service account could build over, run, read,
 * cancel or <b>delete</b> any other tenant's graph by simply naming its id. That is the
 * graph-level twin of the thread-level IDOR: {@code DeleteGraph} is the sharp edge.
 * <p>
 * The scope is the account's {@code allowed-graphs} configuration, carried on
 * {@link B2BClientContext} by {@link B2BAuthInterceptor} (one source of truth: the same
 * parsed filter decides both what a request may touch and which rows {@code ListGraphs}
 * may return, so the gate and the query can never disagree).
 * <p>
 * Fails closed: an account with no configured scope may touch nothing, and a blank
 * {@code graph_id} is a contract violation rather than a permission question.
 */
@Component
public class B2BGraphScope {

    /**
     * Rejects a call that names a graph outside the caller's scope.
     *
     * @param context the authenticated caller
     * @param graphId the graph named by the request
     * @throws IllegalArgumentException when {@code graphId} is blank (mapped to
     *                                  {@code INVALID_ARGUMENT} by {@link GrpcErrors})
     * @throws io.grpc.StatusRuntimeException {@code PERMISSION_DENIED} with the
     *                                  {@code x-error-code: GRAPH_ACCESS_DENIED} trailer
     *                                  when the graph is outside the caller's scope
     */
    public void requireAccess(B2BClientContext context, String graphId) {
        if (graphId == null || graphId.isBlank()) {
            throw new IllegalArgumentException("graph_id is required");
        }
        if (!allows(context, graphId)) {
            throw GrpcErrors.permissionDenied("GRAPH_ACCESS_DENIED",
                    "Client '" + context.clientId() + "' is not allowed to access graph '" + graphId + "'");
        }
    }

    /**
     * @return the scope as the persistence layer needs it - {@code anyGraph()} for an
     *         account granted {@code *}, otherwise the exact ids and prefixes to push
     *         into the query
     */
    public GraphScopeFilter filter(B2BClientContext context) {
        return context.graphScope();
    }

    /** @return whether the caller's granted scope contains the graph */
    public static boolean allows(B2BClientContext context, String graphId) {
        return context.graphScope().allows(graphId);
    }
}