package org.sandbox.langgraph.store;

import java.time.Instant;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Java equivalent of Python's GraphStore.
 * Thread-safe in-memory store for managing graph definitions and state.
 */
public class GraphStore {
    
    private final Map<String, StoredGraph> graphs = new ConcurrentHashMap<>();
    private final Map<String, Map<String, Object>> states = new ConcurrentHashMap<>();
    
    /**
     * Add a graph definition to the store.
     */
    public void addGraph(String graphId, GraphDefinition graphData) {
        StoredGraph storedGraph = new StoredGraph(
                graphData,
                Instant.now().toString(),
                "active"
        );
        graphs.put(graphId, storedGraph);
        // Log would go here via SLF4J
    }
    
    /**
     * Get a stored graph by ID.
     */
    public StoredGraph getGraph(String graphId) {
        return graphs.get(graphId);
    }
    
    /**
     * Delete a graph from the store.
     * @return true if deleted, false if not found
     */
    public boolean deleteGraph(String graphId) {
        if (graphs.containsKey(graphId)) {
            graphs.remove(graphId);
            return true;
        }
        return false;
    }
    
    /**
     * List graphs with pagination.
     * @param pageSize Number of graphs to return
     * @param pageToken Token for pagination (index as string)
     * @return Pair of graph list and next page token
     */
    public PageResult<StoredGraph> listGraphs(int pageSize, String pageToken) {
        List<Map.Entry<String, StoredGraph>> graphList = new ArrayList<>(graphs.entrySet());
        int startIdx = 0;
        
        if (pageToken != null && !pageToken.isEmpty()) {
            try {
                startIdx = Integer.parseInt(pageToken);
            } catch (NumberFormatException e) {
                startIdx = 0;
            }
        }
        
        int endIdx = Math.min(startIdx + pageSize, graphList.size());
        String nextToken = endIdx < graphList.size() ? String.valueOf(endIdx) : "";
        
        List<GraphEntry<StoredGraph>> result = new ArrayList<>();
        for (int i = startIdx; i < endIdx; i++) {
            var entry = graphList.get(i);
            result.add(new GraphEntry<>(entry.getKey(), entry.getValue()));
        }
        
        return new PageResult<>(result, nextToken, graphList.size());
    }
    
    /**
     * Update state for a specific graph and thread.
     */
    public Map<String, Object> updateState(String graphId, String threadId, Map<String, Object> stateUpdates) {
        String key = graphId + ":" + threadId;
        Map<String, Object> currentState = states.computeIfAbsent(key, k -> new HashMap<>());
        currentState.putAll(stateUpdates);
        return currentState;
    }
    
    /**
     * Get state for a specific graph and thread.
     */
    public Map<String, Object> getState(String graphId, String threadId) {
        String key = graphId + ":" + threadId;
        return states.getOrDefault(key, new HashMap<>());
    }
    
    /**
     * Clear all stored data (useful for testing).
     */
    public void clear() {
        graphs.clear();
        states.clear();
    }
    
    /**
     * Helper class for paginated results.
     */
    public static class PageResult<T> {
        private final List<GraphEntry<T>> items;
        private final String nextPageToken;
        private final int totalCount;
        
        public PageResult(List<GraphEntry<T>> items, String nextPageToken, int totalCount) {
            this.items = items;
            this.nextPageToken = nextPageToken;
            this.totalCount = totalCount;
        }
        
        public List<GraphEntry<T>> getItems() {
            return items;
        }
        
        public String getNextPageToken() {
            return nextPageToken;
        }
        
        public int getTotalCount() {
            return totalCount;
        }
    }
    
    /**
     * Helper class for graph entries with IDs.
     */
    public static class GraphEntry<T> {
        private final String id;
        private final T value;
        
        public GraphEntry(String id, T value) {
            this.id = id;
            this.value = value;
        }
        
        public String getId() {
            return id;
        }
        
        public T getValue() {
            return value;
        }
    }
}
