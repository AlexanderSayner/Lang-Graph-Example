package org.sandbox.langgraph.mapper;

import com.google.protobuf.Struct;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.core.model.ExecutionEventType;
import org.sandbox.langgraph.dto.graphql.input.NodeInput;
import org.sandbox.langgraph.dto.graphql.payload.CancelGraphExecutionPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphExecutionEventPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphListPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphSummary;
import org.sandbox.langgraph.dto.graphql.payload.meta.PageInfo;
import org.sandbox.langgraph.util.JsonDiffUtil;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatIllegalArgumentException;

/**
 * The B2B contract is a facade, not a mirror: the interesting assertions are the
 * ones about what does <b>not</b> cross the boundary (UI coordinates) and about
 * the two-directional enum mapping.
 */
class ExternalApiMapperTest {

    private final ExternalApiMapper mapper = new ExternalApiMapper();

    @Test
    @DisplayName("a node carries no UI position - the facade never touches the canvas")
    void dropsUiCoordinates() {
        NodeInput node = mapper.toNodeInput(org.sandbox.langgraph.grpc.external.NodeDefinition.newBuilder()
                .setNodeId("node_1")
                .setNodeType(org.sandbox.langgraph.grpc.external.NodeType.NODE_TYPE_HTTP_REQUEST)
                .setHandlerName("callApi")
                .setMetadata(Struct.newBuilder()
                        .putFields("url", com.google.protobuf.Value.newBuilder()
                                .setStringValue("https://example.test").build())
                        .build())
                .build());

        assertThat(node.nodeId()).isEqualTo("node_1");
        assertThat(node.nodeType()).isEqualTo(org.sandbox.langgraph.core.model.NodeType.HTTP_REQUEST);
        assertThat(node.metadata()).containsEntry("url", "https://example.test");
        assertThat(node.position()).isNull();
    }

    @Test
    @DisplayName("an UNSPECIFIED node type is rejected at the edge")
    void rejectsUnspecifiedNodeType() {
        assertThatIllegalArgumentException()
                .isThrownBy(() -> mapper.toNodeInput(org.sandbox.langgraph.grpc.external.NodeDefinition.newBuilder()
                        .setNodeType(org.sandbox.langgraph.grpc.external.NodeType.NODE_TYPE_UNSPECIFIED)
                        .build()))
                .withMessageContaining("node_type is required");
    }

    @Test
    @DisplayName("a value from a newer contract cannot even be built into a message")
    void rejectsUnrecognizedNodeType() {
        // Protobuf itself refuses to put an UNRECOGNIZED value into a message, so the
        // mapper's UNRECOGNIZED branch is defence in depth rather than the first line.
        assertThatIllegalArgumentException()
                .isThrownBy(() -> org.sandbox.langgraph.grpc.external.NodeDefinition.newBuilder()
                        .setNodeType(org.sandbox.langgraph.grpc.external.NodeType.UNRECOGNIZED)
                        .build());
    }

    @Test
    @DisplayName("every execution event type maps, including CANCELLED")
    void mapsEveryEventType() {
        assertThat(mapper.toExternalEventType(ExecutionEventType.CANCELLED))
                .isEqualTo(org.sandbox.langgraph.grpc.external.ExecutionEventType
                        .EXECUTION_EVENT_TYPE_CANCELLED);
        assertThat(mapper.toExternalEventType(ExecutionEventType.WAITING_FOR_INPUT))
                .isEqualTo(org.sandbox.langgraph.grpc.external.ExecutionEventType
                        .EXECUTION_EVENT_TYPE_WAITING_FOR_INPUT);
        assertThat(mapper.toExternalEventType(ExecutionEventType.START))
                .isEqualTo(org.sandbox.langgraph.grpc.external.ExecutionEventType
                        .EXECUTION_EVENT_TYPE_START);
    }

