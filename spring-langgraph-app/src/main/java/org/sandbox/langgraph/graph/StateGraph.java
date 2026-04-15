package org.sandbox.langgraph.graph;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.util.*;
import java.util.function.Function;

/**
 * Java implementation of a state graph workflow, similar to LangGraph's StateGraph.
 * This is a simplified LangGraph4j-style implementation for orchestrating LLM workflows.
 */
public class StateGraph {
    
    private static final Logger log = LoggerFactory.getLogger(StateGraph.class);
    
    private final Map<String, NodeHandler> nodes = new LinkedHashMap<>();
    private final Map<String, List<String>> edges = new HashMap<>();
    private String entryPoint;
    
    public StateGraph() {
        // Default constructor
    }
    
    /**
     * Add a node to the graph.
     * 
     * @param nodeId Unique identifier for the node
     * @param handler The handler function for this node
     * @return this builder for chaining
     */
    public StateGraph addNode(String nodeId, NodeHandler handler) {
        if (nodes.containsKey(nodeId)) {
            throw new IllegalArgumentException("Node with id '" + nodeId + "' already exists");
        }
        nodes.put(nodeId, handler);
        log.debug("Added node: {}", nodeId);
        return this;
    }
    
    /**
     * Add an edge between two nodes.
     * 
     * @param source Source node ID
     * @param target Target node ID
     * @return this builder for chaining
     */
    public StateGraph addEdge(String source, String target) {
        if (!nodes.containsKey(source)) {
            throw new IllegalArgumentException("Source node '" + source + "' does not exist");
        }
        if (!nodes.containsKey(target) && !target.equals("__end__")) {
            throw new IllegalArgumentException("Target node '" + target + "' does not exist");
        }
        
        edges.computeIfAbsent(source, k -> new ArrayList<>()).add(target);
        log.debug("Added edge: {} -> {}", source, target);
        return this;
    }
    
    /**
     * Set the entry point of the graph.
     * 
     * @param nodeId The node ID to start from
     * @return this builder for chaining
     */
    public StateGraph setEntryPoint(String nodeId) {
        if (!nodes.containsKey(nodeId)) {
            throw new IllegalArgumentException("Entry point node '" + nodeId + "' does not exist");
        }
        this.entryPoint = nodeId;
        log.debug("Set entry point: {}", nodeId);
        return this;
    }
    
    /**
     * Compile the graph into an executable workflow.
     * 
     * @return CompiledGraph ready for execution
     */
    public CompiledGraph compile() {
        if (entryPoint == null) {
            throw new IllegalStateException("Entry point not set. Call setEntryPoint() first.");
        }
        if (nodes.isEmpty()) {
            throw new IllegalStateException("Graph has no nodes");
        }
        
        log.info("Compiling graph with {} nodes and entry point '{}'", nodes.size(), entryPoint);
        return new CompiledGraph(this);
    }
    
    // Package-private getters for CompiledGraph
    Map<String, NodeHandler> getNodes() {
        return Collections.unmodifiableMap(nodes);
    }
    
    Map<String, List<String>> getEdges() {
        return Collections.unmodifiableMap(edges);
    }
    
    String getEntryPoint() {
        return entryPoint;
    }
    
    /**
     * Compiled, executable graph.
     */
    public static class CompiledGraph {
        private final StateGraph graph;
        
        CompiledGraph(StateGraph graph) {
            this.graph = graph;
        }
        
        /**
         * Execute the graph with initial state.
         * Returns the final state after all nodes have executed.
         * 
         * @param initialState The starting state
         * @return Mono containing the final state
         */
        public Mono<GraphState> invoke(GraphState initialState) {
            return executeFrom(initialState, graph.entryPoint)
                    .last()
                    .switchIfEmpty(Mono.just(initialState));
        }
        
        /**
         * Stream the execution, emitting state after each node.
         * Useful for real-time updates and progress tracking.
         * 
         * @param initialState The starting state
         * @return Flux of intermediate states
         */
        public Flux<GraphState> stream(GraphState initialState) {
            return executeFrom(initialState, graph.entryPoint);
        }
        
        private Flux<GraphState> executeFrom(GraphState state, String currentNodeId) {
            return Flux.defer(() -> {
                if (currentNodeId == null || currentNodeId.equals("__end__")) {
                    log.debug("Graph execution completed");
                    return Flux.empty();
                }
                
                NodeHandler handler = graph.nodes.get(currentNodeId);
                if (handler == null) {
                    log.error("Node '{}' not found", currentNodeId);
                    return Flux.error(new IllegalStateException("Node '" + currentNodeId + "' not found"));
                }
                
                log.debug("Executing node: {}", currentNodeId);
                
                return handler.handle(state)
                        .flatMapMany(updatedState -> {
                            // Merge the returned state with current state
                            GraphState mergedState = new GraphState();
                            mergedState.merge(state);
                            mergedState.merge(updatedState);
                            mergedState.setLastNode(currentNodeId);
                            
                            // Determine next node
                            List<String> targets = graph.edges.get(currentNodeId);
                            String nextNode = null;
                            if (targets != null && !targets.isEmpty()) {
                                nextNode = targets.get(0); // Simple linear flow
                            } else if (updatedState.getLastNode() != null) {
                                // Check if there's an edge from the updated last_node
                                targets = graph.edges.get(updatedState.getLastNode());
                                if (targets != null && !targets.isEmpty()) {
                                    nextNode = targets.get(0);
                                }
                            }
                            
                            log.debug("Node {} completed, next: {}", currentNodeId, 
                                    nextNode != null ? nextNode : "END");
                            
                            // Emit current state and continue
                            if (nextNode != null && !nextNode.equals("__end__")) {
                                return Flux.concat(
                                        Flux.just(mergedState),
                                        executeFrom(mergedState, nextNode)
                                );
                            } else {
                                return Flux.just(mergedState);
                            }
                        });
            });
        }
    }
}
