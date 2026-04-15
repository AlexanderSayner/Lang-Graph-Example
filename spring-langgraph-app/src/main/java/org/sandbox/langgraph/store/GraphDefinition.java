package org.sandbox.langgraph.store;

import java.util.List;
import java.util.Map;

/**
 * Java equivalent of Python's GraphDefinition.
 * Represents the structure of a graph with nodes and edges.
 */
public class GraphDefinition {
    
    private String name;
    private List<NodeDefinition> nodes;
    private List<EdgeDefinition> edges;
    private Map<String, Object> config;
    
    public GraphDefinition() {
    }
    
    public GraphDefinition(String name, List<NodeDefinition> nodes, List<EdgeDefinition> edges, Map<String, Object> config) {
        this.name = name;
        this.nodes = nodes;
        this.edges = edges;
        this.config = config;
    }
    
    // Getters and Setters
    
    public String getName() {
        return name;
    }
    
    public void setName(String name) {
        this.name = name;
    }
    
    public List<NodeDefinition> getNodes() {
        return nodes;
    }
    
    public void setNodes(List<NodeDefinition> nodes) {
        this.nodes = nodes;
    }
    
    public List<EdgeDefinition> getEdges() {
        return edges;
    }
    
    public void setEdges(List<EdgeDefinition> edges) {
        this.edges = edges;
    }
    
    public Map<String, Object> getConfig() {
        return config;
    }
    
    public void setConfig(Map<String, Object> config) {
        this.config = config;
    }
}