    @Test
    @DisplayName("an execution event keeps its state, tokens and timestamp")
    void mapsExecutionEvent() {
        GraphExecutionEventPayload payload = new GraphExecutionEventPayload(
                ExecutionEventType.NODE_END, "node_1", "done", Map.of("answer", "42"), 123L, 7, null);

        var event = mapper.toExecutionEvent(payload);

        assertThat(event.getEventType()).isEqualTo(org.sandbox.langgraph.grpc.external.ExecutionEventType
                .EXECUTION_EVENT_TYPE_NODE_END);
        assertThat(event.getNodeId()).isEqualTo("node_1");
        assertThat(event.getOutput()).isEqualTo("done");
        assertThat(event.getState().getFieldsMap().get("answer").getStringValue()).isEqualTo("42");
        assertThat(event.getTimestamp()).isEqualTo(123L);
        assertThat(event.getTotalTokens()).isEqualTo(7);
    }

    @Test
    @DisplayName("an event without tokens maps to 0 instead of failing the frame")
    void mapsNullTokensToZero() {
        var event = mapper.toExecutionEvent(new GraphExecutionEventPayload(
                ExecutionEventType.END, null, null, null, 1L, null, null));

        assertThat(event.getTotalTokens()).isZero();
        assertThat(event.getOutput()).isEmpty();
        assertThat(event.getState()).isEqualTo(Struct.getDefaultInstance());
    }

    @Test
    @DisplayName("a run that produced no events maps its (absent) type to UNSPECIFIED")
    void mapsNullEventTypeToUnspecified() {
        // The blocking ExecuteGraph answers an eventless run with a payload whose
        // event type is null; the wire type cannot be null.
        var response = mapper.toExecuteGraphResponse(
                new org.sandbox.langgraph.dto.graphql.payload.ExecuteGraphPayload(
                        true, "", null, null, null, null));

        assertThat(response.getEventType())
                .isEqualTo(org.sandbox.langgraph.grpc.external.ExecutionEventType
                        .EXECUTION_EVENT_TYPE_UNSPECIFIED);
        assertThat(response.getSuccess()).isTrue();
    }

    @Test
    @DisplayName("a state diff's modified paths become nested Struct values")
    void mapsStateDiff() {
        JsonDiffUtil.StateDiff diff = new JsonDiffUtil.StateDiff(
                Map.of("added", 1),
                List.of("/removed"),
                Map.of("/path", Map.of("old", 1, "new", 2)),
                List.of("Variable added"));

        var mapped = mapper.toStateDiff(diff);

        assertThat(mapped.getAdded().getFieldsMap().get("added").getNumberValue()).isEqualTo(1.0);
        assertThat(mapped.getRemovedList()).containsExactly("/removed");
        assertThat(mapped.getModified().getFieldsMap().get("/path").getStructValue()
                .getFieldsMap().get("old").getNumberValue()).isEqualTo(1.0);
        assertThat(mapped.getSummaryList()).containsExactly("Variable added");
    }

    @Test
    @DisplayName("a graph listing flattens the page info and keeps the real total")
    void mapsGraphList() {
        GraphListPayload payload = new GraphListPayload(
                List.of(new GraphSummary("g1", "Graph one", 3, "2026-01-01T00:00:00",
                        org.sandbox.langgraph.core.model.GraphStatus.ACTIVE)),
                new PageInfo(true, "10"),
                42);

        var response = mapper.toListGraphsResponse(payload);

        assertThat(response.getTotalCount()).isEqualTo(42);
        assertThat(response.getHasNextPage()).isTrue();
        assertThat(response.getNextPageToken()).isEqualTo("10");
        assertThat(response.getGraphsList()).hasSize(1);
        assertThat(response.getGraphs(0).getStatus())
                .isEqualTo(org.sandbox.langgraph.grpc.external.GraphStatus.GRAPH_STATUS_ACTIVE);
    }

    @Test
    @DisplayName("cancelling a run that was not running stays a success with cancelled=false")
    void mapsCancelWithoutActiveRun() {
        var response = mapper.toCancelExecutionResponse(
                new CancelGraphExecutionPayload(true, false, "No active execution found for this graph/thread"));

        assertThat(response.getSuccess()).isTrue();
        assertThat(response.getCancelled()).isFalse();
        assertThat(response.getMessage()).contains("No active execution");
    }
}
