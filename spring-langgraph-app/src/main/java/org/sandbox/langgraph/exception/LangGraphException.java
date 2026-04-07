package org.sandbox.langgraph.exception;

import lombok.Getter;

@Getter
public sealed class LangGraphException extends RuntimeException permits
        LangGraphException.GraphNotFoundException,
        LangGraphException.GraphBuildException,
        LangGraphException.GraphExecutionException,
        LangGraphException.GraphStateException {

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
}
