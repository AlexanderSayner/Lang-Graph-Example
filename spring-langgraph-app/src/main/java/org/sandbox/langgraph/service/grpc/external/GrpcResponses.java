package org.sandbox.langgraph.service.grpc.external;

import io.grpc.StatusRuntimeException;
import io.grpc.stub.StreamObserver;
import org.sandbox.langgraph.exception.GrpcErrors;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.function.Supplier;

/**
 * How a unary call on the B2B facade reaches its caller: the response, or a mapped status.
 * <p>
 * One rule, two users (the facade's unary handlers and the streaming bridge): <b>nothing may escape
 * a handler</b>. An exception that escapes a gRPC handler is reported by grpc-java as a bare
 * {@code UNKNOWN "Application error processing RPC"}, which silently discards the status and the
 * {@code x-error-code} trailer the contract documents - so even the <i>sends</i> are guarded here.
 * <p>
 * Lives beside the streaming bridge rather than inside the service so both surfaces are subject to
 * the same guard instead of the service owning one copy and the bridge another.
 */
final class GrpcResponses {

    private static final Logger log = LoggerFactory.getLogger(GrpcResponses.class);

    private GrpcResponses() {
    }

    /**
     * Runs one unary handler and guarantees that every failure reaches the caller as a mapped status
     * instead of escaping into grpc-java.
     *
     * @param observer the response observer of the RPC
     * @param body     the handler body: identity, scopes, validation and delegation
     * @param <T>      the response message type
     */
    static <T> void unary(StreamObserver<T> observer, Supplier<T> body) {
        T response;
        try {
            response = body.get();
        } catch (RuntimeException e) {
            error(observer, GrpcErrors.toStatus(e));
            return;
        }
        try {
            observer.onNext(response);
            observer.onCompleted();
        } catch (RuntimeException e) {
            // The call went away between the handler's reply and the write. Nothing is left to
            // report - and letting this throw would turn a perfectly good status into the bare
            // UNKNOWN this class exists to prevent.
            log.debug("Dropping B2B response for a call that went away: {}", e.getMessage());
        }
    }

    /**
     * Sends a terminal error, swallowing the throw of a call that is already closed - cancelled
     * while the handler was still running, or dropped between {@code !isCancelled()} and the send.
     * Without this a mapped status would reach grpc-java as a bare {@code UNKNOWN}.
     *
     * @param observer the response observer of the RPC
     * @param status   the mapped status to report
     */
    static void error(StreamObserver<?> observer, StatusRuntimeException status) {
        try {
            observer.onError(status);
        } catch (RuntimeException e) {
            log.debug("Could not report {} to a call that went away: {}",
                    status.getStatus(), e.getMessage());
        }
    }
}