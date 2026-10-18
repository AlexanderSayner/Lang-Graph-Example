package org.sandbox.langgraph.mapper;

import org.springframework.stereotype.Component;
import org.sandbox.langgraph.core.model.ExecutionEventType;
import org.sandbox.langgraph.dto.graphql.input.BuildGraphInput;
import org.sandbox.langgraph.dto.graphql.input.EdgeInput;
import org.sandbox.langgraph.dto.graphql.input.ExecuteGraphInput;
import org.sandbox.langgraph.dto.graphql.input.NodeInput;
import org.sandbox.langgraph.dto.graphql.input.UpdateGraphStateInput;
import org.sandbox.langgraph.dto.graphql.payload.BuildGraphPayload;
import org.sandbox.langgraph.dto.graphql.payload.CancelGraphExecutionPayload;
import org.sandbox.langgraph.dto.graphql.payload.CopilotGraphPayload;
import org.sandbox.langgraph.dto.graphql.payload.DeleteGraphPayload;
import org.sandbox.langgraph.dto.graphql.payload.ExecuteGraphPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphExecutionEventPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphHistoryPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphListPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphStatePayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphSummary;
import org.sandbox.langgraph.dto.graphql.payload.StateSnapshot;
import org.sandbox.langgraph.dto.graphql.payload.UpdateGraphStatePayload;
import org.sandbox.langgraph.util.JsonDiffUtil;
import org.sandbox.langgraph.util.ProtoJson;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Maps the B2B {@code external.v1} contract onto the service's own DTOs and back.
 * <p>
 * The external types are fully qualified because they share their simple names
 * with the internal model classes ({@code NodeType}, {@code GraphStatus}). The
 * mapping is total in both directions: an {@code *_UNSPECIFIED} enum value on
 * the request path is a contract violation and fails the call, and an unknown
 * internal value can never be silently dropped on the response path.
 */
@Component
public final class ExternalApiMapper {

    // ==================== Request: external -> internal DTO ====================

    public BuildGraphInput toBuildGraphInput(org.sandbox.langgraph.grpc.external.BuildGraphRequest request) {
        List<NodeInput> nodes = request.getNodesList().stream().map(this::toNodeInput).toList();
        List<EdgeInput> edges = request.getEdgesList().stream().map(this::toEdgeInput).toList();
        return new BuildGraphInput(
                request.getGraphId(),
                request.getGraphName(),
                nodes,
                edges,
                ProtoJson.toMap(request.getConfig())
        );
    }

    /**
     * @param internalThreadId the tenant-scoped thread id, resolved by
     *                         {@code B2BThreadScope} before this mapping runs
     */
    public ExecuteGraphInput toExecuteGraphInput(org.sandbox.langgraph.grpc.external.ExecuteGraphRequest request,
                                                  String internalThreadId) {
        return new ExecuteGraphInput(
                request.getGraphId(),
                request.getInput(),
                ProtoJson.toMap(request.getContext()),
                internalThreadId
        );
    }

    /**
     * @param internalThreadId the tenant-scoped thread id, resolved by
     *                         {@code B2BThreadScope} before this mapping runs
     */
    public UpdateGraphStateInput toUpdateGraphStateInput(
            org.sandbox.langgraph.grpc.external.UpdateGraphStateRequest request,
            String internalThreadId) {
        return new UpdateGraphStateInput(
                request.getGraphId(),
                internalThreadId,
                request.getAsNode(),
                ProtoJson.toMap(request.getStateUpdates())
        );
    }

    public NodeInput toNodeInput(org.sandbox.langgraph.grpc.external.NodeDefinition node) {
        // The last argument is the UI `position`, which the B2B contract never
        // carries: a build through this facade must not touch the canvas layout.
        return new NodeInput(
                node.getNodeId(),
                toNodeType(node.getNodeType()),
                node.getHandlerName(),
                ProtoJson.toMap(node.getMetadata()),
                null
        );
    }

