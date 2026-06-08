package org.sandbox.langgraph.service;

import org.sandbox.langgraph.dto.graphql.payload.CopilotGraphPayload;
import reactor.core.publisher.Mono;

public interface CopilotService {
    Mono<CopilotGraphPayload> askCopilot(String graphId,
                                         String threadId,
                                         String message,
                                         String copilotChatHistoryJson,
                                         String selectedNodeId,
                                         String selectedEdgeJson);
}
