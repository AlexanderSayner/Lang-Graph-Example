package org.sandbox.langgraph.service.impl;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.sandbox.langgraph.dto.graphql.payload.CopilotGraphPayload;
import org.sandbox.langgraph.dto.graphql.payload.GraphHistoryPayload;
import org.sandbox.langgraph.dto.graphql.payload.redis.GraphViewData;
import org.sandbox.langgraph.grpc.CopilotRequest;
import org.sandbox.langgraph.grpc.LangGraphServiceGrpc;
import org.sandbox.langgraph.mapper.GraphGrpcMapper;
import org.sandbox.langgraph.service.CopilotService;
import org.sandbox.langgraph.service.LangGraphGrpcService;
import org.sandbox.langgraph.service.PostgresGraphViewService;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.concurrent.TimeUnit;

@Service
@Slf4j
@RequiredArgsConstructor
public class CopilotServiceImpl implements CopilotService {
    private final LangGraphServiceGrpc.LangGraphServiceFutureStub futureStub;
    private final LangGraphGrpcService langGraphGrpcService;
    private final PostgresGraphViewService postgresGraphViewService;
    private final GraphGrpcMapper mapper;
    private final ObjectMapper objectMapper;

    @Override
    public Mono<CopilotGraphPayload> askCopilot(String graphId,
                                                String threadId,
                                                String message,
                                                String copilotChatHistoryJson,
                                                String selectedNodeId,
                                                String selectedEdgeJson) {
        log.debug("Copilot request for graph: {}, thread: {}", graphId, threadId);

        return Mono.zip(
                postgresGraphViewService.getGraphViewData(graphId),
                langGraphGrpcService.getExecutionHistory(graphId, threadId)
        ).flatMap(tuple -> {
            GraphViewData graphView = tuple.getT1();
            GraphHistoryPayload executionHistory = tuple.getT2();

            try {
                String graphContextJson = objectMapper.writeValueAsString(graphView);
                String executionHistoryJson = objectMapper.writeValueAsString(executionHistory);

                return Mono.fromCallable(() -> {
                            CopilotRequest request = CopilotRequest.newBuilder()
                                    .setUserMessage(message)
                                    .setGraphContextJson(graphContextJson)
                                    .setExecutionHistoryJson(executionHistoryJson)
                                    .setCopilotChatHistoryJson(copilotChatHistoryJson != null ? copilotChatHistoryJson : "[]")
                                    .setSelectedNodeId(selectedNodeId != null ? selectedNodeId : "")
                                    .setSelectedEdgeJson(selectedEdgeJson != null ? selectedEdgeJson : "")
                                    .build();

                            return futureStub.askCopilot(request).get(30, TimeUnit.SECONDS);
                        })
                        .map(mapper::toCopilotGraphPayload)
                        .doOnError(e -> log.error("Python Copilot call failed", e))
                        .subscribeOn(Schedulers.boundedElastic());

            } catch (JsonProcessingException e) {
                log.error("Failed to serialize context for Copilot", e);
                return Mono.error(new RuntimeException("Failed to prepare context for AI"));
            }
        });
    }
}