    public EdgeInput toEdgeInput(org.sandbox.langgraph.grpc.external.EdgeDefinition edge) {
        return new EdgeInput(edge.getSource(), edge.getTarget(),
                edge.getCondition().isEmpty() ? null : edge.getCondition());
    }

    public org.sandbox.langgraph.core.model.NodeType toNodeType(
            org.sandbox.langgraph.grpc.external.NodeType nodeType) {
        return switch (nodeType) {
            case NODE_TYPE_START -> org.sandbox.langgraph.core.model.NodeType.START;
            case NODE_TYPE_END -> org.sandbox.langgraph.core.model.NodeType.END;
            case NODE_TYPE_ACTION -> org.sandbox.langgraph.core.model.NodeType.ACTION;
            case NODE_TYPE_HUMAN -> org.sandbox.langgraph.core.model.NodeType.HUMAN;
            case NODE_TYPE_GRAPH -> org.sandbox.langgraph.core.model.NodeType.GRAPH;
            case NODE_TYPE_HTTP_REQUEST -> org.sandbox.langgraph.core.model.NodeType.HTTP_REQUEST;
            // Protobuf enums carry an extra UNRECOGNIZED sentinel for values the
            // client sent with a newer contract; it is as invalid as UNSPECIFIED.
            case NODE_TYPE_UNSPECIFIED, UNRECOGNIZED ->
                    throw new IllegalArgumentException("node_type is required and must be a known value");
        };
    }

    // ==================== Response: internal DTO -> external ====================

    public org.sandbox.langgraph.grpc.external.GraphExecutionEvent toExecutionEvent(
            GraphExecutionEventPayload payload) {
        return org.sandbox.langgraph.grpc.external.GraphExecutionEvent.newBuilder()
                .setEventType(toExternalEventType(payload.eventType()))
                .setNodeId(nullToEmpty(payload.nodeId()))
                .setOutput(nullToEmpty(payload.output()))
                .setState(ProtoJson.toStruct(payload.state()))
                .setTimestamp(payload.timestamp())
                .setTotalTokens(payload.totalTokens() == null ? 0 : payload.totalTokens())
                .setErrorMessage(nullToEmpty(payload.errorMessage()))
                .build();
    }

    public org.sandbox.langgraph.grpc.external.ExecuteGraphResponse toExecuteGraphResponse(
            ExecuteGraphPayload payload) {
        return org.sandbox.langgraph.grpc.external.ExecuteGraphResponse.newBuilder()
                .setSuccess(payload.success())
                .setOutput(nullToEmpty(payload.output()))
                .setEventType(toExternalEventType(payload.eventType()))
                .setState(ProtoJson.toStruct(payload.state()))
                .setTotalTokens(payload.totalTokens() == null ? 0 : payload.totalTokens())
                .setErrorMessage(nullToEmpty(payload.errorMessage()))
                .build();
    }

    public org.sandbox.langgraph.grpc.external.BuildGraphResponse toBuildGraphResponse(
            BuildGraphPayload payload) {
        return org.sandbox.langgraph.grpc.external.BuildGraphResponse.newBuilder()
                .setSuccess(payload.success())
                .setGraphId(nullToEmpty(payload.graphId()))
                .setMessage(nullToEmpty(payload.message()))
                .build();
    }

    public org.sandbox.langgraph.grpc.external.GetGraphStateResponse toGetGraphStateResponse(
            GraphStatePayload payload) {
        return org.sandbox.langgraph.grpc.external.GetGraphStateResponse.newBuilder()
                .setSuccess(payload.success())
                .setState(ProtoJson.toStruct(payload.state()))
                .setCurrentNode(nullToEmpty(payload.currentNode()))
                .addAllNodeHistory(payload.nodeHistory())
                .build();
    }

    public org.sandbox.langgraph.grpc.external.UpdateGraphStateResponse toUpdateGraphStateResponse(
            UpdateGraphStatePayload payload) {
        return org.sandbox.langgraph.grpc.external.UpdateGraphStateResponse.newBuilder()
                .setSuccess(payload.success())
                .setUpdatedState(ProtoJson.toStruct(payload.updatedState()))
                .setMessage(nullToEmpty(payload.message()))
                .build();
    }

