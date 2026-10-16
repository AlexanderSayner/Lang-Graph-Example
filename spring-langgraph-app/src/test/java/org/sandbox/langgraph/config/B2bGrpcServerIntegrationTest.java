package org.sandbox.langgraph.config;

import io.grpc.ManagedChannel;
import io.grpc.ManagedChannelBuilder;
import io.grpc.Metadata;
import io.grpc.Server;
import io.grpc.ServerInterceptors;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import io.grpc.stub.MetadataUtils;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.config.props.B2bGrpcProperties;
import org.sandbox.langgraph.core.model.ExecutionEventType;
import org.sandbox.langgraph.dto.graphql.payload.GraphExecutionEventPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphListPayload;
import org.sandbox.langgraph.grpc.external.DeleteGraphRequest;
import org.sandbox.langgraph.grpc.external.ExecuteGraphRequest;
import org.sandbox.langgraph.grpc.external.GraphExecutionEvent;
import org.sandbox.langgraph.grpc.external.LangGraphExternalApiGrpc;
import org.sandbox.langgraph.grpc.external.ListGraphsRequest;
import org.sandbox.langgraph.grpc.external.ListGraphsResponse;
import org.sandbox.langgraph.mapper.ExternalApiMapper;
import org.sandbox.langgraph.security.b2b.B2BAuthInterceptor;
import org.sandbox.langgraph.security.b2b.B2BServiceAccounts;
import org.sandbox.langgraph.security.b2b.B2BGraphScope;
import org.sandbox.langgraph.security.b2b.B2BThreadScope;
import org.sandbox.langgraph.service.CopilotService;
import org.sandbox.langgraph.service.GraphExecutionAggregator;
import org.sandbox.langgraph.service.LangGraphGrpcService;
import org.sandbox.langgraph.service.grpc.external.LangGraphExternalApiService;
import org.springframework.grpc.server.ShadedNettyGrpcServerFactory;
import reactor.core.publisher.Flux;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * The B2B server as it actually runs: the same hand-wired factory, the same
 * service binding and the same interceptor the application uses, exercised over a
 * real transport on an ephemeral port.
 * <p>
 * The unit tests prove the pieces; this proves they are assembled the way
 * {@link B2bGrpcServerConfig} assembles them.
 */
class B2bGrpcServerIntegrationTest {

    private static final String API_KEY = "orchestra-secret-key";

    private final LangGraphGrpcService langGraphService = mock(LangGraphGrpcService.class);
    private Server server;
    private ManagedChannel channel;

    @BeforeEach
    void startServer() throws java.io.IOException {
        B2BServiceAccounts accounts = new B2BServiceAccounts(
                new B2bGrpcProperties(null, null, null, null, null, null, null,
                        new B2bGrpcProperties.Security(List.of(
                                new B2bGrpcProperties.Client("orchestra", "orchestra", "", API_KEY,
                                        // Deliberately narrow: every test below either uses a "graph-*"
                                        // id or proves that a graph outside this scope is refused.
                                        null, List.of("*"), List.of("graph-*"), false)))));
        accounts.init();

        LangGraphExternalApiService externalApi = new LangGraphExternalApiService(
                langGraphService, mock(CopilotService.class), new GraphExecutionAggregator(),
                new ExternalApiMapper(), new B2BThreadScope(), new B2BGraphScope(), mock(jakarta.validation.Validator.class));

        ShadedNettyGrpcServerFactory factory = new ShadedNettyGrpcServerFactory(
                "localhost:0", List.of(), null, null, null);
        factory.addService(ServerInterceptors.interceptForward(
                externalApi.bindService(), new B2BAuthInterceptor(accounts)));

        server = factory.createServer().start();
    }

    @AfterEach
    void stopServer() {
        if (channel != null) {
            channel.shutdownNow();
        }
        if (server != null) {
            server.shutdownNow();
        }
    }

    @Test
    @DisplayName("an unauthenticated caller is rejected with UNAUTHENTICATED")
    void rejectsCallersWithoutAKey() {
        LangGraphExternalApiGrpc.LangGraphExternalApiBlockingStub stub = blockingStub(null);

        assertThatThrownBy(() -> stub.listGraphs(ListGraphsRequest.getDefaultInstance()))
                .isInstanceOf(StatusRuntimeException.class)
                .satisfies(error -> assertThat(((StatusRuntimeException) error).getStatus().getCode())
                        .isEqualTo(Status.Code.UNAUTHENTICATED));
    }

