package org.sandbox.langgraph.mapper;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.dto.graphql.payload.CancelGraphExecutionPayload;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Tests for the gRPC -> GraphQL mapping of an execution-cancel response.
 * <p>
 * The subtle contract pinned here: {@code success} is always true even when {@code cancelled}
 * is false. "Nothing was running to cancel" is a legitimate outcome, not a failure - the UI
 * must not render it as an error. Only a transport failure (handled in the service, not the
 * mapper) reports success=false.
 */
class GraphGrpcMapperCancelTest {

    private final GraphGrpcMapper mapper = new GraphGrpcMapper() {
    };

    @Test
    @DisplayName("a cancelled run maps to success=true, cancelled=true")
    void mapsCancelledRun() {
        var response = org.sandbox.langgraph.grpc.CancelGraphExecutionResponse.newBuilder()
                .setCancelled(true)
                .setMessage("Execution cancelled")
                .build();

        CancelGraphExecutionPayload payload = mapper.toCancelGraphExecutionPayload(response);

        assertThat(payload.success()).isTrue();
        assertThat(payload.cancelled()).isTrue();
        assertThat(payload.message()).isEqualTo("Execution cancelled");
    }

    @Test
    @DisplayName("nothing running is not an error - success stays true, cancelled=false")
    void mapsNoActiveRunAsSuccess() {
        var response = org.sandbox.langgraph.grpc.CancelGraphExecutionResponse.newBuilder()
                .setCancelled(false)
                .setMessage("No active execution found for this graph/thread")
                .build();

        CancelGraphExecutionPayload payload = mapper.toCancelGraphExecutionPayload(response);

        assertThat(payload.success()).isTrue();
        assertThat(payload.cancelled()).isFalse();
    }
}