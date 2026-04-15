package org.sandbox.langgraph.service;

import org.sandbox.langgraph.graph.*;
import org.sandbox.langgraph.llm.YandexGptClient;
import org.sandbox.langgraph.store.GraphStore;
import org.sandbox.langgraph.store.StoredGraph;
import org.sandbox.langgraph.store.NodeDefinition;
import org.sandbox.langgraph.store.EdgeDefinition;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Java-native graph orchestration service using LangGraph4j-style patterns.
 * Replaces the Python LangGraphServiceServicer with a pure Java implementation.
 */
@Service
public class LangGraphOrchestrator {
    
    private static final Logger log = LoggerFactory.getLogger(LangGraphOrchestrator.class);
    
    private final YandexGptClient yandexGptClient;
    private final Map<String, StateGraph.CompiledGraph> compiledGraphs = new ConcurrentHashMap<>();
    private final GraphStore store;
    
    public LangGraphOrchestrator(
            YandexGptClient yandexGptClient,
            GraphStore store) {
        this.yandexGptClient = yandexGptClient;
        this.store = store;
    }
    
    /**
     * Build and compile a graph from stored definition.
     * Equivalent to BuildGraph in Python service.
     */
    public Mono<StateGraph.CompiledGraph> buildGraph(String graphId) {
        return Mono.fromCallable(() -> store.getGraph(graphId))
                .switchIfEmpty(Mono.error(new IllegalArgumentException("Graph not found: " + graphId)))
                .map(graphDefinition -> {
                    log.info("Building graph '{}' with {} nodes", graphId, graphDefinition.getNodes().size());
                    
                    StateGraph workflow = new StateGraph();
                    
                    // Add nodes
                    for (var node : graphDefinition.getNodes()) {
                        NodeHandler handler = createNodeHandler(node.getNodeId(), node.getMetadata());
                        workflow.addNode(node.getNodeId(), handler);
                    }
                    
                    // Add edges
                    for (var edge : graphDefinition.getEdges()) {
                        if (edge.getCondition() == null || edge.getCondition().isEmpty()) {
                            workflow.addEdge(edge.getSource(), edge.getTarget());
                        } else {
                            log.warn("Conditional edge from {} ignored (requires custom routing)", 
                                    edge.getSource());
                        }
                    }
                    
                    // Set entry point
                    if (!graphDefinition.getNodes().isEmpty()) {
                        workflow.setEntryPoint(graphDefinition.getNodes().get(0).getNodeId());
                    }
                    
                    // Compile and cache
                    StateGraph.CompiledGraph compiled = workflow.compile();
                    compiledGraphs.put(graphId, compiled);
                    
                    return compiled;
                });
    }
    
    /**
     * Create a node handler that calls YandexGPT.
     * Equivalent to the handler in Python's langgraph_servicer.py
     */
    private NodeHandler createNodeHandler(String nodeId, Map<String, Object> metadata) {
        return state -> {
            log.info("Executing node: {}", nodeId);
            
            String userInput = state.getInput() != null ? state.getInput() : "";
            String systemPrompt = metadata != null && metadata.containsKey("system_prompt") 
                    ? (String) metadata.get("system_prompt")
                    : "You are a helpful assistant and a pro developer.";
            
            return yandexGptClient.generate(userInput, systemPrompt)
                    .map(responseText -> {
                        log.info("Node {} response received", nodeId);
                        
                        GraphState result = new GraphState();
                        result.setLastNode(nodeId);
                        result.setOutput(responseText);
                        
                        Map<String, Object> historyEntry = new HashMap<>();
                        historyEntry.put("node", nodeId);
                        historyEntry.put("output", responseText);
                        result.addToHistory(historyEntry);
                        
                        return result;
                    })
                    .onErrorResume(e -> {
                        log.error("Node execution failed: {}", e.getMessage());
                        
                        GraphState errorState = new GraphState();
                        errorState.setLastNode(nodeId);
                        errorState.setOutput("Error: " + e.getMessage());
                        
                        Map<String, Object> historyEntry = new HashMap<>();
                        historyEntry.put("node", nodeId);
                        historyEntry.put("error", e.getMessage());
                        errorState.addToHistory(historyEntry);
                        
                        return Mono.just(errorState);
                    });
        };
    }
    
    /**
     * Execute a compiled graph with streaming.
     * Equivalent to ExecuteGraph in Python service.
     */
    public Flux<GraphState> executeGraphStream(String graphId, String input, Map<String, Object> context) {
        StateGraph.CompiledGraph compiledGraph = compiledGraphs.get(graphId);
        
        if (compiledGraph == null) {
            return Flux.error(new IllegalArgumentException("Graph not found or not compiled: " + graphId));
        }
        
        GraphState initialState = new GraphState();
        initialState.setInput(input);
        initialState.setContext(context != null ? context : new HashMap<>());
        initialState.setTimestamp(System.currentTimeMillis());
        
        log.info("Executing graph '{}' with input: {}", graphId, input);
        
        return compiledGraph.stream(initialState);
    }
    
    /**
     * Execute a graph and return only the final result.
     */
    public Mono<GraphState> executeGraph(String graphId, String input, Map<String, Object> context) {
        StateGraph.CompiledGraph compiledGraph = compiledGraphs.get(graphId);
        
        if (compiledGraph == null) {
            return Mono.error(new IllegalArgumentException("Graph not found or not compiled: " + graphId));
        }
        
        GraphState initialState = new GraphState();
        initialState.setInput(input);
        initialState.setContext(context != null ? context : new HashMap<>());
        initialState.setTimestamp(System.currentTimeMillis());
        
        return compiledGraph.invoke(initialState);
    }
    
    /**
     * Get a compiled graph by ID.
     */
    public StateGraph.CompiledGraph getCompiledGraph(String graphId) {
        return compiledGraphs.get(graphId);
    }
    
    /**
     * Check if a graph is compiled.
     */
    public boolean isGraphCompiled(String graphId) {
        return compiledGraphs.containsKey(graphId);
    }
    
    /**
     * Remove a compiled graph from cache.
     */
    public void removeCompiledGraph(String graphId) {
        compiledGraphs.remove(graphId);
        log.debug("Removed compiled graph: {}", graphId);
    }
}
