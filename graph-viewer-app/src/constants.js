// --- Config ---
const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
export const API_URL = isLocalhost
    ? "http://localhost:9191/graphql"
    : `http://${window.location.hostname}:9191/graphql`;

// --- Helpers ---
export const fetchGraphQL = async (query, variables) => {
    const response = await fetch(API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, variables })
    });
    const json = await response.json();
    if (json.errors) throw new Error(json.errors[0].message);
    return json.data;
};

// --- Queries ---
export const LIST_QUERY = `query { listGraphs(pageSize: 100) { graphs { graphId graphName nodeCount } } }`;
export const VIEW_QUERY = `query Get($graphId: String!) { getGraphView(graphId: $graphId) { nodes { nodeId nodeType metadata position { x y } } edges { source target condition } } }`;

export const HISTORY_QUERY = `query History($graphId: String!, $threadId: String!) {
    getExecutionHistory(graphId: $graphId, threadId: $threadId) {
        success
        history {
            nodeId
            stateJson
            timestamp
            diff {
                added
                removed
                modified
                summary
            }
            tokensUsed
            totalTokens
        }
        errorMessage
    }
}`;

export const EXECUTE_MUTATION = `
    mutation Exec($input: ExecuteGraphInput!) {
        executeGraph(input: $input) {
            output
            eventType
            state
            totalTokens
            errorMessage
        }
    }`;

export const SAVE_MUTATION = `mutation Save($graphId: String!, $positions: [NodePositionInput!]!) { saveGraphCoordinates(graphId: $graphId, positions: $positions) { success } }`;
export const DELETE_MUTATION = `mutation Del($graphId: String!) { deleteGraph(graphId: $graphId) { success } }`;

export const REWIND_MUTATION = `
    mutation Rewind($graphId: String!, $threadId: String!, $stateJson: String!, $targetNodeId: String) {
        rewindGraph(graphId: $graphId, threadId: $threadId, stateJson: $stateJson, targetNodeId: $targetNodeId) { success message }
    }
`;

export const COPILOT_MUTATION = `
    mutation AskCopilot($graphId: String!, $threadId: String!, $message: String!, $chatHistory: String!, $selectedNodeId: String, $selectedEdgeJson: String) {
        askCopilot(graphId: $graphId, threadId: $threadId, message: $message, copilotChatHistoryJson: $chatHistory, selectedNodeId: $selectedNodeId, selectedEdgeJson: $selectedEdgeJson) {
            success
            aiResponse
            totalTokens
            errorMessage
        }
    }
`;

// --- Layout Logic ---
export const layoutGraph = (nodes, edges) => {
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: 'TB', nodesep: 150, ranksep: 120 });
    g.setDefaultEdgeLabel(() => ({}));
    nodes.forEach(n => g.setNode(n.id, { width: 220, height: 80 }));
    edges.forEach(e => g.setEdge(e.source, e.target));
    dagre.layout(g);
    return nodes.map(n => {
        const pos = g.node(n.id);
        return { ...n, position: { x: pos.x - 110, y: pos.y - 40 } };
    });
};
