package org.sandbox.langgraph.security.b2b;

import java.util.List;

/**
 * One configured B2B caller: a service account with a fixed tenant, an optional
 * owner to act on behalf of, and allow-lists of methods and graphs.
 * <p>
 * The credential itself is never held here - the registry keeps a
 * {@code SHA-256(digest) -> account} map, so a matching lookup only ever compares
 * hashes and a leaked configuration cannot be replayed as-is.
 *
 * @param allowedMethods      fully qualified method names
 *                            (e.g. {@code external.v1.LangGraphExternalApi/BuildGraph}) or a
 *                            single {@code *} wildcard for full access
 * @param allowedGraphs       graph ids this account may build, run, read, cancel and delete:
 *                            exact ids, a trailing-{@code *} prefix, or a single {@code *}
 *                            for every graph. Empty = no graph at all (fail closed), which is
 *                            what an account gets when the operator never set
 *                            {@code allowed-graphs}.
 * @param allowTenantOverride whether the account may claim a foreign tenant through the
 *                            {@code x-tenant-id} metadata
 */
public record B2BServiceAccount(
        String clientId,
        String tenantId,
        String ownerId,
        List<String> allowedMethods,
        List<String> allowedGraphs,
        boolean allowTenantOverride
) {

    private static final String WILDCARD = "*";

    public B2BServiceAccount {
        if (clientId == null || clientId.isBlank()) {
            throw new IllegalArgumentException("b2b.grpc.security.clients[].client-id must not be blank");
        }
        if (tenantId == null || tenantId.isBlank()) {
            throw new IllegalArgumentException(
                    "b2b.grpc.security.clients[" + clientId + "].tenant-id must not be blank");
        }
        if (tenantId.contains(":")) {
            throw new IllegalArgumentException(
                    "b2b.grpc.security.clients[" + clientId + "].tenant-id must not contain ':' "
                            + "(it is the thread-id scope separator)");
        }
        allowedMethods = allowedMethods == null ? List.of(WILDCARD) : List.copyOf(allowedMethods);
        allowedGraphs = allowedGraphs == null ? List.of() : List.copyOf(allowedGraphs);
    }

    /**
     * An empty {@code allowed-methods} still means <b>every</b> method, unlike {@code
     * allowed-graphs} (empty = no graph): the absence of a method allow-list is the "not
     * configured yet" case, and an account that has been onboarded with an explicit empty list is
     * not distinguished from one that was never given a list at all. Documented here because the
     * asymmetry with the graph scope is deliberate: operators turn methods off, but the graph scope
     * is what multi-tenant isolation rests on, so it fails closed.
     *
     * @param method the fully qualified gRPC method name of the running call
     * @return true when the account may call the method
     */
    public boolean permits(String method) {
        return allowedMethods.isEmpty()
                || allowedMethods.contains(WILDCARD)
                || allowedMethods.contains(method);
    }

    /**
     * @param tenant the tenant claimed through the {@code x-tenant-id} metadata
     * @return true when the claim is legitimate for this account
     */
    public boolean permitsTenant(String tenant) {
        if (tenant == null || tenant.isBlank()) {
            return true;
        }
        return tenantId.equals(tenant) || allowTenantOverride;
    }
}