    @Test
    @DisplayName("an authenticated caller reaches the facade over the wire")
    void servesAuthenticatedCallers() {
        when(langGraphService.listGraphs(anyInt(), any(), any()))
                .thenReturn(new GraphListPayload(List.of(), null, 0));

        ListGraphsResponse response = blockingStub(apiKeyHeaders())
                .listGraphs(ListGraphsRequest.newBuilder().setPageSize(5).build());

        assertThat(response.getGraphsList()).isEmpty();
        assertThat(response.getTotalCount()).isZero();
    }

    @Test
    @DisplayName("a rejected scope/validation error survives the transport with its status and trailer")
    void carriesTheStatusOverTheWire() {
        // An exception thrown by the handler instead of mapped would reach this client as
        // UNKNOWN("Application error processing RPC") - the regression F2 exists to prevent.
        // An empty thread id is a contract violation: GrpcErrors maps it to INVALID_ARGUMENT,
        // and grpc-java must then report that exact code plus the trailer.
        assertThatThrownBy(() -> blockingStub(apiKeyHeaders()).executeGraph(
                        ExecuteGraphRequest.newBuilder().setGraphId("graph-1").setThreadId("").build()))
                .isInstanceOf(StatusRuntimeException.class)
                .satisfies(error -> {
                    StatusRuntimeException status = (StatusRuntimeException) error;
                    assertThat(status.getStatus().getCode()).isEqualTo(Status.Code.INVALID_ARGUMENT);
                    assertThat(status.getTrailers()).isNotNull();
                    assertThat(status.getTrailers().get(
                            org.sandbox.langgraph.exception.GrpcErrors.ERROR_CODE))
                            .isEqualTo("INVALID_ARGUMENT");
                });
    }

    @Test
    @DisplayName("a graph outside the account scope is PERMISSION_DENIED over the wire")
    void refusesOutOfScopeGraphOverTheWire() {
        assertThatThrownBy(() -> blockingStub(apiKeyHeaders()).deleteGraph(
                        DeleteGraphRequest.newBuilder().setGraphId("not-granted").build()))
                .isInstanceOf(StatusRuntimeException.class)
                .satisfies(error -> {
                    StatusRuntimeException status = (StatusRuntimeException) error;
                    assertThat(status.getStatus().getCode()).isEqualTo(Status.Code.PERMISSION_DENIED);
                    assertThat(status.getTrailers().get(
                            org.sandbox.langgraph.exception.GrpcErrors.ERROR_CODE))
                            .isEqualTo("GRAPH_ACCESS_DENIED");
                });
        org.mockito.Mockito.verify(langGraphService, org.mockito.Mockito.never())
                .deleteGraph(org.mockito.ArgumentMatchers.any());
    }

    @Test
    @DisplayName("a streaming call reaches the service and completes over the wire")
    void streamsOverTheWire() {
        when(langGraphService.executeGraphStream(any(), anyBoolean())).thenReturn(Flux.just(
                new GraphExecutionEventPayload(ExecutionEventType.START, "node", "out",
                        Map.of("k", "v"), 1L, 1, null)));

        List<GraphExecutionEvent> events = new ArrayList<>();
        var stream = blockingStub(apiKeyHeaders())
                .executeGraphStream(ExecuteGraphRequest.newBuilder()
                        .setGraphId("graph-1")
                        .setThreadId("abc")
                        .setInput("hi")
                        .build());
        // The blocking stub returns a plain Iterator over the server stream.
        while (stream.hasNext()) {
            events.add(stream.next());
        }

        assertThat(events).hasSize(1);
        assertThat(events.get(0).getEventType())
                .isEqualTo(org.sandbox.langgraph.grpc.external.ExecutionEventType
                        .EXECUTION_EVENT_TYPE_START);
        // The thread the engine is asked to run on is tenant-scoped.
        org.mockito.Mockito.verify(langGraphService).executeGraphStream(
                org.mockito.ArgumentMatchers.argThat(input ->
                        "orchestra:abc".equals(input.threadId())), anyBoolean());
    }

    private LangGraphExternalApiGrpc.LangGraphExternalApiBlockingStub blockingStub(Metadata headers) {
        if (channel == null) {
            channel = ManagedChannelBuilder.forAddress("localhost", server.getPort())
                    .usePlaintext()
                    .build();
        }
        LangGraphExternalApiGrpc.LangGraphExternalApiBlockingStub stub =
                LangGraphExternalApiGrpc.newBlockingStub(channel);
        return headers == null ? stub
                : stub.withInterceptors(MetadataUtils.newAttachHeadersInterceptor(headers));
    }

    private static Metadata apiKeyHeaders() {
        Metadata headers = new Metadata();
        headers.put(Metadata.Key.of("x-api-key", Metadata.ASCII_STRING_MARSHALLER), API_KEY);
        return headers;
    }
}
