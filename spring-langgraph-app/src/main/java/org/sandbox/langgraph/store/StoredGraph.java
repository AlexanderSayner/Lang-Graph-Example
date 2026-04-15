package org.sandbox.langgraph.store;

/**
 * Java equivalent of Python's StoredGraph.
 * Represents a graph stored in the system with metadata.
 */
public class StoredGraph {
    
    private GraphDefinition data;
    private String createdAt;
    private String status;
    
    public StoredGraph() {
    }
    
    public StoredGraph(GraphDefinition data, String createdAt, String status) {
        this.data = data;
        this.createdAt = createdAt;
        this.status = status;
    }
    
    // Getters and Setters
    
    public GraphDefinition getData() {
        return data;
    }
    
    public void setData(GraphDefinition data) {
        this.data = data;
    }
    
    public String getCreatedAt() {
        return createdAt;
    }
    
    public void setCreatedAt(String createdAt) {
        this.createdAt = createdAt;
    }
    
    public String getStatus() {
        return status;
    }
    
    public void setStatus(String status) {
        this.status = status;
    }
}
