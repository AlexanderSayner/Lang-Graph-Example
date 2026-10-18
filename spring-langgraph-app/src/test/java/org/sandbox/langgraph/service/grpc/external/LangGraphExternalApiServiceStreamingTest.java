package org.sandbox.langgraph.service.grpc.external;

import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.sandbox.langgraph.core.model.ExecutionEventType;
import org.sandbox.langgraph.dto.graphql.payload.GraphExecutionEventPayload;
import org.sandbox.langgraph.exception.GrpcErrors;
import org.sandbox.langgraph.exception.LangGraphException;
import org.sandbox.langgraph.grpc.external.ExecuteGraphRequest;
import org.sandbox.langgraph.grpc.external.GraphExecutionEvent;
import org.sandbox.langgraph.mapper.ExternalApiMapper;
import org.sandbox.langgraph.security.b2b.B2BClientContext;
import org.sandbox.langgraph.security.b2b.B2BGraphScope;
import org.sandbox.langgraph.security.b2b.B2BThreadScope;
import org.sandbox.langgraph.service.CopilotService;
import org.sandbox.langgraph.service.GraphExecutionAggregator;
import org.sandbox.langgraph.service.LangGraphGrpcService;
import jakarta.validation.Validator;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Sinks;

import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * The Reactor -> gRPC bridge and, most importantly, what happens when the B2B
 * client goes away mid-run.
 * <p>
 * The engine has no "cancel this stream" RPC: dropping the local subscription
 * alone leaves the run alive on the Python side, so the cancel handler must also
 * ask the engine to stop - exactly what the GraphQL cancel mutation does.
 */
class LangGraphExternalApiServiceStreamingTest {

    private final LangGraphGrpcService langGraphService = mock(LangGraphGrpcService.class);
    private final CopilotService copilotService = mock(CopilotService.class);
    private final LangGraphExternalApiService service = new LangGraphExternalApiService(
            langGraphService, copilotService, new GraphExecutionAggregator(),
            new ExternalApiMapper(), new B2BThreadScope(), new B2BGraphScope(), mock(Validator.class));

    /** Granted every graph, so the tests below are not about the graph scope. */
    private static final B2BClientContext CLIENT =
            B2BClientContext.of("orchestra", "orchestra", "", List.of("*"));

    @Test
    @DisplayName("events are forwarded in order and the stream completes once")
    void forwardsEventsInOrder() {
        when(langGraphService.executeGraphStream(any(), anyBoolean())).thenReturn(Flux.just(
                event(ExecutionEventType.START),
                event(ExecutionEventType.NODE_END),
                event(ExecutionEventType.END)));
        RecordingServerCallStreamObserver observer = new RecordingServerCallStreamObserver();

        call(observer);

        assertThat(observer.outputs).extracting(GraphExecutionEvent::getEventType)
                .containsExactly(
                        org.sandbox.langgraph.grpc.external.ExecutionEventType.EXECUTION_EVENT_TYPE_START,
                        org.sandbox.langgraph.grpc.external.ExecutionEventType.EXECUTION_EVENT_TYPE_NODE_END,
                        org.sandbox.langgraph.grpc.external.ExecutionEventType.EXECUTION_EVENT_TYPE_END);
        assertThat(observer.completed).isTrue();
        assertThat(observer.errors).isEmpty();
    }

    @Test
    @DisplayName("the caller's bare thread id is scoped to its tenant before it leaves Java")
    void scopesThreadIdForTheEngine() {
        when(langGraphService.executeGraphStream(any(), anyBoolean())).thenReturn(Flux.empty());

        call(new RecordingServerCallStreamObserver());

        Mockito.verify(langGraphService).executeGraphStream(
                argumentThat(input -> assertThat(input.threadId()).isEqualTo("orchestra:abc")), anyBoolean());
    }

