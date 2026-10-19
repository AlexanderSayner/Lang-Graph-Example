package org.sandbox.langgraph.exception;

import io.grpc.Metadata;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import jakarta.validation.ConstraintViolationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.concurrent.CompletionException;
import java.util.concurrent.ExecutionException;

/**
 * Maps the service layer's exceptions onto gRPC statuses for the B2B facade.
 * <p>
 * The mapping mirrors {@link GraphQlExceptionHandler} exactly - the same exception type
 * produces the same code on both surfaces, and the {@code extensions.code} the GraphQL
 * layer puts in the response travels as the {@code x-error-code} trailer instead - so a
 * client that already switches on error codes keeps working across both transports:
 * <pre>
 *   ConstraintViolationException -&gt; INVALID_ARGUMENT / VALIDATION_ERROR
 *   IllegalArgumentException     -&gt; INVALID_ARGUMENT / INVALID_ARGUMENT
 *   GraphNotFoundException       -&gt; NOT_FOUND        / GRAPH_NOT_FOUND
 *   GraphBuildException          -&gt; INVALID_ARGUMENT / GRAPH_BUILD_FAILED
 *   GraphStateException          -&gt; INVALID_ARGUMENT / GRAPH_STATE_ERROR
 *   GraphExecutionException      -&gt; INTERNAL         / GRAPH_EXECUTION_FAILED (description redacted)
 *   anything else                -&gt; INTERNAL         / INTERNAL_ERROR         (description redacted)
 * </pre>
 * <p>
 * One deliberate difference: an <i>unexpected</i> failure is reported as a bare
 * {@code INTERNAL "internal error"} and the detail is only logged. The GraphQL
 * layer runs behind the UI's session boundary; the B2B port is a public-ish
 * surface and must not echo stack traces, driver messages or endpoint details.
 */
public final class GrpcErrors {

    private static final Logger log = LoggerFactory.getLogger(GrpcErrors.class);

    /** Trailer carrying the GraphQL-parity error code (e.g. GRAPH_NOT_FOUND). */
    public static final Metadata.Key<String> ERROR_CODE =
            Metadata.Key.of("x-error-code", Metadata.ASCII_STRING_MARSHALLER);

    private static final String INTERNAL_ERROR = "internal error";

    private GrpcErrors() {
    }

    /**
     * @param error the failure raised by the service layer
     * @return a status ready to be handed to {@code responseObserver.onError(...)}
     */
    public static StatusRuntimeException toStatus(Throwable error) {
        Throwable unwrapped = unwrap(error);

        if (unwrapped instanceof StatusRuntimeException statusError) {
            // Already a gRPC status (for example from B2BClientContext.require()).
            return statusError;
        }

        if (unwrapped instanceof LangGraphException langGraphException) {
            Status status = statusOf(langGraphException);
            if (status.getCode() == Status.Code.INTERNAL) {
                // INTERNAL is only ever an unexpected failure (engine, transport, driver), and its
                // message is exactly the shape the redaction rule above exists to keep off this
                // public-ish surface: "Database error: <driver text>",
                // "Service unavailable: <host:port>". Logged - never sent: dropping detail must
                // never mean losing it. The code still travels, so a client that switches on
                // x-error-code keeps working across both transports.
                log.error("B2B failure {}: {}", langGraphException.getErrorCode(),
                        langGraphException.getMessage(), langGraphException);
                return withCode(status, langGraphException.getErrorCode(), INTERNAL_ERROR);
            }
            return withCode(status, langGraphException.getErrorCode(), langGraphException.getMessage());
        }

        if (unwrapped instanceof IllegalArgumentException validation) {
            return withCode(Status.INVALID_ARGUMENT, "INVALID_ARGUMENT",
                    validation.getMessage() != null ? validation.getMessage() : "invalid request");
        }

        if (unwrapped instanceof ConstraintViolationException violations) {
            // Bean validation of the mapped DTOs (the B2B path validates what the GraphQL
            // edge validates), with the message the GraphQL handler would have sent.
            return withCode(Status.INVALID_ARGUMENT, "VALIDATION_ERROR",
                    "Validation failed: " + violations.getMessage());
        }

        // Anything else is a bug on our side: log it, redact it.
        log.error("Unhandled error on the B2B facade: {}", unwrapped.toString(), unwrapped);
        return withCode(Status.INTERNAL, "INTERNAL_ERROR", INTERNAL_ERROR);
    }

