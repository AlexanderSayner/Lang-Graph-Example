package org.sandbox.langgraph.security.b2b;

import org.jspecify.annotations.Nullable;
import org.springframework.stereotype.Component;

/**
 * Keeps B2B threads inside the caller's tenant.
 * <p>
 * A thread is the address of everything a graph run produces: its checkpoint in
 * the engine (keyed by {@code graph_id + thread_id}), its state reads, its
 * history and its cancellation. Nothing in the engine or in {@code core.graphs}
 * owns a thread, so a client that could send an arbitrary {@code thread_id}
 * would be able to read and cancel another tenant's conversation - an IDOR.
 * <p>
 * The facade therefore treats {@code <tenantId>:<rest>} as the canonical
 * internal thread id. The separator is {@code ':'} because it may not appear in
 * a tenant id ({@code B2BServiceAccount} rejects such configurations), which
 * makes the split unambiguous.
 */
@Component
public class B2BThreadScope {

    static final String SEPARATOR = ":";

    /**
     * Normalises a caller-supplied thread id into the tenant-scoped form the
     * engine stores.
     *
     * @param context  the authenticated caller
     * @param threadId the id from the request; required
     * @return the scoped id, either the caller's own prefixed id or
     *         {@code <tenantId>:<threadId>}
     * @throws IllegalArgumentException when the id is blank or namespaced with
     *                                  another tenant's prefix - the caller is told
     *                                  the id is out of scope, never what it points at
     */
    public String toInternal(B2BClientContext context, @Nullable String threadId) {
        if (threadId == null || threadId.isBlank()) {
            throw new IllegalArgumentException("thread_id is required");
        }
        String prefix = context.tenantId() + SEPARATOR;
        if (threadId.startsWith(prefix)) {
            return threadId;
        }
        if (threadId.contains(SEPARATOR)) {
            throw new IllegalArgumentException("thread_id is not scoped to this tenant");
        }
        return prefix + threadId;
    }

    /**
     * Strips the caller's own tenant prefix from an id on the way out, so a client
     * that started a thread as {@code "abc"} sees {@code "abc"} again.
     *
     * @return the caller-visible id; null stays null
     */
    public String toExternal(B2BClientContext context, @Nullable String threadId) {
        if (threadId == null) {
            return null;
        }
        String prefix = context.tenantId() + SEPARATOR;
        return threadId.startsWith(prefix) ? threadId.substring(prefix.length()) : threadId;
    }
}
