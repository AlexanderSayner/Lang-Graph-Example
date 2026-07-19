package org.sandbox.langgraph.service;

import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.dto.graphql.payload.CopilotGraphPayload;

public interface CopilotService {
    @NonNull CopilotGraphPayload askCopilot(String graphId,
                                            String threadId,
                                            String message,
                                            String copilotChatHistoryJson,
                                            String selectedNodeId,
                                            String selectedEdgeJson);
}
