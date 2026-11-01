package org.sandbox.langgraph.exception;

import lombok.Getter;

@Getter
public sealed class LangGraphException extends RuntimeException permits
        LangGraphException.GraphNotFoundException,
        LangGraphException.GraphBuildException,
        LangGraphException.GraphExecutionException,
        LangGraphException.GraphStateException,
        LangGraphException.LlmKeyRequiredException {

    private final String errorCode;

    protected LangGraphException(String message, String errorCode) {
        super(message);
        this.errorCode = errorCode;
    }

    protected LangGraphException(String message, String errorCode, Throwable cause) {
        super(message, cause);
        this.errorCode = errorCode;
    }

    public static final class GraphNotFoundException extends LangGraphException {
        public GraphNotFoundException(String graphId) {
            super("Graph not found: " + graphId, "GRAPH_NOT_FOUND");
        }
    }

    public static final class GraphBuildException extends LangGraphException {
        public GraphBuildException(String message) {
            super(message, "GRAPH_BUILD_FAILED");
        }

        public GraphBuildException(String message, Throwable cause) {
            super(message, "GRAPH_BUILD_FAILED", cause);
        }
    }

    public static final class GraphExecutionException extends LangGraphException {
        public GraphExecutionException(String message) {
            super(message, "GRAPH_EXECUTION_FAILED");
        }

        public GraphExecutionException(String message, Throwable cause) {
            super(message, "GRAPH_EXECUTION_FAILED", cause);
        }
    }

    public static final class GraphStateException extends LangGraphException {
        public GraphStateException(String message) {
            super(message, "GRAPH_STATE_ERROR");
        }
    }

    /**
     * The caller must bring its own LLM key before this run may happen: an anonymous browser
     * under {@code PERSONAL_ONLY}, or - for the B2B facade - a request without
     * {@code llm_credentials_json} on a deployment that does not run on its shared service key.
     *
     * <p>Deliberately its own type rather than a plain {@code IllegalArgumentException}: this
     * one is <em>actionable by the caller alone</em>, so it keeps its detail on both surfaces
     * under the dedicated code {@code LLM_KEY_REQUIRED} - {@code BAD_REQUEST} from GraphQL,
     * {@code FAILED_PRECONDITION} from the B2B gRPC facade - where a generic invalid-argument
     * would make an integrator hunt for what was wrong with the <em>shape</em> of the request
     * instead of the one missing thing.</p>
     */
    public static final class LlmKeyRequiredException extends LangGraphException {
        public LlmKeyRequiredException(String message) {
            super(message, "LLM_KEY_REQUIRED");
        }
    }
}