    @Test
    @DisplayName("a graph-not-found failure becomes NOT_FOUND with the GraphQL-parity code")
    void mapsFailuresToStatus() {
        when(langGraphService.executeGraphStream(any(), anyBoolean()))
                .thenReturn(Flux.error(new LangGraphException.GraphNotFoundException("graph-1")));
        RecordingServerCallStreamObserver observer = new RecordingServerCallStreamObserver();

        call(observer);

        assertThat(observer.completed).isFalse();
        assertThat(observer.errors).hasSize(1);
        StatusRuntimeException error = observer.errors.get(0);
        assertThat(error.getStatus().getCode()).isEqualTo(Status.Code.NOT_FOUND);
        assertThat(error.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("GRAPH_NOT_FOUND");
    }

    @Test
    @DisplayName("a cancelled stream disposes the subscription and stops the run in the engine")
    void cancellingTheStreamStopsTheEngineRun() {
        Sinks.Many<GraphExecutionEventPayload> sink = Sinks.many().unicast().onBackpressureBuffer();
        when(langGraphService.executeGraphStream(any(), anyBoolean())).thenReturn(sink.asFlux());
        RecordingServerCallStreamObserver observer = new RecordingServerCallStreamObserver();

        call(observer);
        assertThat(sink.currentSubscriberCount()).isEqualTo(1);

        // What gRPC invokes when the B2B client drops/aborts the stream.
        observer.cancelHandler.run();

        assertThat(sink.currentSubscriberCount())
                .as("the local subscription must be disposed")
                .isZero();
        // The engine keeps running until it is told to stop; the dispose alone is
        // not enough (there is no engine-side cancel for a stream).
        verify(langGraphService).cancelGraphExecution("graph-1", "orchestra:abc");
        assertThat(observer.completed)
                .as("a cancelled stream must not also complete")
                .isFalse();
    }

    @Test
    @DisplayName("nothing is pushed after the client cancelled")
    void stopsEmittingAfterCancel() {
        Sinks.Many<GraphExecutionEventPayload> sink = Sinks.many().unicast().onBackpressureBuffer();
        when(langGraphService.executeGraphStream(any(), anyBoolean())).thenReturn(sink.asFlux());
        RecordingServerCallStreamObserver observer = new RecordingServerCallStreamObserver();

        call(observer);
        observer.cancelHandler.run();
        sink.tryEmitNext(event(ExecutionEventType.NODE_END));

        assertThat(observer.outputs).isEmpty();
        assertThat(observer.completed).isFalse();
        assertThat(observer.errors).isEmpty();
    }

    @Test
    @DisplayName("a stream reached without the auth interceptor fails closed as a status")
    void failsClosedWithoutClientContext() {
        RecordingServerCallStreamObserver observer = new RecordingServerCallStreamObserver();

        service.executeGraphStream(request(), observer);

        // Must arrive through onError, not as an escaping exception: on the wire grpc-java
        // would report an escaped throwable as UNKNOWN and drop the documented code.
        assertThat(observer.outputs).isEmpty();
        assertThat(observer.completed).isFalse();
        assertThat(observer.errors).singleElement()
                .satisfies(error -> assertThat(error.getStatus().getCode())
                        .isEqualTo(Status.Code.UNAUTHENTICATED));
        assertThat(observer.cancelHandler).isNull();
        verify(langGraphService, never()).executeGraphStream(any(), anyBoolean());
    }

    @Test
    @DisplayName("a blank thread id fails the stream with INVALID_ARGUMENT before subscribing")
    void rejectsBlankThreadIdBeforeSubscribing() {
        RecordingServerCallStreamObserver observer = new RecordingServerCallStreamObserver();

        io.grpc.Context.current().withValue(B2BClientContext.KEY, CLIENT).run(
                () -> service.executeGraphStream(ExecuteGraphRequest.newBuilder()
                        .setGraphId("graph-1").setThreadId("").setInput("hello").build(), observer));

        assertThat(observer.errors).singleElement().satisfies(error -> {
            assertThat(error.getStatus().getCode()).isEqualTo(Status.Code.INVALID_ARGUMENT);
            assertThat(error.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("INVALID_ARGUMENT");
        });
        assertThat(observer.cancelHandler).as("nothing is registered once the call failed").isNull();
        verify(langGraphService, never()).executeGraphStream(any(), anyBoolean());
    }

    @Test
    @DisplayName("a graph outside the account scope never reaches the engine")
    void refusesGraphOutsideTheAccountScope() {
        B2BClientContext scoped = B2BClientContext.of("acme", "acme", "", List.of("acme-*"));
        RecordingServerCallStreamObserver observer = new RecordingServerCallStreamObserver();

        io.grpc.Context.current().withValue(B2BClientContext.KEY, scoped).run(
                () -> service.executeGraphStream(ExecuteGraphRequest.newBuilder()
                        .setGraphId("other-tenant-graph").setThreadId("abc").setInput("hello").build(),
                        observer));

        assertThat(observer.outputs).isEmpty();
        assertThat(observer.completed).isFalse();
        assertThat(observer.errors).singleElement().satisfies(error -> {
            assertThat(error.getStatus().getCode()).isEqualTo(Status.Code.PERMISSION_DENIED);
            assertThat(error.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("GRAPH_ACCESS_DENIED");
        });
        verify(langGraphService, never()).executeGraphStream(any(), anyBoolean());
        verify(langGraphService, never()).cancelGraphExecution(any(), any());
    }

    /** Runs the service method on the gRPC context the interceptor would have set. */
    private void call(RecordingServerCallStreamObserver observer) {
        io.grpc.Context.current().withValue(B2BClientContext.KEY, CLIENT)
                .run(() -> service.executeGraphStream(request(), observer));
    }

    private static ExecuteGraphRequest request() {
        return ExecuteGraphRequest.newBuilder()
                .setGraphId("graph-1")
                .setThreadId("abc")
                .setInput("hello")
                .build();
    }

    private static GraphExecutionEventPayload event(ExecutionEventType type) {
        return new GraphExecutionEventPayload(type, "node_1", "out", Map.of("k", "v"), 1L, 3, null);
    }

    /**
     * Mockito matchers are typed as the argument type, hence the generic return.
     */
    private static <T> T argumentThat(Consumer<T> assertion) {
        return org.mockito.ArgumentMatchers.argThat(input -> {
            assertion.accept(input);
            return true;
        });
    }
}
