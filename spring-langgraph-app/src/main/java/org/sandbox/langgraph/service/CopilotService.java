package org.sandbox.langgraph.service;

import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.dto.graphql.payload.CopilotGraphPayload;
import org.sandbox.langgraph.service.llm.RequestOrigin;

public interface CopilotService {

    /**
     * The interactive (GraphQL) form: no caller-supplied credentials - a browser client never
     * gets a credential channel, its keys are resolved from the session.
     */
    default @NonNull CopilotGraphPayload askCopilot(String graphId,
                                                    String threadId,
                                                    String message,
                                                    String copilotChatHistoryJson,
                                                    String selectedNodeId,
                                                    String selectedEdgeJson,
                                                    String copilotProvider,
                                                    String copilotModel) {
        return askCopilot(graphId, threadId, message, copilotChatHistoryJson, selectedNodeId,
                selectedEdgeJson, copilotProvider, copilotModel, RequestOrigin.GRAPHQL, null);
    }

    /**
     * @param origin                 GraphQL for the UI, B2B for the facade - decides whose keys
     *                               are resolved and whether a refusal applies
     * @param suppliedCredentialsJson the keys the caller attached to this call (B2B's own
     *                                {@code llm_credentials_json} field); null on the GraphQL path
     * @throws org.sandbox.langgraph.exception.LangGraphException.LlmKeyRequiredException when
     *         the deployment does not spend its shared key and no key was supplied
     */
    @NonNull CopilotGraphPayload askCopilot(String graphId,
                                            String threadId,
                                            String message,
                                            String copilotChatHistoryJson,
                                            String selectedNodeId,
                                            String selectedEdgeJson,
                                            String copilotProvider,
                                            String copilotModel,
                                            RequestOrigin origin,
                                            String suppliedCredentialsJson);
}