    /**
     * The internal enum is exhaustive (it is the engine's own event vocabulary), so
     * every constant maps. A null event type is possible only for a run that
     * produced no events at all (the empty-run payload of the blocking execute),
     * and the wire type cannot be null - it becomes UNSPECIFIED.
     */
    public org.sandbox.langgraph.grpc.external.ExecutionEventType toExternalEventType(ExecutionEventType type) {
        if (type == null) {
            return org.sandbox.langgraph.grpc.external.ExecutionEventType
                    .EXECUTION_EVENT_TYPE_UNSPECIFIED;
        }
        return switch (type) {
            case START -> org.sandbox.langgraph.grpc.external.ExecutionEventType
                    .EXECUTION_EVENT_TYPE_START;
            case NODE_START -> org.sandbox.langgraph.grpc.external.ExecutionEventType
                    .EXECUTION_EVENT_TYPE_NODE_START;
            case NODE_END -> org.sandbox.langgraph.grpc.external.ExecutionEventType
                    .EXECUTION_EVENT_TYPE_NODE_END;
            case END -> org.sandbox.langgraph.grpc.external.ExecutionEventType
                    .EXECUTION_EVENT_TYPE_END;
            case WAITING_FOR_INPUT -> org.sandbox.langgraph.grpc.external.ExecutionEventType
                    .EXECUTION_EVENT_TYPE_WAITING_FOR_INPUT;
            case ERROR -> org.sandbox.langgraph.grpc.external.ExecutionEventType
                    .EXECUTION_EVENT_TYPE_ERROR;
            case CANCELLED -> org.sandbox.langgraph.grpc.external.ExecutionEventType
                    .EXECUTION_EVENT_TYPE_CANCELLED;
        };
    }

    public org.sandbox.langgraph.grpc.external.GraphStatus toExternalGraphStatus(
            org.sandbox.langgraph.core.model.GraphStatus status) {
        // The response path must be total: a summary built without a status (the engine
        // may answer "UNKNOWN") would otherwise NPE inside the switch and fail the whole
        // listing rather than degrade to UNSPECIFIED.
        if (status == null) {
            return org.sandbox.langgraph.grpc.external.GraphStatus.GRAPH_STATUS_UNSPECIFIED;
        }
        return switch (status) {
            case ACTIVE -> org.sandbox.langgraph.grpc.external.GraphStatus.GRAPH_STATUS_ACTIVE;
            case INACTIVE -> org.sandbox.langgraph.grpc.external.GraphStatus.GRAPH_STATUS_INACTIVE;
            case ERROR -> org.sandbox.langgraph.grpc.external.GraphStatus.GRAPH_STATUS_ERROR;
            case BUILDING -> org.sandbox.langgraph.grpc.external.GraphStatus.GRAPH_STATUS_BUILDING;
        };
    }

    public org.sandbox.langgraph.grpc.external.GetExecutionHistoryResponse toGetExecutionHistoryResponse(
            GraphHistoryPayload payload) {
        List<org.sandbox.langgraph.grpc.external.StateSnapshot> history = payload.history() == null
                ? List.of()
                : payload.history().stream().map(this::toStateSnapshot).toList();
        return org.sandbox.langgraph.grpc.external.GetExecutionHistoryResponse.newBuilder()
                .setSuccess(Boolean.TRUE.equals(payload.success()))
                .addAllHistory(history)
                .setErrorMessage(nullToEmpty(payload.errorMessage()))
                .build();
    }

    public org.sandbox.langgraph.grpc.external.StateSnapshot toStateSnapshot(StateSnapshot snapshot) {
        return org.sandbox.langgraph.grpc.external.StateSnapshot.newBuilder()
                .setNodeId(nullToEmpty(snapshot.nodeId()))
                .setState(ProtoJson.toStruct(snapshot.stateJson()))
                .setTimestamp(nullToEmpty(snapshot.timestamp()))
                .setDiff(toStateDiff(snapshot.diff()))
                .setTokensUsed(snapshot.tokensUsed() == null ? 0 : snapshot.tokensUsed())
                .setTotalTokens(snapshot.totalTokens() == null ? 0 : snapshot.totalTokens())
                .build();
    }

