package org.sandbox.langgraph.graph;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.ArrayList;

/**
 * Represents the state of a graph execution.
 * Equivalent to the GraphState TypedDict in Python.
 */
public class GraphState {
    
    private String input;
    private Map<String, Object> context;
    private Long timestamp;
    private String lastNode;
    private String output;
    private List<Map<String, Object>> history;

    public GraphState() {
        this.context = new HashMap<>();
        this.history = new ArrayList<>();
    }

    // Getters and Setters
    
    public String getInput() {
        return input;
    }

    public void setInput(String input) {
        this.input = input;
    }

    public Map<String, Object> getContext() {
        return context;
    }

    public void setContext(Map<String, Object> context) {
        this.context = context;
    }

    public Long getTimestamp() {
        return timestamp;
    }

    public void setTimestamp(Long timestamp) {
        this.timestamp = timestamp;
    }

    public String getLastNode() {
        return lastNode;
    }

    public void setLastNode(String lastNode) {
        this.lastNode = lastNode;
    }

    public String getOutput() {
        return output;
    }

    public void setOutput(String output) {
        this.output = output;
    }

    public List<Map<String, Object>> getHistory() {
        return history;
    }

    public void setHistory(List<Map<String, Object>> history) {
        this.history = history;
    }

    /**
     * Add an entry to the history list.
     * Implements the "operator.add" behavior from Python.
     */
    public void addToHistory(Map<String, Object> entry) {
        if (this.history == null) {
            this.history = new ArrayList<>();
        }
        this.history.add(entry);
    }

    /**
     * Merge another state into this one.
     * Useful for combining node outputs.
     */
    public void merge(GraphState other) {
        if (other.getInput() != null) {
            this.input = other.getInput();
        }
        if (other.getContext() != null) {
            this.context.putAll(other.getContext());
        }
        if (other.getTimestamp() != null) {
            this.timestamp = other.getTimestamp();
        }
        if (other.getLastNode() != null) {
            this.lastNode = other.getLastNode();
        }
        if (other.getOutput() != null) {
            this.output = other.getOutput();
        }
        if (other.getHistory() != null) {
            this.history.addAll(other.getHistory());
        }
    }

    @Override
    public String toString() {
        return "GraphState{" +
                "input='" + input + '\'' +
                ", context=" + context +
                ", timestamp=" + timestamp +
                ", lastNode='" + lastNode + '\'' +
                ", output='" + output + '\'' +
                ", history=" + history +
                '}';
    }
}
