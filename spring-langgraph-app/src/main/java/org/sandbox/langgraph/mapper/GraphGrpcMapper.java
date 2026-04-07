package org.sandbox.langgraph.mapper;

import org.mapstruct.Mapper;
import org.sandbox.langgraph.dto.graphql.input.*;
import org.sandbox.langgraph.dto.graphql.payload.*;
import org.sandbox.langgraph.grpc.*;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Mapper(componentModel = "spring")
public interface GraphGrpcMapper {

    // ============== GraphQL Input -> gRPC Request ==============

    default BuildGraphRequest toBuildGraphRequest(BuildGraphInput input) {
        BuildGraphRequest.Builder builder = BuildGraphRequest.newBuilder()
                .setGraphId(input.graphId())
                .setGraphName(input.graphName());

        if (input.nodes() != null) {
            builder.addAllNodes(input.nodes().stream().map(this::toNodeDefinition).toList());
        }
        if (input.edges() != null) {
            builder.addAllEdges(input.edges().stream().map(this::toEdgeDefinition).toList());
        }
        if (input.config() != null) {
            builder.putAllConfig(convertToStringMap(input.config()));
        }
        return builder.build();
    }

    default NodeDefinition toNodeDefinition(NodeInput node) {
        NodeDefinition.Builder builder = NodeDefinition.newBuilder()
                .setNodeId(node.nodeId())
                .setNodeType(node.nodeType())
                .setHandlerName(node.handlerName());
        if (node.metadata() != null) {
            builder.putAllMetadata(convertToStringMap(node.metadata()));
        }
        return builder.build();
    }

    default EdgeDefinition toEdgeDefinition(EdgeInput edge) {
        EdgeDefinition.Builder builder = EdgeDefinition.newBuilder()
                .setSource(edge.source())
                .setTarget(edge.target());
        if (edge.condition() != null && !edge.condition().isBlank()) {
            builder.setCondition(edge.condition());
        }
        return builder.build();
    }

    default ExecuteGraphRequest toExecuteGraphRequest(ExecuteGraphInput input) {
        return toExecuteGraphRequestInternal(input, false);
    }

    default ExecuteGraphRequest toStreamExecuteGraphRequest(ExecuteGraphInput input) {
        return toExecuteGraphRequestInternal(input, true);
    }

    // FIX: Removed 'default' modifier. It is now just 'private'.
    private ExecuteGraphRequest toExecuteGraphRequestInternal(ExecuteGraphInput input, boolean stream) {
        ExecuteGraphRequest.Builder builder = ExecuteGraphRequest.newBuilder()
                .setGraphId(input.graphId())
                .setInput(input.input())
                .setStreamOutput(stream);
        if (input.context() != null) {
            builder.putAllContext(convertToStringMap(input.context()));
        }
        return builder.build();
    }

    default GetGraphStateRequest toGetGraphStateRequest(String graphId, String threadId) {
        GetGraphStateRequest.Builder builder = GetGraphStateRequest.newBuilder().setGraphId(graphId);
        if (threadId != null && !threadId.isBlank()) {
            builder.setThreadId(threadId);
        }
        return builder.build();
    }

    default UpdateGraphStateRequest toUpdateGraphStateRequest(UpdateGraphStateInput input) {
        UpdateGraphStateRequest.Builder builder = UpdateGraphStateRequest.newBuilder()
                .setGraphId(input.graphId());
        if (input.threadId() != null && !input.threadId().isBlank()) {
            builder.setThreadId(input.threadId());
        }
        if (input.stateUpdates() != null) {
            builder.putAllStateUpdates(convertToStringMap(input.stateUpdates()));
        }
        return builder.build();
    }

    default ListGraphsRequest toListGraphsRequest(int pageSize, String pageToken) {
        ListGraphsRequest.Builder builder = ListGraphsRequest.newBuilder().setPageSize(pageSize);
        if (pageToken != null && !pageToken.isBlank()) {
            builder.setPageToken(pageToken);
        }
        return builder.build();
    }

    default DeleteGraphRequest toDeleteGraphRequest(String graphId) {
        return DeleteGraphRequest.newBuilder().setGraphId(graphId).build();
    }

    // ============== gRPC Response -> GraphQL Payload ==============

    default GraphListPayload toGraphListPayload(org.sandbox.langgraph.grpc.ListGraphsResponse response) {
        List<GraphSummary> graphs = response.getGraphsList().stream()
                .map(this::toGraphSummary)
                .toList();

        PageInfo pageInfo = new PageInfo(
                response.getNextPageToken() != null && !response.getNextPageToken().isEmpty(),
                response.getNextPageToken()
        );

        return new GraphListPayload(graphs, pageInfo, graphs.size());
    }

    default GraphSummary toGraphSummary(org.sandbox.langgraph.grpc.GraphInfo graph) {
        return new GraphSummary(graph.getGraphId(), graph.getGraphName(), graph.getNodeCount(), graph.getCreatedAt(), graph.getStatus());
    }

    // FIX: Wrapped response.getStateMap() with new HashMap<>(...) to cast Map<String, String> to Map<String, Object>
    default GraphStatePayload toGraphStatePayload(org.sandbox.langgraph.grpc.GetGraphStateResponse response) {
        return new GraphStatePayload(
                response.getSuccess(),
                response.getStateMap() != null ? new HashMap<>(response.getStateMap()) : null,
                response.getCurrentNode(),
                response.getNodeHistoryList()
        );
    }

    default BuildGraphPayload toBuildGraphPayload(org.sandbox.langgraph.grpc.BuildGraphResponse response) {
        return new BuildGraphPayload(response.getSuccess(), response.getGraphId(), response.getMessage());
    }

    // FIX: Wrapped response.getStateMap() with new HashMap<>(...)
    default GraphExecutionEventPayload toGraphExecutionEventPayload(org.sandbox.langgraph.grpc.ExecuteGraphResponse response) {
        return new GraphExecutionEventPayload(
                response.getEventType(),
                response.getNodeId(),
                response.getOutput(),
                response.getStateMap() != null ? new HashMap<>(response.getStateMap()) : null,
                response.getTimestamp(),
                response.getErrorMessage()
        );
    }

    // FIX: Wrapped response.getUpdatedStateMap() with new HashMap<>(...)
    default UpdateGraphStatePayload toUpdateGraphStatePayload(org.sandbox.langgraph.grpc.UpdateGraphStateResponse response) {
        return new UpdateGraphStatePayload(
                response.getSuccess(),
                response.getUpdatedStateMap() != null ? new HashMap<>(response.getUpdatedStateMap()) : null,
                "Graph state updated successfully"
        );
    }

    default DeleteGraphPayload toDeleteGraphPayload(org.sandbox.langgraph.grpc.DeleteGraphResponse response) {
        return new DeleteGraphPayload(response.getSuccess(), response.getMessage());
    }

    // ============== Helper Methods ==============

    default Map<String, String> convertToStringMap(Map<String, Object> map) {
        if (map == null) return null;
        return map.entrySet().stream()
                .collect(Collectors.toMap(
                        Map.Entry::getKey,
                        e -> e.getValue() != null ? e.getValue().toString() : null
                ));
    }
}