    /**
     * A denial that is about authorisation rather than about the request: the caller is
     * known but the graph it named is outside its {@code allowed-graphs} scope (see
     * {@link org.sandbox.langgraph.security.b2b.B2BGraphScope}).
     *
     * @param code        the {@code x-error-code} trailer, e.g. {@code GRAPH_ACCESS_DENIED}
     * @param description shown to the caller; never say whether the graph exists
     * @return a {@code PERMISSION_DENIED} status carrying the code as a trailer
     */
    public static StatusRuntimeException permissionDenied(String code, String description) {
        return withCode(Status.PERMISSION_DENIED, code, description);
    }

    /**
     * A failure this side already knows is ours, whose detail must not travel: the caller gets the
     * same bare {@code INTERNAL "internal error"} as the catch-all branch of {@link #toStatus}, and
     * the detail (a transport this facade cannot serve, an unwired dependency) goes to the log.
     * <p>
     * Used where the mapping needs to happen without an exception to map - e.g. the streaming
     * handler discovering it was handed something other than a
     * {@link io.grpc.stub.ServerCallStreamObserver}, which an unchecked cast would have reported
     * as a bare {@code UNKNOWN} and lost the {@code x-error-code} trailer.
     *
     * @param detail the operator-facing reason; logged, never sent
     * @return {@code INTERNAL} carrying the code as a trailer
     */
    public static StatusRuntimeException internal(String detail) {
        log.error("B2B facade cannot serve this call: {}", detail);
        return withCode(Status.INTERNAL, "INTERNAL_ERROR", INTERNAL_ERROR);
    }

    /**
     * Mirrors {@code GraphQlExceptionHandler}, which maps the same exception types
     * to NOT_FOUND / BAD_REQUEST / BAD_REQUEST / INTERNAL_ERROR.
     */
    private static Status statusOf(LangGraphException error) {
        if (error instanceof LangGraphException.GraphNotFoundException) {
            return Status.NOT_FOUND;
        }
        if (error instanceof LangGraphException.GraphBuildException
                || error instanceof LangGraphException.GraphStateException) {
            return Status.INVALID_ARGUMENT;
        }
        return Status.INTERNAL;
    }

    private static StatusRuntimeException withCode(Status status, String code, String description) {
        return status.withDescription(description).asRuntimeException(trailers(code));
    }

    /**
     * The {@code x-error-code} trailer on its own, for a call the interceptor closes directly -
     * no handler-level mapping runs on those rejections, and a client switching on the code must
     * still see one vocabulary across the whole surface.
     *
     * @param code the value to carry, e.g. {@code UNAUTHENTICATED}
     * @return the trailers to close the call with
     */
    public static Metadata trailers(String code) {
        Metadata trailers = new Metadata();
        trailers.put(ERROR_CODE, code);
        return trailers;
    }

    /**
     * The status of the auth gate: no credential, or a credential that matches no account. Carries
     * {@code x-error-code: UNAUTHENTICATED} so an unauthenticated call reads the same whether it was
     * rejected by {@code B2BAuthInterceptor} or by a handler that found no context.
     *
     * @param description shown to the caller; never echo the presented credential
     * @return a {@code UNAUTHENTICATED} status carrying the code as a trailer
     */
    public static StatusRuntimeException unauthenticated(String description) {
        return Status.UNAUTHENTICATED.withDescription(description).asRuntimeException(trailers("UNAUTHENTICATED"));
    }

    private static Throwable unwrap(Throwable error) {
        Throwable current = error;
        while ((current instanceof CompletionException || current instanceof ExecutionException)
                && current.getCause() != null) {
            current = current.getCause();
        }
        return current;
    }
}
