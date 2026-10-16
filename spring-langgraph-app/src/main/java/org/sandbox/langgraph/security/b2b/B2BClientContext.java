package org.sandbox.langgraph.security.b2b;

import io.grpc.Context;
import io.grpc.StatusRuntimeException;
import org.jspecify.annotations.Nullable;
import org.sandbox.langgraph.dto.pagination.GraphScopeFilter;
import org.sandbox.langgraph.exception.GrpcErrors;

import java.util.List;

/**
 * The identity of the B2B caller for the current gRPC call.
 * <p>
 * It is carried in an {@link io.grpc.Context} key (not in Spring Security's
 * {@code SecurityContextHolder}): a gRPC call never passes through the servlet
 * filter chain, and the gRPC context is the structure that is propagated with
 * the call and cleared when it ends - so an identity can never leak onto a
 * pooled/thread-pool thread the way a ThreadLocal can.
 *
 * @param clientId      the configured service account id (audit + logs)
 * @param tenantId      the scope every thread id is namespaced with
 * @param ownerId       the optional owner the call acts on behalf of (from the
 *                      {@code x-owner-id} metadata), may be blank
 * @param graphScope the <b>parsed</b> graph scope granted to the account, resolved once when the
 *                   interceptor authenticates the call - never re-parsed per check, so the gate
 *                   and the ListGraphs query answer from the very same object. Empty means the
 *                   account may touch no graph at all; enforced by {@link B2BGraphScope} on every
 *                   graph-keyed RPC and pushed into the query by the scoped ListGraphs
 */
public record B2BClientContext(String clientId, String tenantId, String ownerId,
                                GraphScopeFilter graphScope) {

    /**
     * Context key read by the service layer. {@link Context#key(String)} is the
     * gRPC-supported way to pass a value down the call: it is resolved lazily and
     * is correct even for handlers that hop threads inside the same call.
     */
    public static final Context.Key<B2BClientContext> KEY = Context.key("b2b-client");

    private static final String NOT_AUTHENTICATED =
            "No B2B client context: the B2BAuthInterceptor must run on this call";

    /**
     * Builds a context from the account's <i>configured</i> scope, parsing it exactly once - the
     * shape the interceptor uses and the one tests build with, so neither can drift into a
     * hand-rolled filter the gate and the query would then disagree about.
     *
     * @param allowedGraphs the configured {@code allowed-graphs} entries (exact ids, a
     *                      trailing-{@code *} prefix, or {@code *}); null/empty means no graph
     */
    public static B2BClientContext of(String clientId,
                                      String tenantId,
                                      String ownerId,
                                      List<String> allowedGraphs) {
        return new B2BClientContext(clientId, tenantId, ownerId, GraphScopeFilter.of(allowedGraphs));
    }

    /**
     * @return the caller as an audit log line should quote it: {@code by 'client'} for an account
     *         with no {@code x-owner-id} claim, {@code by 'client' on behalf of 'owner'} otherwise
     *         - the owner is captured by the interceptor, so this is what makes it travel
     */
    public String audit() {
        return ownerId == null || ownerId.isBlank()
                ? "by '" + clientId + "'"
                : "by '" + clientId + "' on behalf of '" + ownerId + "'";
    }

    /**
     * @return the context of the running call, or null when the call did not come
     *         in through the B2B facade (for example the internal 9090 server)
     */
    public static @Nullable B2BClientContext current() {
        return KEY.get();
    }

    /**
     * @return the context of the running call
     * @throws StatusRuntimeException {@code UNAUTHENTICATED} when absent, so a
     *         service method reached without the interceptor fails closed instead
     *         of proceeding with a null identity
     */
    public static B2BClientContext require() {
        B2BClientContext context = KEY.get();
        if (context == null) {
            throw GrpcErrors.unauthenticated(NOT_AUTHENTICATED);
        }
        return context;
    }
}
