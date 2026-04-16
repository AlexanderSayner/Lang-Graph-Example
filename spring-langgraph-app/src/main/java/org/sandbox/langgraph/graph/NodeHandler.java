package org.sandbox.langgraph.graph;

import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.util.List;
import java.util.Map;

/**
 * Represents a node in the graph workflow.
 * Equivalent to node handlers in Python LangGraph.
 */
@FunctionalInterface
public interface NodeHandler {
    
    /**
     * Process the current state and return updates.
     * 
     * @param currentState The current graph state
     * @return Mono containing state updates to merge
     */
    Mono<GraphState> handle(GraphState currentState);
    
    /**
     * Create a simple node handler from a function.
     */
    static NodeHandler of(NodeHandler handler) {
        return handler;
    }
    
    /**
     * Chain two handlers together.
     */
    default NodeHandler andThen(NodeHandler after) {
        return state -> this.handle(state)
                .flatMap(intermediate -> after.handle(intermediate));
    }
}
