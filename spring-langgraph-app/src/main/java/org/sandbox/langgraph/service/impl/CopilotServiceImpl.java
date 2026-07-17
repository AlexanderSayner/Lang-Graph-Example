package org.sandbox.langgraph.service.impl;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.dto.graphql.payload.CopilotGraphPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphHistoryPayload;
import org.sandbox.langgraph.dto.graphql.payload.redis.GraphViewData;
import org.sandbox.langgraph.grpc.CopilotRequest;
import org.sandbox.langgraph.grpc.CopilotResponse;
import org.sandbox.langgraph.grpc.LangGraphServiceGrpc;
import org.sandbox.langgraph.mapper.GraphGrpcMapper;
import org.sandbox.langgraph.service.CopilotService;
import org.sandbox.langgraph.service.LangGraphGrpcService;
import org.sandbox.langgraph.service.PostgresGraphViewService;
import org.springframework.stereotype.Service;

import java.util.concurrent.TimeUnit;

@Service
@Slf4j
@RequiredArgsConstructor
public class CopilotServiceImpl implements CopilotService {

    private final LangGraphServiceGrpc.LangGraphServiceBlockingStub blockingStub;
    private final LangGraphGrpcService langGraphGrpcService;
    private final PostgresGraphViewService postgresGraphViewService;
    private final GraphGrpcMapper mapper;
    private final ObjectMapper objectMapper;

    @Override
    public @NonNull CopilotGraphPayload askCopilot(String graphId,
                                                   String threadId,
                                                   String message,
                                                   String copilotChatHistoryJson,
                                                   String selectedNodeId,
                                                   String selectedEdgeJson) {
        log.debug("Copilot request for graph: {}, thread: {}", graphId, threadId);

        GraphViewData graphView = postgresGraphViewService.getGraphViewData(graphId);
        GraphHistoryPayload executionHistory = langGraphGrpcService.getExecutionHistory(graphId, threadId);

        try {
            String graphContextJson = objectMapper.writeValueAsString(graphView);
            String executionHistoryJson = objectMapper.writeValueAsString(executionHistory);

            CopilotRequest request = CopilotRequest.newBuilder()
                    .setUserMessage(message)
                    .setGraphContextJson(graphContextJson)
                    .setExecutionHistoryJson(executionHistoryJson)
                    .setCopilotChatHistoryJson(copilotChatHistoryJson != null ? copilotChatHistoryJson : "[]")
                    .setSelectedNodeId(selectedNodeId != null ? selectedNodeId : "")
                    .setSelectedEdgeJson(selectedEdgeJson != null ? selectedEdgeJson : "")
                    .build();

            CopilotResponse response = blockingStub.withDeadlineAfter(30, TimeUnit.SECONDS).askCopilot(request);
            return mapper.toCopilotGraphPayload(response);

        } catch (JsonProcessingException e) {
            log.error("Failed to serialize context for Copilot", e);
            throw new RuntimeException("Failed to prepare context for AI", e);
        } catch (Exception e) {
            log.error("Python Copilot call failed", e);
            throw new RuntimeException("Copilot service call failed: " + e.getMessage(), e);
        }
    }
}
