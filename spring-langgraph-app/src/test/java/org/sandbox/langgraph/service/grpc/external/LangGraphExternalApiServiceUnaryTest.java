package org.sandbox.langgraph.service.grpc.external;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.sandbox.langgraph.core.model.ExecutionEventType;
import org.sandbox.langgraph.dto.graphql.input.BuildGraphInput;
import org.sandbox.langgraph.dto.graphql.payload.BuildGraphPayload;
import org.sandbox.langgraph.dto.graphql.payload.CancelGraphExecutionPayload;
import org.sandbox.langgraph.dto.graphql.payload.CopilotGraphPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphExecutionEventPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphStatePayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphListPayload;
import org.sandbox.langgraph.dto.pagination.GraphScopeFilter;
import org.sandbox.langgraph.exception.GrpcErrors;
import org.sandbox.langgraph.grpc.external.AskCopilotRequest;
import org.sandbox.langgraph.grpc.external.BuildGraphRequest;
import org.sandbox.langgraph.grpc.external.CancelExecutionRequest;
import org.sandbox.langgraph.grpc.external.DeleteGraphRequest;
import org.sandbox.langgraph.grpc.external.DeleteGraphResponse;
import org.sandbox.langgraph.grpc.external.ExecuteGraphRequest;
import org.sandbox.langgraph.grpc.external.GraphExecutionEvent;
import org.sandbox.langgraph.grpc.external.AskCopilotResponse;
import org.sandbox.langgraph.grpc.external.BuildGraphResponse;
import org.sandbox.langgraph.grpc.external.CancelExecutionResponse;
import org.sandbox.langgraph.grpc.external.ExecuteGraphResponse;
import org.sandbox.langgraph.grpc.external.GetGraphStateResponse;
import org.sandbox.langgraph.grpc.external.ListGraphsResponse;
import org.sandbox.langgraph.grpc.external.GetGraphStateRequest;
import org.sandbox.langgraph.grpc.external.ListGraphsRequest;
import org.sandbox.langgraph.grpc.external.NodeDefinition;
import org.sandbox.langgraph.grpc.external.NodeType;
import org.sandbox.langgraph.mapper.ExternalApiMapper;
import org.sandbox.langgraph.security.b2b.B2BClientContext;
import org.sandbox.langgraph.security.b2b.B2BGraphScope;
import org.sandbox.langgraph.security.b2b.B2BThreadScope;
import org.sandbox.langgraph.service.CopilotService;
import org.sandbox.langgraph.service.GraphExecutionAggregator;
import org.sandbox.langgraph.service.LangGraphGrpcService;
import jakarta.validation.Validator;
import io.grpc.stub.StreamObserver;
import reactor.core.publisher.Flux;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * The unary half of the B2B facade: delegation, tenant scoping, the page-size
 * clamp and the fail-closed behaviour when no client context is present.
 */
class LangGraphExternalApiServiceUnaryTest {

    private final LangGraphGrpcService langGraphService = mock(LangGraphGrpcService.class);
    private final CopilotService copilotService = mock(CopilotService.class);
    private final LangGraphExternalApiService service = new LangGraphExternalApiService(
            langGraphService, copilotService, new GraphExecutionAggregator(),
            new ExternalApiMapper(), new B2BThreadScope(), new B2BGraphScope(), mock(Validator.class));

    /** Granted every graph, so the delegation tests below are not about the scope. */
    private static final B2BClientContext CLIENT =
            B2BClientContext.of("orchestra", "orchestra", "", List.of("*"));

    @Test
    @DisplayName("BuildGraph never forwards a UI position")
    void buildGraphDropsUiPosition() {
        when(langGraphService.buildGraph(any())).thenReturn(new BuildGraphPayload(true, "g1", "built"));
        var observer = new RecordingObserver<BuildGraphResponse>();

        call(() -> service.buildGraph(BuildGraphRequest.newBuilder()
                .setGraphId("g1")
                .setGraphName("Graph one")
                .addNodes(NodeDefinition.newBuilder()
                        .setNodeId("node_1")
                        .setNodeType(NodeType.NODE_TYPE_ACTION)
                        .setHandlerName("act")
                        .build())
                .build(), observer));

        ArgumentCaptor<BuildGraphInput> captor = ArgumentCaptor.forClass(BuildGraphInput.class);
        verify(langGraphService).buildGraph(captor.capture());
        assertThat(captor.getValue().nodes()).hasSize(1);
        assertThat(captor.getValue().nodes().get(0).position())
                .as("the canvas layout must not be touched through the facade")
                .isNull();
        assertThat(observer.completed).isTrue();
        assertThat(observer.outputs).hasSize(1);
    }

