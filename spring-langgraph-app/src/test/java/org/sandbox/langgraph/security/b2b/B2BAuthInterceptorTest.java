package org.sandbox.langgraph.security.b2b;

import io.grpc.Attributes;
import io.grpc.Metadata;
import io.grpc.MethodDescriptor;
import io.grpc.ServerCall;
import io.grpc.ServerCallHandler;
import io.grpc.Status;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.config.props.B2bGrpcProperties;
import org.sandbox.langgraph.exception.GrpcErrors;

import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The B2B gate. Everything else on the facade assumes this interceptor ran: it
 * either rejects the call outright or puts a {@link B2BClientContext} on the gRPC
 * context that the service handlers then require.
 * <p>
 * Tested against a hand-rolled {@link ServerCall} rather than a transport, so the
 * assertions are about the interceptor's own decisions.
 */
class B2BAuthInterceptorTest {

    private static final String METHOD = "external.v1.LangGraphExternalApi/BuildGraph";
    private static final String KEY = "orchestra-secret-key";

    private final B2BServiceAccounts accounts = accounts(client("orchestra", "orchestra",
            List.of("*"), false));
    private final B2BAuthInterceptor interceptor = new B2BAuthInterceptor(accounts);

    @Test
    @DisplayName("a missing credential is rejected before any handler runs")
    void rejectsMissingKey() {
        RecordingCall call = new RecordingCall();

        interceptor.interceptCall(call, new Metadata(), noOpHandler());

        assertThat(call.closedStatus().getCode()).isEqualTo(Status.Code.UNAUTHENTICATED);
        assertThat(call.closedStatus().getDescription()).contains("Missing credentials");
        // The gate closes a call rather than throwing, so the code travels in the close trailers -
        // a client switching on x-error-code must see it here too, not only on handler denials.
        assertThat(call.closedTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("UNAUTHENTICATED");
    }

    @Test
    @DisplayName("an unknown key is rejected")
    void rejectsUnknownKey() {
        RecordingCall call = new RecordingCall();

        interceptor.interceptCall(call, headers("x-api-key", "wrong"), noOpHandler());

        assertThat(call.closedStatus().getCode()).isEqualTo(Status.Code.UNAUTHENTICATED);
        assertThat(call.closedStatus().getDescription()).isEqualTo("Invalid API key");
        assertThat(call.closedTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("UNAUTHENTICATED");
    }

    @Test
    @DisplayName("x-api-key and a Bearer token both authenticate")
    void acceptsBothCredentialStyles() {
        assertThat(authenticateVia("x-api-key", KEY)).isNotNull();
        assertThat(authenticateVia("authorization", "Bearer " + KEY)).isNotNull();
        // Case-insensitive scheme, surrounding whitespace tolerated.
        assertThat(authenticateVia("authorization", "bearer   " + KEY + "  ")).isNotNull();
    }

    @Test
    @DisplayName("an Authorization header that is not a Bearer token is rejected")
    void rejectsNonBearerAuthorization() {
        RecordingCall call = new RecordingCall();

        interceptor.interceptCall(call, headers("authorization", "Basic dXNlcjpwYXNz"), noOpHandler());

        assertThat(call.closedStatus().getCode()).isEqualTo(Status.Code.UNAUTHENTICATED);
    }

    @Test
    @DisplayName("the authenticated context reaches the handler on the call's context")
    void putsContextOnGrpcContext() {
        AtomicReference<B2BClientContext> seen = new AtomicReference<>();

        interceptor.interceptCall(new RecordingCall(), headers("x-api-key", KEY), contextCapturing(seen));

        assertThat(seen.get()).isNotNull();
        assertThat(seen.get().clientId()).isEqualTo("orchestra");
        // No x-owner-id on this call: the account speaks for itself.
        assertThat(seen.get().audit()).isEqualTo("by 'orchestra'");
        assertThat(seen.get().tenantId()).isEqualTo("orchestra");
    }

    @Test
    @DisplayName("a method outside the account's allow-list is denied")
    void deniesDisallowedMethod() {
        B2BServiceAccounts readOnly = accounts(client("reader", "reader",
                List.of("external.v1.LangGraphExternalApi/ListGraphs"), false));
        RecordingCall call = new RecordingCall();

        new B2BAuthInterceptor(readOnly).interceptCall(call, headers("x-api-key", KEY), noOpHandler());

        assertThat(call.closedStatus().getCode()).isEqualTo(Status.Code.PERMISSION_DENIED);
        assertThat(call.closedStatus().getDescription()).contains("not allowed to call");
        assertThat(call.closedTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("METHOD_NOT_ALLOWED");
    }

    @Test
    @DisplayName("a foreign tenant claim is ignored unless the account may override it")
    void guardsTenantOverride() {
        assertThat(authenticateVia(allHeaders("x-api-key", KEY, "x-tenant-id", "somebody-else")))
                .satisfies(context -> assertThat(context.tenantId()).isEqualTo("orchestra"));

        AtomicReference<B2BClientContext> seen = new AtomicReference<>();
        new B2BAuthInterceptor(accounts(client("orchestra", "orchestra", List.of("*"), true)))
                .interceptCall(new RecordingCall(),
                        allHeaders("x-api-key", KEY, "x-tenant-id", "somebody-else"),
                        contextCapturing(seen));

        assertThat(seen.get().tenantId()).isEqualTo("somebody-else");
    }

    @Test
    @DisplayName("the owner claim is carried for audit")
    void carriesOwnerClaim() {
        assertThat(authenticateVia(allHeaders("x-api-key", KEY, "x-owner-id", "team-a")))
                .satisfies(context -> assertThat(context.ownerId()).isEqualTo("team-a"))
                // Not just carried: quoted by every audit line, which is the point of capturing it.
                .satisfies(context -> assertThat(context.audit())
                        .isEqualTo("by 'orchestra' on behalf of 'team-a'"));
    }

    private B2BClientContext authenticateVia(String key, String value) {
        return authenticateVia(headers(key, value));
    }

    private B2BClientContext authenticateVia(Metadata headers) {
        AtomicReference<B2BClientContext> seen = new AtomicReference<>();
        interceptor.interceptCall(new RecordingCall(), headers, contextCapturing(seen));
        return seen.get();
    }

    private static ServerCallHandler<String, String> contextCapturing(
            AtomicReference<B2BClientContext> seen) {
        return (call, headers) -> {
            seen.set(B2BClientContext.current());
            return new ServerCall.Listener<>() {
            };
        };
    }

    private static ServerCallHandler<String, String> noOpHandler() {
        return (call, headers) -> new ServerCall.Listener<>() {
        };
    }

    private static Metadata headers(String key, String value) {
        return allHeaders(key, value, null, null);
    }

    private static Metadata allHeaders(String key, String value,
                                       String secondKey, String secondValue) {
        Metadata metadata = new Metadata();
        if (key != null) {
            metadata.put(Metadata.Key.of(key, Metadata.ASCII_STRING_MARSHALLER), value);
        }
        if (secondKey != null) {
            metadata.put(Metadata.Key.of(secondKey, Metadata.ASCII_STRING_MARSHALLER), secondValue);
        }
        return metadata;
    }

    private static B2BServiceAccounts accounts(B2bGrpcProperties.Client... clients) {
        B2BServiceAccounts registry = new B2BServiceAccounts(
                new B2bGrpcProperties(null, null, null, null, null, null, null,
                        new B2bGrpcProperties.Security(List.of(clients))));
        registry.init();
        return registry;
    }

    private static B2bGrpcProperties.Client client(String clientId,
                                                   String tenantId,
                                                   List<String> allowedMethods,
                                                   boolean allowTenantOverride) {
        return new B2bGrpcProperties.Client(clientId, tenantId, "", KEY, null,
                allowedMethods, List.of("*"), allowTenantOverride);
    }

    /** Just enough of a ServerCall to observe how the interceptor closes it. */
    private static final class RecordingCall extends ServerCall<String, String> {

        private final MethodDescriptor<String, String> descriptor = MethodDescriptor
                .<String, String>newBuilder()
                .setType(MethodDescriptor.MethodType.UNARY)
                .setFullMethodName(METHOD)
                .setRequestMarshaller(STRING_MARSHALLER)
                .setResponseMarshaller(STRING_MARSHALLER)
                .build();

        private Status closedStatus;
        private Metadata closedTrailers = new Metadata();

        Status closedStatus() {
            return closedStatus;
        }

        Metadata closedTrailers() {
            return closedTrailers;
        }

        @Override
        public void request(int numMessages) {
        }

        @Override
        public void sendHeaders(Metadata headers) {
        }

        @Override
        public void sendMessage(String message) {
        }

        @Override
        public void close(Status status, Metadata trailers) {
            this.closedStatus = status;
            this.closedTrailers = trailers;
        }

        @Override
        public boolean isCancelled() {
            return false;
        }

        @Override
        public Attributes getAttributes() {
            return Attributes.EMPTY;
        }

        @Override
        public MethodDescriptor<String, String> getMethodDescriptor() {
            return descriptor;
        }
    }

    private static final MethodDescriptor.Marshaller<String> STRING_MARSHALLER =
            new MethodDescriptor.Marshaller<>() {
                @Override
                public InputStream stream(String value) {
                    return new ByteArrayInputStream(value.getBytes(StandardCharsets.UTF_8));
                }

                @Override
                public String parse(InputStream stream) {
                    try {
                        return new String(stream.readAllBytes(), StandardCharsets.UTF_8);
                    } catch (java.io.IOException e) {
                        throw new java.io.UncheckedIOException(e);
                    }
                }
            };
}
