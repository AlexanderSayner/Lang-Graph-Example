package org.sandbox.langgraph.service.grpc.external;

import io.grpc.stub.ServerCallStreamObserver;
import org.sandbox.langgraph.dto.graphql.payload.GraphExecutionEventPayload;
import org.sandbox.langgraph.exception.GrpcErrors;
import org.sandbox.langgraph.grpc.external.GraphExecutionEvent;
import org.sandbox.langgraph.mapper.ExternalApiMapper;
import org.sandbox.langgraph.security.b2b.B2BClientContext;
import org.sandbox.langgraph.service.LangGraphGrpcService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import reactor.core.Disposable;
import reactor.core.publisher.Flux;

import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * The Reactor -&gt; gRPC bridge of {@code ExecuteGraphStream}: one run, one observer, exactly one
 * terminal signal.
 * <p>
 * The internal service hands back a Reactor {@code Flux} over the engine's blocking response
 * iterator; gRPC speaks {@link ServerCallStreamObserver}. The bridge owns the whole handshake so
 * the handler only has to resolve identity, scopes and validation and then call {@link #open}:
 * <ul>
 *   <li>the Flux is subscribed on the calling (gRPC) thread - the upstream call is blocking,
 *       exactly like the GraphQL subscription path;</li>
 *   <li>every event is pushed guarded by {@code isCancelled()}, so a client that vanished never
 *       sees - nor trips over - a late frame;</li>
 *   <li>the terminal signal is guarded by {@code finished}, so the stream always ends with exactly
 *       one of {@code onCompleted}/{@code onError};</li>
 *   <li>every send is itself guarded by {@link GrpcResponses#error}, because an exception escaping a
 *       gRPC handler is reported as a bare {@code UNKNOWN}, losing the {@code x-error-code}
 *       trailer.</li>
 * </ul>
 * <p>
 * <b>Cancellation.</b> gRPC reports a dropped/aborted stream through
 * {@link ServerCallStreamObserver#setOnCancelHandler}, which may only be registered while the call
 * is still being applied - so {@link #open} registers it <i>before</i> subscribing, and nothing is
 * ever pushed before it exists. The engine has no "cancel this stream" RPC, so the handler performs
 * the same cleanup the GraphQL cancel mutation performs: stop consuming, then ask the engine to
 * stop the run for this thread - which is what actually ends the run and its token spend.
 */
final class ExecutionStreamBridge {

    private static final Logger log = LoggerFactory.getLogger(ExecutionStreamBridge.class);

    private final ServerCallStreamObserver<GraphExecutionEvent> observer;
    private final ExternalApiMapper mapper;
    private final LangGraphGrpcService langGraphService;

    /** Flips once, on the first terminal signal (completion, error or client cancel). */
    private final AtomicBoolean finished = new AtomicBoolean(false);

    /** The subscription once it exists; the cancel handler may run before that, hence the box. */
    private final AtomicReference<Disposable> subscriptionRef = new AtomicReference<>();

    ExecutionStreamBridge(ServerCallStreamObserver<GraphExecutionEvent> observer,
                          ExternalApiMapper mapper,
                          LangGraphGrpcService langGraphService) {
        this.observer = observer;
        this.mapper = mapper;
        this.langGraphService = langGraphService;
    }

    /**
     * Reports a failure that happened before any event could flow (identity, scope, validation, or
     * a Flux that could not even be created). Safe to call at any point: only the first terminal
     * signal wins, so a failure racing the stream's own completion changes nothing.
     */
    void fail(Throwable error) {
        if (finished.compareAndSet(false, true) && !observer.isCancelled()) {
            GrpcResponses.error(observer, GrpcErrors.toStatus(error));
        }
    }

    /**
     * Registers the cancellation hook and subscribes to the run. Must be called before any
     * onNext/onCompleted - gRPC only honours the cancel handler while the service call is still
     * being applied, and registering it first also suppresses the "call already cancelled"
     * exception from a racing onNext.
     *
     * @param graphId         the graph being run (engine-side cancel target)
     * @param threadId        the tenant-scoped thread id (engine-side cancel target)
     * @param visibleThreadId the id as the caller sees it (logging)
     * @param client          the authenticated caller (audit in the cancel log line)
     * @param events          the run's event stream from the internal service
     */
    void open(String graphId,
              String threadId,
              String visibleThreadId,
              B2BClientContext client,
              Flux<GraphExecutionEventPayload> events) {
        observer.setOnCancelHandler(
                () -> onClientCancelled(graphId, threadId, visibleThreadId, client));

        Disposable subscription = events.subscribe(this::forward, this::fail, this::complete);
        subscriptionRef.set(subscription);
        // The client may have cancelled before the subscription could be published; the handler
        // then found nothing to dispose.
        if (finished.get()) {
            subscription.dispose();
        }
    }

    private void forward(GraphExecutionEventPayload event) {
        if (finished.get() || observer.isCancelled()) {
            return;
        }
        try {
            observer.onNext(mapper.toExecutionEvent(event));
        } catch (RuntimeException e) {
            // The call went away between the isCancelled() check and onNext; nothing to do - the
            // cancel handler owns the cleanup for that path.
            log.debug("Dropping execution event for a cancelled B2B stream: {}", e.getMessage());
        }
    }

    private void complete() {
        if (finished.compareAndSet(false, true) && !observer.isCancelled()) {
            try {
                observer.onCompleted();
            } catch (RuntimeException e) {
                // Same race as GrpcResponses.error: the call closed between the check and the
                // send, and the stream is already marked finished - there is nothing left to do.
                log.debug("Could not complete a B2B stream the client had already dropped: {}",
                        e.getMessage());
            }
        }
    }

    /**
     * The B2B equivalent of the GraphQL path's local cleanup on cancel.
     * <p>
     * Disposing the subscription is not enough on its own: the upstream iterator keeps the engine's
     * run alive (there is no engine-side cancel for a stream), so the run - and its token spend -
     * would continue until it finished. Asking the engine to cancel the run is what actually stops
     * it, and it is best-effort: a transport failure here must never mask the original cancel.
     */
    private void onClientCancelled(String graphId,
                                   String threadId,
                                   String visibleThreadId,
                                   B2BClientContext client) {
        if (!finished.compareAndSet(false, true)) {
            return;
        }
        log.info("B2B stream cancelled by {} for '{}' (thread '{}')",
                client.audit(), graphId, visibleThreadId);
        Disposable subscription = subscriptionRef.getAndSet(null);
        if (subscription != null) {
            subscription.dispose();
        }
        try {
            langGraphService.cancelGraphExecution(graphId, threadId);
        } catch (RuntimeException e) {
            log.warn("Failed to cancel '{}' (thread '{}') in the engine after a client disconnect: {}",
                    graphId, visibleThreadId, e.getMessage());
        }
    }

}
