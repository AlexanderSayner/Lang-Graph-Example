package org.sandbox.langgraph.security.b2b;

import io.grpc.Context;
import io.grpc.Contexts;
import io.grpc.Grpc;
import io.grpc.Metadata;
import io.grpc.ServerCall;
import io.grpc.ServerCallHandler;
import io.grpc.ServerInterceptor;
import io.grpc.Status;
import org.jspecify.annotations.Nullable;
import org.sandbox.langgraph.exception.GrpcErrors;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.net.SocketAddress;

/**
 * Authenticates every call on the B2B gRPC port.
 * <p>
 * The identity is placed on the gRPC {@link Context} (never on Spring Security's
 * {@code SecurityContextHolder}: no gRPC call ever crosses the servlet filter
 * chain) and is therefore visible to the service handler of this very call, and
 * nowhere else. Rejections never reach a service handler - the call is closed
 * with a {@code UNAUTHENTICATED}/{@code PERMISSION_DENIED} status.
 * <p>
 * Bound to the B2B service definition only (see {@code B2bGrpcServerConfig}),
 * deliberately <b>not</b> registered as a global interceptor: the internal 9090
 * server serves the Python engine's {@code ToolService}/{@code JavaGraphProvider}
 * callbacks, which carry no API key.
 */
@Component
@ConditionalOnProperty(prefix = "b2b.grpc", name = "enabled", havingValue = "true", matchIfMissing = true)
public class B2BAuthInterceptor implements ServerInterceptor {

    private static final Logger log = LoggerFactory.getLogger(B2BAuthInterceptor.class);

    static final Metadata.Key<String> API_KEY =
            Metadata.Key.of("x-api-key", Metadata.ASCII_STRING_MARSHALLER);
    static final Metadata.Key<String> AUTHORIZATION =
            Metadata.Key.of("authorization", Metadata.ASCII_STRING_MARSHALLER);
    static final Metadata.Key<String> TENANT_ID =
            Metadata.Key.of("x-tenant-id", Metadata.ASCII_STRING_MARSHALLER);
    static final Metadata.Key<String> OWNER_ID =
            Metadata.Key.of("x-owner-id", Metadata.ASCII_STRING_MARSHALLER);

    private static final String BEARER_PREFIX = "bearer ";

    private final B2BServiceAccounts accounts;

    public B2BAuthInterceptor(B2BServiceAccounts accounts) {
        this.accounts = accounts;
    }

    @Override
    public <ReqT, RespT> ServerCall.Listener<ReqT> interceptCall(ServerCall<ReqT, RespT> call,
                                                                 Metadata headers,
                                                                 ServerCallHandler<ReqT, RespT> next) {
        String method = call.getMethodDescriptor().getFullMethodName();
        String presented = extractKey(headers);

        if (presented == null) {
            return reject(call, Status.UNAUTHENTICATED.withDescription(
                    "Missing credentials: send 'x-api-key' or 'authorization: Bearer <key>'"),
                    "UNAUTHENTICATED");
        }

        B2BServiceAccount account = accounts.authenticate(presented).orElse(null);
        if (account == null) {
            // Never log the presented key itself - only the fact of the rejection.
            log.warn("Rejected B2B call '{}' from {}: invalid API key", method, remoteAddress(call));
            return reject(call, Status.UNAUTHENTICATED.withDescription("Invalid API key"),
                    "UNAUTHENTICATED");
        }

        if (!account.permits(method)) {
            log.warn("B2B client '{}' is not allowed to call '{}'", account.clientId(), method);
            return reject(call, Status.PERMISSION_DENIED.withDescription(
                    "Client '" + account.clientId() + "' is not allowed to call " + method),
                    "METHOD_NOT_ALLOWED");
        }

        B2BClientContext context = B2BClientContext.of(
                account.clientId(),
                resolveTenant(headers, account),
                normalize(headers.get(OWNER_ID)),
                account.allowedGraphs());

        return Contexts.interceptCall(Context.current().withValue(B2BClientContext.KEY, context),
                call, headers, next);
    }

    /**
     * @return the raw key from {@code x-api-key}, else the token of an
     *         {@code Authorization: Bearer <token>} header; null when absent or blank
     */
    private static @Nullable String extractKey(Metadata headers) {
        String apiKey = normalize(headers.get(API_KEY));
        if (apiKey != null) {
            return apiKey;
        }
        String authorization = normalize(headers.get(AUTHORIZATION));
        if (authorization == null) {
            return null;
        }
        return authorization.regionMatches(true, 0, BEARER_PREFIX, 0, BEARER_PREFIX.length())
                ? normalize(authorization.substring(BEARER_PREFIX.length()))
                : null;
    }

    /**
     * A foreign tenant claim is only honoured when the account allows overrides;
     * the account's own tenant always wins otherwise.
     */
    private String resolveTenant(Metadata headers, B2BServiceAccount account) {
        String claimed = normalize(headers.get(TENANT_ID));
        if (claimed == null) {
            return account.tenantId();
        }
        if (account.permitsTenant(claimed)) {
            return claimed;
        }
        log.warn("B2B client '{}' tried to claim tenant '{}', falling back to '{}'",
                account.clientId(), claimed, account.tenantId());
        return account.tenantId();
    }

    private static @Nullable String normalize(@Nullable String value) {
        if (value == null) {
            return null;
        }
        String trimmed = value.trim();
        return trimmed.isEmpty() ? null : trimmed;
    }

    private static String remoteAddress(ServerCall<?, ?> call) {
        SocketAddress address = call.getAttributes().get(Grpc.TRANSPORT_ATTR_REMOTE_ADDR);
        return address != null ? address.toString() : "unknown";
    }

    /**
     * Closes the call with a mapped status and the GraphQL-parity code as a trailer: an auth
     * rejection is a closed call rather than a thrown exception, so the code has to travel with
     * the close instead of through {@link org.sandbox.langgraph.exception.GrpcErrors#toStatus}.
     */
    private static <ReqT, RespT> ServerCall.Listener<ReqT> reject(ServerCall<ReqT, RespT> call,
                                                                  Status status,
                                                                  String errorCode) {
        call.close(status, GrpcErrors.trailers(errorCode));
        return new ServerCall.Listener<>() {
        };
    }
}
