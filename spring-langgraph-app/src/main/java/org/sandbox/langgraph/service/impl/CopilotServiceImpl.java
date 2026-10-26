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
import org.sandbox.langgraph.service.llm.LlmRequestCredentials;
import org.sandbox.langgraph.service.llm.RequestOrigin;
import org.springframework.stereotype.Service;

import java.util.Map;
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
    private final LlmRequestCredentials requestCredentials;

    @Override
    public @NonNull CopilotGraphPayload askCopilot(String graphId,
                                                   String threadId,
                                                   String message,
                                                   String copilotChatHistoryJson,
                                                   String selectedNodeId,
                                                   String selectedEdgeJson,
                                                   String copilotProvider,
                                                   String copilotModel) {
        log.debug("Copilot request for graph: {}, thread: {}", graphId, threadId);

        GraphViewData graphView = postgresGraphViewService.getGraphViewData(graphId);
        GraphHistoryPayload executionHistory = langGraphGrpcService.getExecutionHistory(graphId, threadId);

        // Resolved on THIS thread, before anything reactive (see LlmRequestCredentials).
        // Only the credential map and the policy flag are forwarded: the copilot's provider is
        // resolved engine-side, and it picks its own entry from that map. A B2B caller reaches
        // this method on a gRPC thread with no Spring Security session, so the lookup yields
        // nothing: under FALLBACK/REQUIRE the shared key is used - unchanged from before
        // personal keys existed - and under PERSONAL_ONLY the very same call is REFUSED here,
        // before the engine is involved, through this method's anonymous branch (the wording
        // is therefore the "sign in" one rather than the B2B-specific text the execute paths
        // produce, because nothing in this signature distinguishes the two transports).
        Map<String, String> runContext = requestCredentials.forRequest(RequestOrigin.GRAPHQL);

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
                    // Empty means "inherit": the Python service falls back to COPILOT_* / LLM_*.
                    .setCopilotProvider(copilotProvider != null ? copilotProvider : "")
                    .setCopilotModel(copilotModel != null ? copilotModel : "")
                    // Empty means "use the service settings" (config-based mode).
                    .setLlmCredentialsJson(
                            runContext.getOrDefault(LlmRequestCredentials.CREDENTIALS_JSON, ""))
                    .setRequireUserKey(
                            runContext.getOrDefault(LlmRequestCredentials.REQUIRE_USER_KEY, ""))
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