    @Test
    @DisplayName("an ExecuteGraph run with no events is an empty success, not an error")
    void executeGraphWithEmptyRun() {
        when(langGraphService.executeGraphStream(any(), anyBoolean())).thenReturn(Flux.empty());
        var observer = new RecordingObserver<ExecuteGraphResponse>();

        call(() -> service.executeGraph(ExecuteGraphRequest.newBuilder()
                .setGraphId("g1").setThreadId("abc").setInput("hi").build(), observer));

        assertThat(observer.errors).isEmpty();
        assertThat(observer.completed).isTrue();
        assertThat((observer.outputs.get(0)).getSuccess()).isTrue();
        assertThat((observer.outputs.get(0)).getOutput()).isEmpty();
    }

    @Test
    @DisplayName("a blocking run is folded into one response")
    void executeGraphAggregatesTheRun() {
        when(langGraphService.executeGraphStream(any(), anyBoolean())).thenReturn(Flux.just(
                new GraphExecutionEventPayload(ExecutionEventType.NODE_END, "n", "answer", null, 1L, 5, null),
                new GraphExecutionEventPayload(ExecutionEventType.END, "n", "", null, 2L, 5, null)));
        var observer = new RecordingObserver<ExecuteGraphResponse>();

        call(() -> service.executeGraph(ExecuteGraphRequest.newBuilder()
                .setGraphId("g1").setThreadId("abc").setInput("hi").build(), observer));

        ExecuteGraphResponse response = observer.outputs.get(0);
        assertThat(response.getSuccess()).isTrue();
        assertThat(response.getOutput()).isEqualTo("answer");
        assertThat(response.getTotalTokens()).isEqualTo(5);
        assertThat(response.getEventType())
                .isEqualTo(org.sandbox.langgraph.grpc.external.ExecutionEventType.EXECUTION_EVENT_TYPE_END);
    }

    @Test
    @DisplayName("state reads address the engine with the tenant-scoped thread id")
    void scopesStateReads() {
        when(langGraphService.getGraphState(any(), any()))
                .thenReturn(new GraphStatePayload(true, Map.of("k", "v"), "node", List.of()));
        var observer = new RecordingObserver<GetGraphStateResponse>();

        call(() -> service.getGraphState(GetGraphStateRequest.newBuilder()
                .setGraphId("g1").setThreadId("abc").build(), observer));

        verify(langGraphService).getGraphState("g1", "orchestra:abc");
        assertThat(observer.completed).isTrue();
    }

    @Test
    @DisplayName("cancelling a run that was not running stays a success")
    void cancelExecutionWithoutActiveRun() {
        when(langGraphService.cancelGraphExecution(any(), any()))
                .thenReturn(new CancelGraphExecutionPayload(true, false, "No active execution found"));
        var observer = new RecordingObserver<CancelExecutionResponse>();

        call(() -> service.cancelExecution(CancelExecutionRequest.newBuilder()
                .setGraphId("g1").setThreadId("abc").build(), observer));

        var response = observer.outputs.get(0);
        assertThat(response.getSuccess()).isTrue();
        assertThat(response.getCancelled()).isFalse();
    }

