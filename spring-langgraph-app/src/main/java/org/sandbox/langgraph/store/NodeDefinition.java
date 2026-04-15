package org.sandbox.langgraph.store;

import java.util.Map;

/**
 * Java equivalent of Python's NodeDefinition.
 * Represents a single node in the graph.
 */
public class NodeDefinition {
    
    private String nodeId;
    private String nodeType;
    private String handlerName;
    private Map<String, Object> metadata;
    
    public NodeDefinition() {
    }
    
    public NodeDefinition(String nodeId, String nodeType, String handlerName, Map<String, Object> metadata) {
        this.nodeId = nodeId;
        this.nodeType = nodeType;
        this.handlerName = handlerName;
        this.metadata = metadata;
    }
    
    // Getters and Setters
    
    public String getNodeId() {
        return nodeId;
    }
    
    public void setNodeId(String nodeId) {
        this.nodeId = nodeId;
    }
    
    public String getNodeType() {
        return nodeType;
    }
    
    public void setNodeType(String nodeType) {
        this.nodeType = nodeType;
    }
    
    public String getHandlerName() {
        return handlerName;
    }
    
    public void setHandlerName(String handlerName) {
        this.handlerName = handlerName;
    }
    
    public Map<String, Object> getMetadata() {
        return metadata;
    }
    
    public void setMetadata(Map<String, Object> metadata) {
        this.metadata = metadata;
    }
}
