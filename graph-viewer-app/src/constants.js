import dagre from 'dagre';

// --- GraphQL endpoint resolution ---
// The same bundle can be served in several ways, and only some of them proxy /graphql:
//   1) vite dev server      - proxies /graphql to localhost:9191 (vite.config.js),
//   2) nginx in Docker      - proxies /graphql to spring-service:9191 (nginx.conf),
//   3) plain static hosting - no proxy at all, so /graphql falls through to the SPA index.html.
// In case 3 a raw `response.json()` failed with `Unexpected token '<' ... "<!DOCTYPE"`, therefore
// the endpoints are probed in order and the first one that really answers JSON is remembered.
// The Spring backend allows credentialed CORS, so the absolute URL works as a fallback - except
// when the UI itself is served over HTTPS (the browser would block it as mixed content; there the
// /graphql proxy of the web server is the way to go).
const SAME_ORIGIN_GRAPHQL_URL = `${window.location.origin}/graphql`;

// Prevent Mixed Content: only use the direct port fallback if we are NOT on HTTPS
const BACKEND_GRAPHQL_URL = window.location.protocol === 'https:'
    ? `https://${window.location.hostname}:9191/graphql`
    : `http://${window.location.hostname}:9191/graphql`;

const API_URL_CANDIDATES = [SAME_ORIGIN_GRAPHQL_URL, BACKEND_GRAPHQL_URL];

let resolvedApiUrl = null; // endpoint that answered JSON during this session

export const API_URL = SAME_ORIGIN_GRAPHQL_URL;

// --- Helpers ---
const postJson = async (url, query, variables) => {
    const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ query, variables })
    });
    const contentType = response.headers.get('content-type') || '';
    const text = await response.text();
    if (!contentType.includes('json')) {
        // Typical case: the frontend is served without a /graphql proxy and the SPA fallback
        // returned index.html instead of the API response.
        const preview = text.slice(0, 120).replace(/\s+/g, ' ').trim();
        throw new Error(`no JSON from ${url} (HTTP ${response.status}, ${contentType || 'no content-type'})`
            + (preview ? `: ${preview}` : ''));
    }
    return JSON.parse(text);
};

export const fetchGraphQL = async (query, variables) => {
    const candidates = resolvedApiUrl ? [resolvedApiUrl] : API_URL_CANDIDATES;
    const problems = [];

    for (const url of candidates) {
        let json;
        try {
            json = await postJson(url, query, variables);
        } catch (err) {
            // Transport / non-JSON answer: this endpoint is unusable, try the next one.
            problems.push(err.message);
            resolvedApiUrl = null;
            continue;
        }
        if (resolvedApiUrl !== url) {
            console.warn(`[graphql] using endpoint ${url}`);
            resolvedApiUrl = url;
        }
        if (json.errors) throw new Error(json.errors[0].message); // GraphQL-level error, not transport
        return json.data;
    }

    throw new Error(`GraphQL request failed. Tried: ${problems.join(' | ')}`);
};

// --- Auth Queries & Mutations ---
export const GET_CURRENT_USER_QUERY = `
    query {
        getCurrentUser {
            success
            username
            userId
            message
        }
    }
`;

export const CLAIM_THREADS_MUTATION = `
    mutation ClaimThreads($threads: [ClaimThreadInput!]!) {
        claimLocalThreads(threads: $threads) {
            success
            message
            syncedCount
        }
    }
`;

export const LOGIN_MUTATION = `
    mutation Login($username: String!, $password: String!) {
        login(username: $username, password: $password) {
            success
            message
            username
        }
    }
`;

export const REGISTER_MUTATION = `
    mutation Register($username: String!, $password: String!) {
        register(username: $username, password: $password) {
            success
            message
            username
        }
    }
`;

export const LOGOUT_MUTATION = `mutation { logout }`;

// --- Existing Queries/Mutations ---
export const LIST_QUERY = `query { listGraphs(pageSize: 100) { graphs { graphId graphName nodeCount } } }`;
export const VIEW_QUERY = `query Get($graphId: String!) { getGraphView(graphId: $graphId) { config nodes { nodeId nodeType metadata position { x y } } edges { source target condition } } }`;
export const HISTORY_QUERY = `query History($graphId: String!, $threadId: String!) {
    getExecutionHistory(graphId: $graphId, threadId: $threadId) {
        success
        history { nodeId stateJson timestamp diff { added removed modified summary } tokensUsed totalTokens }
        errorMessage
    }
}`;
export const BALANCE_QUERY = `query { getYandexBalance { success balance currency errorMessage } }`;
export const EXECUTE_MUTATION = `mutation Exec($input: ExecuteGraphInput!) { executeGraph(input: $input) { output eventType state totalTokens errorMessage } }`;
export const SAVE_MUTATION = `mutation Save($graphId: String!, $positions: [NodePositionInput!]!) { saveGraphCoordinates(graphId: $graphId, positions: $positions) { success } }`;
export const DELETE_MUTATION = `mutation Del($graphId: String!) { deleteGraph(graphId: $graphId) { success } }`;
export const REWIND_MUTATION = `mutation Rewind($graphId: String!, $threadId: String!, $stateJson: String!, $targetNodeId: String) { rewindGraph(graphId: $graphId, threadId: $threadId, stateJson: $stateJson, targetNodeId: $targetNodeId) { success message } }`;
export const COPILOT_MUTATION = `mutation AskCopilot($graphId: String!, $threadId: String!, $message: String!, $chatHistory: String!, $selectedNodeId: String, $selectedEdgeJson: String) { askCopilot(graphId: $graphId, threadId: $threadId, message: $message, copilotChatHistoryJson: $chatHistory, selectedNodeId: $selectedNodeId, selectedEdgeJson: $selectedEdgeJson) { success aiResponse totalTokens errorMessage } }`;

