package org.sandbox.langgraph.model;

import org.sandbox.langgraph.core.model.ExecutionEventType;

import java.util.Map;

public sealed interface GraphEvent permits GraphEvent.Start, GraphEvent.NodeStart, GraphEvent.NodeEnd, GraphEvent.End, GraphEvent.WaitingForInput, GraphEvent.Error {

    String graphId();
    long timestamp();

    record Start(String graphId, long timestamp) implements GraphEvent {}

    record NodeStart(String graphId, String nodeId, long timestamp) implements GraphEvent {}

    record NodeEnd(String graphId, String nodeId, String output, Map<String, String> state, long timestamp) implements GraphEvent {}

    record End(String graphId, Map<String, String> finalState, long timestamp) implements GraphEvent {}

    record WaitingForInput(String graphId, String nodeId, String output, long timestamp) implements GraphEvent {}

    record Error(String graphId, String nodeId, String errorMessage, long timestamp) implements GraphEvent {}

    static GraphEvent fromEventType(ExecutionEventType eventType, String graphId, String nodeId,
                                    String output, Map<String, String> state,
                                    String errorMessage, long timestamp) {
        return switch (eventType) {
            case START -> new Start(graphId, timestamp);
            case NODE_START -> new NodeStart(graphId, nodeId, timestamp);
            case NODE_END -> new NodeEnd(graphId, nodeId, output, state, timestamp);
            case END -> new End(graphId, state, timestamp);
            case WAITING_FOR_INPUT -> new WaitingForInput(graphId, nodeId, output, timestamp);
            case ERROR -> new Error(graphId, nodeId, errorMessage, timestamp);
        };
    }
}
