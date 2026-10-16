package org.sandbox.langgraph.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.grpc.Status;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.core.service.GraphPostgresService;
import org.sandbox.langgraph.dto.graphql.input.BuildGraphInput;
import org.sandbox.langgraph.dto.graphql.payload.BuildGraphPayload;
import org.sandbox.langgraph.exception.LangGraphException;
import org.sandbox.langgraph.grpc.BuildGraphRequest;
import org.sandbox.langgraph.grpc.BuildGraphResponse;
import org.sandbox.langgraph.grpc.LangGraphServiceGrpc;
import org.sandbox.langgraph.mapper.GraphGrpcMapper;

import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Pins how an engine-side rejection of a graph definition reaches the GraphQL caller.
 * <p>
 * The engine aborts with INVALID_ARGUMENT and a precise reason (unknown provider, malformed edge
 * condition, ...). Without the wrap in {@code buildGraph} the raw StatusRuntimeException matched no
 * case in GraphQlExceptionHandler and the editor showed a bare "Internal server error", leaving the
 * reason in the logs only - the same mapping executeGraphStream and the state calls already apply.
 */
class LangGraphGrpcServiceBuildTest {

    private static final String REASON =
            "Malformed condition 'output = true' on node 'judge': expected 'key == value'";

    private final GraphGrpcMapper mapper = mock(GraphGrpcMapper.class);
    private final GraphPostgresService store = mock(GraphPostgresService.class);

    /** The mapper is stubbed below, so the definition content is irrelevant to this contract. */
    private static BuildGraphInput input() {
        return new BuildGraphInput("g1", "quiz", List.of(), List.of(), Map.of());
    }

    private LangGraphGrpcService service(BuildGraphResponse response) {
        LangGraphServiceGrpc.LangGraphServiceBlockingStub stub =
                mock(LangGraphServiceGrpc.LangGraphServiceBlockingStub.class);
        when(stub.withDeadlineAfter(anyLong(), any(TimeUnit.class))).thenReturn(stub);
        if (response != null) {
            when(stub.buildGraph(any(BuildGraphRequest.class))).thenReturn(response);
        } else {
            when(stub.buildGraph(any(BuildGraphRequest.class)))
                    .thenThrow(Status.INVALID_ARGUMENT.withDescription(REASON).asRuntimeException());
        }
        when(mapper.toBuildGraphRequest(any(BuildGraphInput.class)))
                .thenReturn(BuildGraphRequest.newBuilder().setGraphId("g1").build());
        when(mapper.toBuildGraphPayload(any(BuildGraphResponse.class)))
                .thenReturn(new BuildGraphPayload(true, "g1", "ok"));
        return new LangGraphGrpcService(stub, mapper, new ObjectMapper(), store);
    }

    @Test
    @DisplayName("an engine rejection reaches the caller as GraphBuildException carrying the reason")
    void surfacesTheEngineRejectionWithItsReason() {
        LangGraphGrpcService service = service(null);

        assertThatThrownBy(() -> service.buildGraph(input()))
                .isInstanceOf(LangGraphException.GraphBuildException.class)
                .hasMessageContaining("Malformed condition")
                .hasMessageContaining("judge");

        // A definition the engine refused must never be persisted.
        verify(store, never()).saveGraph(any(BuildGraphInput.class));
    }

    @Test
    @DisplayName("a build the engine accepts is persisted and reported as success")
    void persistsTheDefinitionOnlyAfterTheEngineAccepted() {
        LangGraphGrpcService service = service(
                BuildGraphResponse.newBuilder().setSuccess(true).setGraphId("g1").build());

        BuildGraphPayload payload = service.buildGraph(input());

        assertThat(payload.success()).isTrue();
        verify(store).saveGraph(any(BuildGraphInput.class));
    }
}