// --- Strict enum contract (mirrors the GraphQL enums in spring-langgraph-app schema.graphqls) ---
// The GraphQL gateway now types nodeType / eventType / method / status as enums, so these values are
// validated server-side. Keep this object in sync with the Java enums in
// org.sandbox.langgraph.core.model (NodeType, ExecutionEventType, HttpMethod, GraphStatus).
// GraphQL serializes enums by their NAME, so the values here are exactly the enum constant names.
export const NODE_TYPES = Object.freeze({
    START: 'START',
    END: 'END',
    ACTION: 'ACTION',
    HUMAN: 'HUMAN',
    GRAPH: 'GRAPH',
    TOOL: 'TOOL',
});

// Multi-provider LLM selection. Keep the values in sync with
// python-langgraph-service/app/clients/llm.py PROVIDER_PRESETS.
// An empty value means "inherit": fall back to graph config, then to the server default.
export const LLM_PROVIDER_OPTIONS = Object.freeze([
    { value: '',         label: 'Inherit (graph / server default)' },
    { value: 'yandex',   label: 'YandexGPT' },
    { value: 'openai',   label: 'OpenAI' },
    { value: 'groq',     label: 'Groq' },
    { value: 'deepseek', label: 'DeepSeek' },
    { value: 'openrouter', label: 'OpenRouter' },
    { value: 'anthropic', label: 'Anthropic (Claude)' },
]);

// Copilot provider options: the same choices as the graph/node selectors, but the empty value
// means "inherit the server-side COPILOT_* / LLM_* default". The copilot is a UI assistant, not
// a graph node, so it has no graph-level config to inherit from - hence the clearer label.
export const COPILOT_PROVIDER_OPTIONS = Object.freeze(
    LLM_PROVIDER_OPTIONS.map(o => o.value === '' ? { ...o, label: 'Inherit (server default)' } : o)
);

// Node types whose system prompt is actually sent to a model (NodeHandler.process).
// Every type except TOOL and GRAPH runs the model when it carries a system_prompt, so
// START/HUMAN nodes qualify too - only TOOL and GRAPH never do (unless they are routers,
// which builder.jsx adds back via nodeUsesLlm).
// Keep in sync with `uses_llm` in python-langgraph-service/app/services/langgraph_servicer.py.
export const AI_NODE_TYPES = Object.freeze([NODE_TYPES.START, NODE_TYPES.END, NODE_TYPES.ACTION, NODE_TYPES.HUMAN]);

export const EXECUTION_EVENT_TYPES = Object.freeze({
    START: 'START',
    NODE_START: 'NODE_START',
    NODE_END: 'NODE_END',
    END: 'END',
    WAITING_FOR_INPUT: 'WAITING_FOR_INPUT',
    ERROR: 'ERROR',
});

export const HTTP_METHODS = Object.freeze({
    GET: 'GET',
    POST: 'POST',
    PUT: 'PUT',
    PATCH: 'PATCH',
    DELETE: 'DELETE',
    HEAD: 'HEAD',
    OPTIONS: 'OPTIONS',
});

export const GRAPH_STATUS = Object.freeze({
    ACTIVE: 'ACTIVE',
    INACTIVE: 'INACTIVE',
    ERROR: 'ERROR',
    BUILDING: 'BUILDING',
});

// Methods offered in the TOOL node editor dropdown (a practical subset of HTTP_METHODS).
export const TOOL_METHOD_OPTIONS = [HTTP_METHODS.GET, HTTP_METHODS.POST, HTTP_METHODS.PUT, HTTP_METHODS.DELETE];


