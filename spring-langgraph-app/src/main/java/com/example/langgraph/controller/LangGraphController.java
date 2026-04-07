package com.example.langgraph.controller;

import com.example.langgraph.dto.BuildGraphRequest;
import com.example.langgraph.dto.ExecuteGraphRequest;
import com.example.langgraph.grpc.*;
import com.example.langgraph.service.LangGraphGrpcService;
import graphql.schema.DataFetchingEnvironment;
import org.reactivestreams.Publisher;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.graphql.data.method.annotation.Argument;
import org.springframework.graphql.data.method.annotation.MutationMapping;
import org.springframework.graphql.data.method.annotation.QueryMapping;
import org.springframework.graphql.data.method.annotation.SubscriptionMapping;
import org.springframework.stereotype.Controller;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * GraphQL Controller for LangGraph operations.
 */
@Controller
public class LangGraphController {

    private static final Logger log = LoggerFactory.getLogger(LangGraphController.class);

    private final LangGraphGrpcService langGraphService;

    public LangGraphController(LangGraphGrpcService langGraphService) {
        this.langGraphService = langGraphService;
    }

    @QueryMapping
    public Mono<Map<String, Object>> listGraphs(
            @Argument(defaultValue = "10") int pageSize,
            @Argument String pageToken) {
        
        return langGraphService.listGraphs(pageSize, pageToken)
                .map(response -> {
                    Map<String, Object> result = new HashMap<>();
                    List<Map<String, Object>> graphs = response.getGraphsList().stream()
                            .map(graph -> {
                                Map<String, Object> graphMap = new HashMap<>();
                                graphMap.put("graphId", graph.getGraphId());
                                graphMap.put("graphName", graph.getGraphName());
                                graphMap.put("nodeCount", graph.getNodeCount());
                                graphMap.put("createdAt", graph.getCreatedAt());
                                graphMap.put("status", graph.getStatus());
                                return graphMap;
                            })
                            .collect(Collectors.toList());
                    
                    result.put("graphs", graphs);
                    result.put("nextPageToken", response.getNextPageToken());
                    result.put("totalCount", graphs.size());
                    return result;
                });
    }

    @QueryMapping
    public Mono<Map<String, Object>> getGraphState(
            @Argument String graphId,
            @Argument String threadId) {
        
        return langGraphService.getGraphState(graphId, threadId)
                .map(response -> {
                    Map<String, Object> result = new HashMap<>();
                    result.put("success", response.getSuccess());
                    result.put("state", response.getStateMap());
                    result.put("currentNode", response.getCurrentNode());
                    result.put("nodeHistory", response.getNodeHistoryList());
                    return result;
                });
    }

    @MutationMapping
    public Mono<Map<String, Object>> buildGraph(
            @Argument String graphId,
            @Argument String graphName,
            @Argument List<Map<String, Object>> nodes,
            @Argument List<Map<String, Object>> edges,
            @Argument Map<String, String> config) {
        
        BuildGraphRequest request = BuildGraphRequest.builder()
                .graphId(graphId)
                .graphName(graphName)
                .nodes(nodes != null ? nodes.stream()
                        .map(node -> BuildGraphRequest.NodeDefinition.builder()
                                .nodeId((String) node.get("nodeId"))
                                .nodeType((String) node.get("nodeType"))
                                .handlerName((String) node.get("handlerName"))
                                .metadata((Map<String, String>) node.get("metadata"))
                                .build())
                        .collect(Collectors.toList()) : new ArrayList<>())
                .edges(edges != null ? edges.stream()
                        .map(edge -> BuildGraphRequest.EdgeDefinition.builder()
                                .source((String) edge.get("source"))
                                .target((String) edge.get("target"))
                                .condition((String) edge.get("condition"))
                                .build())
                        .collect(Collectors.toList()) : new ArrayList<>())
                .config(config)
                .build();
        
        return langGraphService.buildGraph(request)
                .map(response -> {
                    Map<String, Object> result = new HashMap<>();
                    result.put("success", response.getSuccess());
                    result.put("graphId", response.getGraphId());
                    result.put("message", response.getMessage());
                    return result;
                });
    }

    @MutationMapping
    public Mono<Map<String, Object>> executeGraph(
            @Argument String graphId,
            @Argument String input,
            @Argument Map<String, String> context) {
        
        ExecuteGraphRequest request = ExecuteGraphRequest.builder()
                .graphId(graphId)
                .input(input)
                .context(context)
                .streamOutput(false)
                .build();
        
        // For non-streaming execution, we collect all events and return the final result
        return langGraphService.executeGraphStream(request)
                .reduce(new HashMap<String, Object>(), (acc, event) -> {
                    acc.put("output", event.getOutput());
                    acc.put("state", event.getStateMap());
                    if (event.hasErrorMessage() && !event.getErrorMessage().isEmpty()) {
                        acc.put("errorMessage", event.getErrorMessage());
                    }
                    acc.put("success", !"ERROR".equals(event.getEventType()));
                    return acc;
                })
                .defaultIfEmpty(Map.of("success", true, "output", ""));
    }

    @SubscriptionMapping
    public Publisher<Map<String, Object>> executeGraphStream(
            @Argument String graphId,
            @Argument String input,
            @Argument Map<String, String> context) {
        
        ExecuteGraphRequest request = ExecuteGraphRequest.builder()
                .graphId(graphId)
                .input(input)
                .context(context)
                .streamOutput(true)
                .build();
        
        return langGraphService.executeGraphStream(request)
                .map(event -> {
                    Map<String, Object> result = new HashMap<>();
                    result.put("eventType", event.getEventType());
                    result.put("nodeId", event.getNodeId());
                    result.put("output", event.getOutput());
                    result.put("state", event.getStateMap());
                    result.put("timestamp", event.getTimestamp());
                    if (event.hasErrorMessage() && !event.getErrorMessage().isEmpty()) {
                        result.put("errorMessage", event.getErrorMessage());
                    }
                    return result;
                });
    }

    @MutationMapping
    public Mono<Map<String, Object>> updateGraphState(
            @Argument String graphId,
            @Argument String threadId,
            @Argument Map<String, String> stateUpdates) {
        
        return langGraphService.updateGraphState(graphId, threadId, stateUpdates)
                .map(response -> {
                    Map<String, Object> result = new HashMap<>();
                    result.put("success", response.getSuccess());
                    result.put("updatedState", response.getUpdatedStateMap());
                    result.put("message", "Graph state updated successfully");
                    return result;
                });
    }

    @MutationMapping
    public Mono<Map<String, Object>> deleteGraph(@Argument String graphId) {
        
        return langGraphService.deleteGraph(graphId)
                .map(response -> {
                    Map<String, Object> result = new HashMap<>();
                    result.put("success", response.getSuccess());
                    result.put("message", response.getMessage());
                    return result;
                });
    }
}
