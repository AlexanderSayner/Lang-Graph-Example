package org.sandbox.langgraph.store;

/**
 * Java equivalent of Python's EdgeDefinition.
 * Represents a connection between two nodes in the graph.
 */
public class EdgeDefinition {
    
    private String source;
    private String target;
    private String condition;
    
    public EdgeDefinition() {
    }
    
    public EdgeDefinition(String source, String target, String condition) {
        this.source = source;
        this.target = target;
        this.condition = condition;
    }
    
    // Getters and Setters
    
    public String getSource() {
        return source;
    }
    
    public void setSource(String source) {
        this.source = source;
    }
    
    public String getTarget() {
        return target;
    }
    
    public void setTarget(String target) {
        this.target = target;
    }
    
    public String getCondition() {
        return condition;
    }
    
    public void setCondition(String condition) {
        this.condition = condition;
    }
}