// --- Node size estimation ---
// Nodes are auto-sized by CSS (`width: max-content; min-width: 200px; max-width: 460px`) and print
// their whole text, so a node may become tall. Dagre still needs explicit dimensions, otherwise the
// automatic layout would overlap the taller nodes. These constants mirror styles.css.
export const NODE_MIN_WIDTH = 200;
export const NODE_MAX_WIDTH = 460;
const NODE_TITLE_CHAR_WIDTH = 7.2;   // 12px bold header font
const NODE_CHAR_WIDTH = 6.0;         // 10px body font
const NODE_LINE_HEIGHT = 14.5;       // 10px * 1.45 line-height
const NODE_HEADER_HEIGHT = 34;
const NODE_BODY_PADDING = 16;
const NODE_X_PADDING = 54;           // header + body horizontal padding + borders

const longestLine = (text) =>
    String(text ?? '').split('\n').reduce((max, line) => Math.max(max, line.length), 0);

const wrappedLineCount = (text, charsPerLine) =>
    String(text ?? '').split('\n').reduce((total, line) =>
        total + (line.trim() ? Math.ceil(line.length / charsPerLine) : 1), 0);

// Estimates the rendered size of a node from the raw texts attached to its data
// (data.titleText / data.bodyText are set in app.jsx and in builder.jsx).
export const estimateNodeSize = (node) => {
    const title = String(node?.data?.titleText ?? node?.id ?? '');
    const body = String(node?.data?.bodyText ?? '');

    const width = Math.min(NODE_MAX_WIDTH, Math.max(
        NODE_MIN_WIDTH,
        Math.ceil(longestLine(title) * NODE_TITLE_CHAR_WIDTH) + NODE_X_PADDING + 40,
        Math.ceil(longestLine(body) * NODE_CHAR_WIDTH) + NODE_X_PADDING
    ));

    const titleCharsPerLine = Math.max(10, Math.floor((width - NODE_X_PADDING - 40) / NODE_TITLE_CHAR_WIDTH));
    const bodyCharsPerLine = Math.max(10, Math.floor((width - NODE_X_PADDING) / NODE_CHAR_WIDTH));

    const headerHeight = wrappedLineCount(title, titleCharsPerLine) * NODE_HEADER_HEIGHT;
    const bodyHeight = body
        ? NODE_BODY_PADDING + wrappedLineCount(body, bodyCharsPerLine) * NODE_LINE_HEIGHT
        : 0;

    return { width, height: Math.max(48, Math.ceil(headerHeight + bodyHeight)) };
};

// Distance between two nodes of the same rank and between two neighbouring ranks.
const LAYOUT_NODE_SEP = 150;
const LAYOUT_RANK_SEP = 140;

// Dagre centres the nodes of a rank around each other, which in a left-to-right layout leaves a
// ragged top edge: the node headers do not line up. This re-packs every rank against one common
// baseline instead - the header of every node of a rank starts at the same y and the rest of the
// rank is stacked below with the usual gap. Nodes are only moved, never resized, so the "no
// overlap" guarantee of dagre is preserved.
const alignRanksByHeader = (boxes, gap) => {
    const columns = new Map();
    for (const box of boxes.values()) {
        // dagre places the nodes of a rank on the same centre x, their left edges differ
        // because the node widths differ
        const rank = Math.round(box.centerX);
        if (!columns.has(rank)) columns.set(rank, []);
        columns.get(rank).push(box);
    }

    const baseline = Math.min(
        ...[...columns.values()].map((column) => Math.min(...column.map((b) => b.y)))
    );

    for (const column of columns.values()) {
        column.sort((a, b) => a.y - b.y);
        let top = baseline;
        for (const box of column) {
            box.y = top;
            top += box.height + gap;
        }
    }
};

// --- Layout Logic ---
// rankdir: 'TB' lays the flow out top to bottom, 'LR' left to right (horizontal process, where the
// node headers of every rank are aligned on a common line).
export const layoutGraph = (nodes, edges, rankdir = 'TB') => {
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir, nodesep: LAYOUT_NODE_SEP, ranksep: LAYOUT_RANK_SEP });
    g.setDefaultEdgeLabel(() => ({}));

    const boxes = new Map();
    nodes.forEach(n => {
        const { width, height } = estimateNodeSize(n);
        boxes.set(n.id, { id: n.id, x: 0, y: 0, centerX: 0, width, height });
        g.setNode(n.id, { width, height });
    });
    edges.forEach(e => g.setEdge(e.source, e.target));
    dagre.layout(g);

    // dagre returns node centres, React Flow expects the top-left corner
    boxes.forEach((box) => {
        const pos = g.node(box.id);
        box.centerX = pos.x;
        box.x = pos.x - box.width / 2;
        box.y = pos.y - box.height / 2;
    });

    if (rankdir === 'LR') alignRanksByHeader(boxes, LAYOUT_NODE_SEP);

    return nodes.map(n => {
        const box = boxes.get(n.id);
        return { ...n, position: { x: Math.round(box.x), y: Math.round(box.y) } };
    });
};