    public org.sandbox.langgraph.grpc.external.StateDiff toStateDiff(JsonDiffUtil.StateDiff diff) {
        if (diff == null) {
            return org.sandbox.langgraph.grpc.external.StateDiff.getDefaultInstance();
        }
        // The diff's `modified` is Map<path, {old, new}>; widen it so the shared
        // Struct conversion can treat the inner object as an ordinary nested value.
        Map<String, Object> modified = new LinkedHashMap<>(diff.modified());
        return org.sandbox.langgraph.grpc.external.StateDiff.newBuilder()
                .setAdded(ProtoJson.toStruct(diff.added()))
                .addAllRemoved(diff.removed())
                .setModified(ProtoJson.toStruct(modified))
                .addAllSummary(diff.summary())
                .build();
    }

    /**
     * {@code cancelled=false} is a legitimate answer ("nothing was running"), so it
     * is a successful call with a flag - never an error.
     */
    public org.sandbox.langgraph.grpc.external.CancelExecutionResponse toCancelExecutionResponse(
            CancelGraphExecutionPayload payload) {
        return org.sandbox.langgraph.grpc.external.CancelExecutionResponse.newBuilder()
                .setSuccess(payload.success())
                .setCancelled(payload.cancelled())
                .setMessage(nullToEmpty(payload.message()))
                .build();
    }

    public org.sandbox.langgraph.grpc.external.DeleteGraphResponse toDeleteGraphResponse(
            DeleteGraphPayload payload) {
        return org.sandbox.langgraph.grpc.external.DeleteGraphResponse.newBuilder()
                .setSuccess(payload.success())
                .setMessage(nullToEmpty(payload.message()))
                .build();
    }

    public org.sandbox.langgraph.grpc.external.ListGraphsResponse toListGraphsResponse(
            GraphListPayload payload) {
        List<org.sandbox.langgraph.grpc.external.GraphInfo> graphs = payload.graphs() == null
                ? List.of()
                : payload.graphs().stream().map(this::toGraphInfo).toList();
        return org.sandbox.langgraph.grpc.external.ListGraphsResponse.newBuilder()
                .addAllGraphs(graphs)
                .setTotalCount(payload.totalCount())
                .setHasNextPage(payload.pageInfo() != null && payload.pageInfo().hasNextPage())
                .setNextPageToken(payload.pageInfo() == null || payload.pageInfo().endCursor() == null
                        ? ""
                        : payload.pageInfo().endCursor())
                .build();
    }

    public org.sandbox.langgraph.grpc.external.GraphInfo toGraphInfo(GraphSummary summary) {
        return org.sandbox.langgraph.grpc.external.GraphInfo.newBuilder()
                .setGraphId(nullToEmpty(summary.graphId()))
                .setGraphName(nullToEmpty(summary.graphName()))
                .setNodeCount(summary.nodeCount())
                .setCreatedAt(nullToEmpty(summary.createdAt()))
                .setStatus(toExternalGraphStatus(summary.status()))
                .build();
    }

    public org.sandbox.langgraph.grpc.external.AskCopilotResponse toAskCopilotResponse(
            CopilotGraphPayload payload) {
        return org.sandbox.langgraph.grpc.external.AskCopilotResponse.newBuilder()
                .setSuccess(Boolean.TRUE.equals(payload.success()))
                .setAiResponse(nullToEmpty(payload.aiResponse()))
                .setTotalTokens(payload.totalTokens() == null ? 0 : payload.totalTokens())
                .setErrorMessage(nullToEmpty(payload.errorMessage()))
                .build();
    }

    public String nullToEmpty(String value) {
        return value == null ? "" : value;
    }
}