    @Test
    @DisplayName("an empty copilot chat history reaches the copilot service as null, not \"\"")
    void normalisesBlankCopilotFields() {
        when(copilotService.askCopilot(any(), any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new CopilotGraphPayload(true, "hi", 1, null));
        var observer = new RecordingObserver<AskCopilotResponse>();

        call(() -> service.askCopilot(AskCopilotRequest.newBuilder()
                .setGraphId("g1").setThreadId("abc").setMessage("question")
                .build(), observer));

        // Blank must become null so the copilot service keeps its own "[]" default:
        // an empty string is not valid JSON and would fail in the engine.
        verify(copilotService).askCopilot(eq("g1"), eq("orchestra:abc"), eq("question"),
                isNull(), isNull(), isNull(), isNull(), isNull());
    }

    @Test
    @DisplayName("the page size is defaulted and clamped")
    void clampsPageSize() {
        when(langGraphService.listGraphs(anyInt(), any(), any()))
                .thenReturn(new GraphListPayload(List.of(), null, 0));

        call(() -> service.listGraphs(ListGraphsRequest.newBuilder().setPageSize(500).build(),
                new RecordingObserver<ListGraphsResponse>()));
        verify(langGraphService).listGraphs(100, null, GraphScopeFilter.anyGraph());

        call(() -> service.listGraphs(ListGraphsRequest.getDefaultInstance(),
                new RecordingObserver<ListGraphsResponse>()));
        verify(langGraphService).listGraphs(10, null, GraphScopeFilter.anyGraph());
    }

    @Test
    @DisplayName("a unary call without the auth interceptor fails closed as a status")
    void failsClosedWithoutClientContext() {
        var observer = new RecordingObserver<GetGraphStateResponse>();

        service.getGraphState(GetGraphStateRequest.newBuilder()
                .setGraphId("g1").setThreadId("abc").build(), observer);

        // Fails closed AND stays a real gRPC status: an escaping exception would be
        // reported by grpc-java as UNKNOWN without the x-error-code contract.
        assertThat(observer.completed).isFalse();
        assertThat(observer.outputs).isEmpty();
        assertThat(observer.errors).singleElement()
                .satisfies(error -> assertThat(error.getStatus().getCode())
                        .isEqualTo(io.grpc.Status.Code.UNAUTHENTICATED));
        verify(langGraphService, never()).getGraphState(any(), any());
    }

    @Test
    @DisplayName("a blank thread id is INVALID_ARGUMENT with the GraphQL-parity code")
    void rejectsBlankThreadIdWithMappedStatus() {
        var observer = new RecordingObserver<GetGraphStateResponse>();

        call(() -> service.getGraphState(GetGraphStateRequest.newBuilder()
                .setGraphId("g1").setThreadId("").build(), observer));

        assertThat(observer.completed).isFalse();
        assertThat(observer.outputs).isEmpty();
        assertThat(observer.errors).singleElement().satisfies(error -> {
            assertThat(error.getStatus().getCode()).isEqualTo(io.grpc.Status.Code.INVALID_ARGUMENT);
            assertThat(error.getStatus().getDescription()).contains("thread_id is required");
            assertThat(error.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("INVALID_ARGUMENT");
        });
        verify(langGraphService, never()).getGraphState(any(), any());
    }

    @Test
    @DisplayName("a thread id namespaced with another tenant is refused, not silently re-prefixed")
    void rejectsForeignTenantThreadId() {
        var observer = new RecordingObserver<GetGraphStateResponse>();

        call(() -> service.getGraphState(GetGraphStateRequest.newBuilder()
                .setGraphId("g1").setThreadId("other-tenant:abc").build(), observer));

        assertThat(observer.errors).singleElement().satisfies(error ->
                assertThat(error.getStatus().getDescription()).contains("not scoped to this tenant"));
        verify(langGraphService, never()).getGraphState(any(), any());
    }

    @Test
    @DisplayName("a graph outside the account scope is PERMISSION_DENIED and never delegated")
    void refusesGraphOutsideTheAccountScope() {
        B2BClientContext scoped = B2BClientContext.of("acme", "acme", "", List.of("acme-*"));
        var observer = new RecordingObserver<DeleteGraphResponse>();

        runAs(scoped, () -> service.deleteGraph(
                DeleteGraphRequest.newBuilder().setGraphId("other-tenant-graph").build(), observer));

        assertThat(observer.completed).isFalse();
        assertThat(observer.errors).singleElement().satisfies(error -> {
            assertThat(error.getStatus().getCode()).isEqualTo(io.grpc.Status.Code.PERMISSION_DENIED);
            assertThat(error.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("GRAPH_ACCESS_DENIED");
            // The answer must not reveal whether such a graph exists.
            assertThat(error.getStatus().getDescription()).contains("not allowed to access");
        });
        verify(langGraphService, never()).deleteGraph(any());
    }

    @Test
    @DisplayName("a graph inside the account scope is still served")
    void allowsGraphInsideTheAccountScope() {
        when(langGraphService.deleteGraph(any()))
                .thenReturn(new org.sandbox.langgraph.dto.graphql.payload.DeleteGraphPayload(true, "gone"));
        B2BClientContext scoped = B2BClientContext.of("acme", "acme", "", List.of("acme-*"));
        var observer = new RecordingObserver<DeleteGraphResponse>();

        runAs(scoped, () -> service.deleteGraph(
                DeleteGraphRequest.newBuilder().setGraphId("acme-agent-v1").build(), observer));

        assertThat(observer.errors).isEmpty();
        assertThat(observer.completed).isTrue();
        verify(langGraphService).deleteGraph("acme-agent-v1");
    }

    @Test
    @DisplayName("ListGraphs pushes the account scope into the query so paging stays correct")
    void pushesTheScopeIntoTheListGraphsQuery() {
        when(langGraphService.listGraphs(anyInt(), any(), any()))
                .thenReturn(new GraphListPayload(List.of(), null, 0));
        B2BClientContext scoped = B2BClientContext.of("acme", "acme", "", List.of("acme-*", "shared"));

        runAs(scoped, () -> service.listGraphs(ListGraphsRequest.getDefaultInstance(),
                new RecordingObserver<ListGraphsResponse>()));

        verify(langGraphService).listGraphs(eq(10), eq(null),
                eq(GraphScopeFilter.of(List.of("acme-*", "shared"))));
    }

    @Test
    @DisplayName("a request the GraphQL edge would reject is rejected here too")
    void rejectsARequestTheGraphQlEdgeWouldReject() {
        LangGraphExternalApiService validated = new LangGraphExternalApiService(
                langGraphService, copilotService, new GraphExecutionAggregator(),
                new ExternalApiMapper(), new B2BThreadScope(), new B2BGraphScope(), realValidator());
        var observer = new RecordingObserver<ExecuteGraphResponse>();

        call(() -> validated.executeGraph(ExecuteGraphRequest.newBuilder()
                .setGraphId("g1").setThreadId("abc").setInput("").build(), observer));

        assertThat(observer.outputs).isEmpty();
        assertThat(observer.errors).singleElement().satisfies(error -> {
            assertThat(error.getStatus().getCode()).isEqualTo(io.grpc.Status.Code.INVALID_ARGUMENT);
            assertThat(error.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("VALIDATION_ERROR");
            assertThat(error.getStatus().getDescription()).contains("Validation failed");
        });
        verify(langGraphService, never()).executeGraphStream(any(), anyBoolean());
    }

    /** A real Hibernate Validator, for the one test that is about bean validation. */
    private static jakarta.validation.Validator realValidator() {
        return jakarta.validation.Validation.buildDefaultValidatorFactory().getValidator();
    }

    /**
     * The transport guard: without a ServerCallStreamObserver the handler cannot observe client
     * cancellation, so it refuses the stream instead of running a run nobody can stop. A bare
     * cast would have thrown CCE into grpc-java - a bare UNKNOWN, no trailer.
     */
    @Test
    @DisplayName("a response observer that cannot report cancellation is refused, not cast")
    void refusesObserverThatCannotObserveCancellation() {
        RecordingObserver<GraphExecutionEvent> plain = new RecordingObserver<>();

        call(() -> service.executeGraphStream(ExecuteGraphRequest.newBuilder()
                .setGraphId("graph-1").setThreadId("abc").setInput("hello").build(), plain));

        assertThat(plain.errors).singleElement().satisfies(error -> {
            assertThat(error.getStatus().getCode()).isEqualTo(io.grpc.Status.Code.INTERNAL);
            assertThat(error.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("INTERNAL_ERROR");
            // The caller is told nothing about the internal reason; only the log has it.
            assertThat(error.getStatus().getDescription()).isEqualTo("internal error");
        });
        verify(langGraphService, never()).executeGraphStream(any(), anyBoolean());
    }

    /**
     * The unary send race: the client disconnects while the handler is answering, so onNext throws
     * out of the handler body's control. It must be swallowed - an exception escaping a gRPC handler
     * is reported as a bare UNKNOWN, discarding the status this facade is built around.
     */
    @Test
    @DisplayName("a response that cannot be written does not escape the handler")
    void aFailedWriteDoesNotEscapeTheHandler() {
        when(langGraphService.listGraphs(anyInt(), any(), any()))
                .thenReturn(new GraphListPayload(List.of(), null, 0));
        StreamObserver<ListGraphsResponse> goneObserver = new StreamObserver<>() {
            @Override
            public void onNext(ListGraphsResponse value) {
                throw new IllegalStateException("call already cancelled");
            }

            @Override
            public void onError(Throwable t) {
                throw new IllegalStateException("call already cancelled");
            }

            @Override
            public void onCompleted() {
                throw new IllegalStateException("call already cancelled");
            }
        };

        // Must return normally: a throw here is the regression, whatever the observer records.
        call(() -> service.listGraphs(ListGraphsRequest.getDefaultInstance(), goneObserver));
    }

    /** Runs the service method on the gRPC context the interceptor would have set. */
    private void call(Runnable action) {
        io.grpc.Context.current().withValue(B2BClientContext.KEY, CLIENT).run(action);
    }

    /** Same, but for an account with an explicit graph scope. */
    private static void runAs(B2BClientContext client, Runnable action) {
        io.grpc.Context.current().withValue(B2BClientContext.KEY, client).run(action);
    }
}
