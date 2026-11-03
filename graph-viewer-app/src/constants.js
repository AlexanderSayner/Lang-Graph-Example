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
// `options` may carry an AbortSignal so a long request (e.g. graph execution) can be
// cancelled from the UI. An abort is NOT a transport failure, so callers distinguish it
// via the AbortError name.
const postJson = async (url, query, variables, options = {}) => {
    const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        signal: options.signal,
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

export const fetchGraphQL = async (query, variables, options = {}) => {
    const candidates = resolvedApiUrl ? [resolvedApiUrl] : API_URL_CANDIDATES;
    const problems = [];

    for (const url of candidates) {
        let json;
        try {
            json = await postJson(url, query, variables, options);
        } catch (err) {
            // A user-initiated abort (e.g. the Cancel button) is intentional, not an endpoint
            // problem. Rethrow it immediately so it is NOT retried against the next candidate
            // and is not mistaken for a transport failure.
            if (err && err.name === 'AbortError') throw err;
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

// --- Document upload ---------------------------------------------------------------
//
// Attachments are REST/multipart, not GraphQL: the file is binary and base64-in-JSON would
// inflate every upload by a third and hit the GraphQL message limit. Same origin rules as
// fetchGraphQL above - the same-origin /api path first (it goes through the dev server's
// proxy and nginx's), the absolute backend URL as the fallback when the UI is served
// without one.
export const DOCUMENT_UPLOAD_URL = `${window.location.origin}/api/documents`;

/**
 * Upload one file and return `{ documentId, filename, sizeBytes, sha256 }`.
 *
 * @param {File}   file     the chosen file; the browser sets its own content type
 * @param {string} threadId the thread it will be attached to (audit only; may be blank)
 * @param {object} options  optional AbortSignal, so a stuck upload can be cancelled
 * @returns {Promise<object>} the reference to quote as context.document_ref on the next run
 */
export const uploadDocument = async (file, threadId, options = {}) => {
    const form = new FormData();
    form.append('file', file);
    if (threadId) form.append('threadId', threadId);

    const response = await fetch(DOCUMENT_UPLOAD_URL, {
        method: 'POST',
        credentials: 'include',
        signal: options.signal,
        body: form,
    });

    const contentType = response.headers.get('content-type') || '';
    const text = await response.text();
    if (!contentType.includes('json')) {
        // Same SPA-fallback failure as fetchGraphQL: no /api proxy, so index.html came back.
        throw new Error(`no JSON from ${DOCUMENT_UPLOAD_URL} (HTTP ${response.status})`);
    }
    const json = JSON.parse(text);
    if (!response.ok || json.success === false) {
        throw new Error(json.errorMessage || `Upload failed (HTTP ${response.status})`);
    }
    return json;
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

// --- Personal LLM API keys ---
//
// The key itself only ever travels browser -> server: it is encrypted server-side (AES-256-GCM,
// bound to user+provider) and never comes back. Listing returns a MASKED hint built at save
// time, so rendering this list cannot decrypt anything.

export const LLM_CREDENTIALS_QUERY = `
    query {
        llmCredentials {
            provider
            maskedKey
            ycFolderId
            updatedAt
        }
    }
`;

// Deployment-level billing facts: decides whether the chat's pre-run hint may promise "these
// nodes will run on the shared service key" (FALLBACK / REQUIRE) or must warn that the run
// will be REFUSED instead (PERSONAL_ONLY). Needs no session - it is not user data.
export const LLM_BILLING_SETTINGS_QUERY = `
    query {
        llmBillingSettings {
            policy
            personalKeysEnabled
            serviceKeyForbidden
            defaultProvider
            defaultModel
            serviceKeyEnabled
        }
    }
`;

export const SAVE_LLM_CREDENTIAL_MUTATION = `
    mutation SaveLlmCredential($input: SaveLlmCredentialInput!) {
        saveLlmCredential(input: $input) {
            provider
            maskedKey
            ycFolderId
            updatedAt
        }
    }
`;

export const DELETE_LLM_CREDENTIAL_MUTATION = `
    mutation DeleteLlmCredential($provider: String!) {
        deleteLlmCredential(provider: $provider)
    }
`;

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
export const EXECUTE_MUTATION = `mutation Exec($input: ExecuteGraphInput!) { executeGraph(input: $input) { output eventType state totalTokens errorMessage llmBilling } }`;

// Stops a running graph execution immediately (mid-node, mid-LLM call) server-side, so token
// spend halts without waiting for the node to finish. `cancelled=false` just means the run
// had already finished or never started.
export const CANCEL_EXECUTION_MUTATION = `mutation CancelExec($graphId: String!, $threadId: String!) { cancelGraphExecution(graphId: $graphId, threadId: $threadId) { success cancelled message } }`;
export const SAVE_MUTATION = `mutation Save($graphId: String!, $positions: [NodePositionInput!]!) { saveGraphCoordinates(graphId: $graphId, positions: $positions) { success } }`;
export const DELETE_MUTATION = `mutation Del($graphId: String!) { deleteGraph(graphId: $graphId) { success } }`;
export const REWIND_MUTATION = `mutation Rewind($graphId: String!, $threadId: String!, $stateJson: String!, $targetNodeId: String) { rewindGraph(graphId: $graphId, threadId: $threadId, stateJson: $stateJson, targetNodeId: $targetNodeId) { success message } }`;
export const COPILOT_MUTATION = `mutation AskCopilot($graphId: String!, $threadId: String!, $message: String!, $chatHistory: String!, $selectedNodeId: String, $selectedEdgeJson: String) { askCopilot(graphId: $graphId, threadId: $threadId, message: $message, copilotChatHistoryJson: $chatHistory, selectedNodeId: $selectedNodeId, selectedEdgeJson: $selectedEdgeJson) { success aiResponse totalTokens errorMessage llmBilling } }`;

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
    HTTP_REQUEST: 'HTTP_REQUEST',
    // Deterministic document extraction: reads the file the run's context names and writes
    // the text to variables.document_text. Never calls a model - see AI_NODE_TYPES below.
    DOCUMENT: 'DOCUMENT',
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

// What an account can actually STORE a personal key for: the same list without "Inherit",
// because inheriting is a graph-level decision, not a credential.
export const LLM_CREDENTIAL_PROVIDERS = Object.freeze(
    LLM_PROVIDER_OPTIONS.filter(option => option.value !== '')
);

// --- Provider id normalisation -----------------------------------------------------
// Mirrors PROVIDER_ALIASES in python-langgraph-service/app/clients/llm.py so a graph that
// says "claude" is matched against a credential stored under "anthropic".
const PROVIDER_ALIASES = Object.freeze({
    claude: 'anthropic',
    'open-router': 'openrouter',
    open_router: 'openrouter',
    ds: 'deepseek',
});

export const normalizeProviderId = (value) => {
    const id = String(value || '').trim().toLowerCase();
    return PROVIDER_ALIASES[id] || id;
};

/**
 * Which providers a graph will actually ask for, applying the engine's own precedence:
 * node metadata beats graph config. Only EXPLICIT choices count - a graph that inherits
 * everywhere runs on the server default, which the browser deliberately cannot see, so
 * there is nothing to warn about (and nothing to attribute).
 *
 * @param {{ config?: object, nodes?: Array<{metadata?: object}> }} view getGraphView payload
 * @returns {string[]} canonical provider ids, deduplicated
 */
export const collectGraphProviders = (view) => {
    const providers = new Set();
    const add = (value) => {
        const id = normalizeProviderId(value);
        if (id) providers.add(id);
    };
    add(view?.config?.llm_provider);
    (view?.nodes || []).forEach(node => add(node?.metadata?.llm_provider));
    return Array.from(providers);
};

/**
 * What the viewer's canvas badge needs to name the model a run of this graph will use:
 * the graph-level default plus every node that departs from it. Same precedence the engine
 * applies (see `resolve_llm_config` in python-langgraph-service/app/clients/llm.py):
 * node metadata beats graph config, and an empty provider/model means "inherit" - which the
 * viewer then resolves against the engine's own default rather than guessing a name.
 *
 * Only REAL differences are reported as overrides: a node that declares nothing, or exactly
 * what the graph already declares, changes nothing a reader of the badge would care about.
 *
 * @param {{ config?: object, nodes?: Array<{nodeId?: string, metadata?: object}> }} view getGraphView payload
 * @returns {{provider: string, model: string, nodeOverrides: Array<{nodeId: string, provider: string, model: string}>}}
 */
export const collectGraphLlm = (view) => {
    const text = (value) => String(value ?? '').trim();
    const config = view?.config || {};
    const provider = normalizeProviderId(config.llm_provider);
    const model = text(config.llm_model);
    const nodeOverrides = [];

    (view?.nodes || []).forEach(node => {
        const nodeProvider = normalizeProviderId(node?.metadata?.llm_provider);
        const nodeModel = text(node?.metadata?.llm_model);
        if ((nodeProvider && nodeProvider !== provider) || (nodeModel && nodeModel !== model)) {
            nodeOverrides.push({ nodeId: text(node?.nodeId) || '(unknown)', provider: nodeProvider, model: nodeModel });
        }
    });

    return { provider, model, nodeOverrides };
};

// Copilot provider options: the same choices as the graph/node selectors, but the empty value
// means "inherit the server-side COPILOT_* / LLM_* default". The copilot is a UI assistant, not
// a graph node, so it has no graph-level config to inherit from - hence the clearer label.
export const COPILOT_PROVIDER_OPTIONS = Object.freeze(
    LLM_PROVIDER_OPTIONS.map(o => o.value === '' ? { ...o, label: 'Inherit (server default)' } : o)
);

// --- LLM model catalog (model suggestions) ---
// The Python service holds the provider keys, so the model list is fetched server-side and
// never exposes a key to the browser. `source` is "live" (fetched from the provider) or
// "curated" (Yandex, or any provider we could not reach / have no key for).
export const LLM_MODELS_QUERY = `
    query LlmModels($provider: String!) {
        llmModels(provider: $provider) {
            success
            models { id label }
            source
            errorMessage
        }
    }
`;

// Provider -> [{ id, label }] suggestions, cached for this session on top of the server's TTL
// cache, so flipping a dropdown back and forth does not re-query. A failed or empty lookup
// just yields [] - the model inputs stay free-text in that case, so a custom model id can
// always still be typed.
const _llmModelsCache = new Map();
export const fetchLlmModels = async (provider) => {
    if (!provider) return [];
    if (_llmModelsCache.has(provider)) return _llmModelsCache.get(provider);
    try {
        const data = await fetchGraphQL(LLM_MODELS_QUERY, { provider });
        const models = (data?.llmModels?.models || []).map(m => ({ id: m.id, label: m.label || m.id }));
        _llmModelsCache.set(provider, models);
        return models;
    } catch (err) {
        console.warn(`[llm-models] '${provider}' suggestions unavailable: ${err.message}`);
        return [];
    }
};

// Node types whose system prompt is actually sent to a model (NodeHandler.process).
// Every type except HTTP_REQUEST, DOCUMENT and GRAPH runs the model when it carries a
// system_prompt, so START/HUMAN nodes qualify too - those three never do (unless they are
// routers, which builder.jsx adds back via nodeUsesLlm). DOCUMENT is the clearest case: it
// only reads bytes and extracts text, so giving it a prompt would be a silent no-op.
// Keep in sync with `uses_llm` in python-langgraph-service/app/services/langgraph_servicer.py.
export const AI_NODE_TYPES = Object.freeze([NODE_TYPES.START, NODE_TYPES.END, NODE_TYPES.ACTION, NODE_TYPES.HUMAN]);

// CSS class suffix per node type. An explicit whitelist rather than a
// `nodeType.toLowerCase().replace(/_/g, '-')` transform, so a type this build does not know about
// renders with a visibly distinct "unknown" style instead of silently falling back to React
// Flow's unstyled white default. `.node-unknown` is defined in styles.css and styles-builder.css.
const NODE_CLASS_SUFFIXES = Object.freeze({
    [NODE_TYPES.START]: 'start',
    [NODE_TYPES.END]: 'end',
    [NODE_TYPES.ACTION]: 'action',
    [NODE_TYPES.HUMAN]: 'human',
    [NODE_TYPES.GRAPH]: 'graph',
    [NODE_TYPES.HTTP_REQUEST]: 'http-request',
    [NODE_TYPES.DOCUMENT]: 'document',
});

/** React Flow node class for a node type, e.g. HTTP_REQUEST -> "node-http-request". */
export const nodeClassName = (nodeType) => `node-${NODE_CLASS_SUFFIXES[nodeType] || 'unknown'}`;

export const EXECUTION_EVENT_TYPES = Object.freeze({
    START: 'START',
    NODE_START: 'NODE_START',
    NODE_END: 'NODE_END',
    END: 'END',
    WAITING_FOR_INPUT: 'WAITING_FOR_INPUT',
    ERROR: 'ERROR',
    CANCELLED: 'CANCELLED',
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

// Methods offered in the HTTP_REQUEST node editor dropdown (a practical subset of HTTP_METHODS).
export const HTTP_REQUEST_METHOD_OPTIONS = [HTTP_METHODS.GET, HTTP_METHODS.POST, HTTP_METHODS.PUT, HTTP_METHODS.DELETE];


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

// --- Attachment slots ------------------------------------------------------------------
//
// A graph reads a document through a DOCUMENT node, and a graph may read several in one run:
// each node names the context key its file arrives under (`document_ref_key`, default
// `document_ref`) and the variable its text lands in (`output_variable`, default
// `document_text`). Those keys ARE the answer to "which file is which" - the graph says so,
// not the model - so the chat panel asks for a file per slot rather than one file per run.

/** The context key a DOCUMENT node reads, and the engine's default when it names none. */
export const DEFAULT_DOCUMENT_REF_KEY = 'document_ref';

/**
 * The attachment slots of a graph, in the order its nodes were declared.
 *
 * Two DOCUMENT nodes reading the same key share one slot: they read the same file twice,
 * which is what a graph with two unnamed nodes looks like. That is reported through
 * `nodeIds` (length > 1) so the panel can say so instead of offering two pickers that write
 * to the same reference.
 *
 * A graph with no DOCUMENT node has no slots by definition, but the panel still needs
 * somewhere to put a picked file - the file is uploaded and the reference carried, and the
 * warning next to it explains that nothing will read it. That case yields one slot marked
 * `declared: false`.
 *
 * @param {{ nodes?: Array<{ nodeId: string, nodeType: string, metadata?: object }> }} view
 *        the getGraphView payload for the LOADED graph
 * @returns {Array<{ refKey: string, label: string, outputVariable: string,
 *                   nodeIds: string[], declared: boolean }>}
 */
export const documentSlots = (view) => {
    const byKey = new Map();
    (view?.nodes || []).forEach(node => {
        if (node?.nodeType !== NODE_TYPES.DOCUMENT) return;
        const meta = node?.metadata || {};
        const refKey = String(meta.document_ref_key || '').trim() || DEFAULT_DOCUMENT_REF_KEY;
        const outputVariable = String(meta.output_variable || '').trim() || 'document_text';
        const label = String(meta.label || '').trim();
        const existing = byKey.get(refKey);
        if (existing) {
            existing.nodeIds.push(node.nodeId);
            if (label && !existing.label) existing.label = label;
            return;
        }
        byKey.set(refKey, {
            refKey,
            label,
            outputVariable,
            nodeIds: [node.nodeId],
            declared: true,
        });
    });

    if (byKey.size === 0) {
        return [{
            refKey: DEFAULT_DOCUMENT_REF_KEY,
            label: '',
            outputVariable: 'document_text',
            nodeIds: [],
            declared: false,
        }];
    }
    return Array.from(byKey.values());
};

/**
 * The slots more than one DOCUMENT node reads - the graph reads one file twice and can never
 * hold two. The engine cannot fix this for the caller (both nodes fetch the same reference),
 * so the panel says it before the run instead of leaving the second file mysteriously absent.
 *
 * @param {ReturnType<typeof documentSlots>} slots
 * @returns {Array<{ refKey: string, nodeIds: string[] }>}
 */
export const ambiguousDocumentSlots = (slots) =>
    (slots || []).filter(slot => slot.nodeIds.length > 1);

/**
 * What the run's context must carry for the slots that have a file attached.
 *
 * @param {ReturnType<typeof documentSlots>} slots every slot of the loaded graph
 * @param {Record<string, { documentId?: string }>} attachments by refKey
 * @returns {object|undefined} the context map, or undefined when nothing is attached (so an
 *          ordinary run keeps sending no context at all rather than an empty object)
 */
export const documentContextFor = (slots, attachments) => {
    const context = {};
    (slots || []).forEach(slot => {
        const documentId = attachments?.[slot.refKey]?.documentId;
        if (documentId) context[slot.refKey] = documentId;
    });
    return Object.keys(context).length > 0 ? context : undefined;
};

// ============================================================================
// Graph export / import (JSON)
// ============================================================================
// A graph can be downloaded as a single .json file and imported back through the web UI.
// Both directions are pure client side: an export is read with `getGraphView`, an import is
// written with the existing `buildGraph` mutation - no server endpoint, no proto change.
// This section owns the file format; the two pages only wire buttons to it.

/** Marker every export carries, so an unrelated JSON file is rejected on import. */
export const GRAPH_EXPORT_FORMAT = 'langgraph.graph';

/** Bumped whenever the envelope changes shape in a way an older reader cannot follow. */
export const GRAPH_EXPORT_VERSION = 1;

/**
 * The read an export performs. Like `VIEW_QUERY`, but it also selects `graphName` (the file is
 * named after it) and `handlerName`, so importing reproduces the stored handler instead of
 * quietly inventing a different one.
 */
export const GRAPH_EXPORT_QUERY = `query Export($graphId: String!) {
  getGraphView(graphId: $graphId) {
    success message graphId graphName config
    nodes { nodeId nodeType handlerName metadata position { x y } }
    edges { source target condition }
  }
}`;

/** Create a graph definition - the mutation an import writes through. */
export const BUILD_MUTATION = `mutation Build($input: BuildGraphInput!) { buildGraph(input: $input) { success graphId message } }`;

const VALID_NODE_TYPES = new Set(Object.values(NODE_TYPES));

/**
 * Build the export envelope from a graph definition.
 *
 * Accepts either a `getGraphView` payload or a `BuildGraphInput`-shaped object (the builder
 * exports its unsaved canvas), because both carry `graphId`/`graphName`/`config`/`nodes`/`edges`.
 *
 * @param {{ graphId?: string, graphName?: string, config?: object,
 *           nodes?: Array<object>, edges?: Array<object> }} view
 * @returns {{ format: string, version: number, exportedAt: string, graph: object }}
 */
export const buildGraphExport = (view) => ({
    format: GRAPH_EXPORT_FORMAT,
    version: GRAPH_EXPORT_VERSION,
    exportedAt: new Date().toISOString(),
    graph: {
        graphId: view?.graphId || '',
        graphName: view?.graphName || '',
        config: view?.config || {},
        nodes: (view?.nodes || []).map(node => ({
            nodeId: node.nodeId,
            nodeType: node.nodeType,
            handlerName: node.handlerName || `${node.nodeId}Handler`,
            metadata: node.metadata || {},
            position: node.position ? { x: node.position.x, y: node.position.y } : null,
        })),
        edges: (view?.edges || []).map(edge => ({
            source: edge.source,
            target: edge.target,
            condition: edge.condition ?? null,
        })),
    },
});

/**
 * Save `text` to the user's disk as a download.
 *
 * The object URL is revoked on the next tick rather than synchronously: revoking in the same
 * turn as `click()` cancels the download in some browsers.
 *
 * @param {string} filename suggested name (the browser may still adjust it)
 * @param {string} text file body
 * @param {string} mime content type
 */
export const downloadTextFile = (filename, text, mime = 'application/json') => {
    const blob = new Blob([text], { type: `${mime};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    setTimeout(() => URL.revokeObjectURL(url), 0);
};

/**
 * Turn a graph name into something a filesystem will accept as a file name.
 *
 * @param {string} name graph name
 * @param {string} fallback used when nothing usable is left
 * @returns {string} never empty, never containing path separators
 */
export const sanitizeFileName = (name, fallback = 'graph') => {
    const cleaned = String(name || '')
        .replace(/[\\/:*?"<>|]+/g, '-')
        .replace(/\s+/g, ' ')
        .trim()
        .replace(/^\.+/, '')
        .slice(0, 80)
        .trim();
    return cleaned || fallback;
};

/**
 * Download one graph as a .json file.
 *
 * @param {string} graphId graph to export
 * @returns {Promise<{ filename: string, graphName: string }>} what was written
 * @throws when the graph cannot be read (unknown id, transport failure)
 */
export const exportGraphFile = async (graphId) => {
    if (!graphId) throw new Error('No graph is loaded to export.');
    const data = await fetchGraphQL(GRAPH_EXPORT_QUERY, { graphId });
    const view = data?.getGraphView;
    if (!view || view.success === false) {
        throw new Error(view?.message || `Graph "${graphId}" could not be read for export.`);
    }
    const filename = `${sanitizeFileName(view.graphName || graphId)}.json`;
    downloadTextFile(filename, JSON.stringify(buildGraphExport(view), null, 2));
    return { filename, graphName: view.graphName || graphId };
};

/** Normalise one node of an imported file, or throw naming exactly what is wrong with it. */
const normalizeImportedNode = (raw, index) => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        throw new Error(`Node #${index + 1} is not an object.`);
    }
    const nodeId = String(raw.nodeId ?? '').trim();
    if (!nodeId) throw new Error(`Node #${index + 1} has no nodeId.`);
    if (nodeId.length > 100) {
        throw new Error(`Node id "${nodeId}" is longer than the 100 characters the server allows.`);
    }

    const nodeType = String(raw.nodeType ?? '').trim().toUpperCase();
    if (!VALID_NODE_TYPES.has(nodeType)) {
        throw new Error(`Node "${nodeId}" has unknown type "${raw.nodeType}".`);
    }

    const metadata = raw.metadata && typeof raw.metadata === 'object' && !Array.isArray(raw.metadata)
        ? raw.metadata
        : {};

    let position = null;
    if (raw.position && typeof raw.position === 'object') {
        const x = Number(raw.position.x);
        const y = Number(raw.position.y);
        if (Number.isFinite(x) && Number.isFinite(y)) position = { x, y };
    }

    return {
        nodeId,
        nodeType,
        handlerName: String(raw.handlerName ?? '').trim() || `${nodeId}Handler`,
        metadata,
        position,
    };
};

/** Normalise one edge of an imported file, checking both endpoints exist in the graph. */
const normalizeImportedEdge = (raw, index, nodeIds) => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        throw new Error(`Edge #${index + 1} is not an object.`);
    }
    const source = String(raw.source ?? '').trim();
    const target = String(raw.target ?? '').trim();
    if (!source || !target) throw new Error(`Edge #${index + 1} needs both a source and a target.`);
    if (!nodeIds.has(source)) throw new Error(`Edge source "${source}" is not a node of this graph.`);
    if (!nodeIds.has(target)) throw new Error(`Edge target "${target}" is not a node of this graph.`);
    return { source, target, condition: raw.condition == null ? null : String(raw.condition) };
};

/**
 * Parse and validate the contents of an exported graph file.
 *
 * Accepts the canonical envelope, and - because they share the same shape - a raw
 * `BuildGraphInput` or `getGraphView` payload too, so a hand-written file works as well.
 *
 * @param {string} text raw file contents
 * @returns {{ graphName: string, config: object, nodes: Array<object>, edges: Array<object> }}
 *          ready to hand to `buildGraph` or to paint on the builder canvas
 * @throws {Error} describing the first problem found, suitable for showing to the user
 */
export const parseGraphImport = (text) => {
    let parsed;
    try {
        parsed = JSON.parse(typeof text === 'string' ? text : String(text ?? ''));
    } catch {
        throw new Error('That file is not valid JSON. Import the .json file produced by Export.');
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('The file does not describe a graph (a JSON object was expected).');
    }
    if (parsed.format != null && parsed.format !== GRAPH_EXPORT_FORMAT) {
        throw new Error(`Unsupported file format "${parsed.format}" - expected "${GRAPH_EXPORT_FORMAT}".`);
    }
    if (typeof parsed.version === 'number' && parsed.version > GRAPH_EXPORT_VERSION) {
        throw new Error(
            `This export is version ${parsed.version}, newer than this UI understands (${GRAPH_EXPORT_VERSION}).`
        );
    }

    // Canonical envelope -> its `graph`; otherwise the file already is the definition.
    const source = parsed.graph && typeof parsed.graph === 'object' ? parsed.graph : parsed;

    const rawNodes = source.nodes;
    if (!Array.isArray(rawNodes) || rawNodes.length === 0) {
        throw new Error('The file contains no nodes, so there is no graph to import.');
    }
    // Built one at a time rather than with a plain `.map`: a repeated id is a broken graph
    // (edges would only ever address one of the two), so it is caught before the Set below
    // would silently collapse them.
    const nodes = [];
    const nodeIds = new Set();
    rawNodes.forEach((raw, index) => {
        const node = normalizeImportedNode(raw, index);
        if (nodeIds.has(node.nodeId)) throw new Error(`Duplicate node id "${node.nodeId}".`);
        nodeIds.add(node.nodeId);
        nodes.push(node);
    });

    if (!nodes.some(node => node.nodeType === NODE_TYPES.START)) {
        throw new Error('The graph has no START node.');
    }
    if (!nodes.some(node => node.nodeType === NODE_TYPES.END)) {
        throw new Error('The graph has no END node.');
    }

    const rawEdges = source.edges == null ? [] : source.edges;
    if (!Array.isArray(rawEdges)) throw new Error('The file\'s "edges" field is not a list.');
    const edges = rawEdges.map((edge, index) => normalizeImportedEdge(edge, index, nodeIds));

    const config = source.config && typeof source.config === 'object' && !Array.isArray(source.config)
        ? source.config
        : {};

    return {
        graphName: String(source.graphName ?? source.name ?? '').trim() || 'Imported Graph',
        config,
        nodes,
        edges,
    };
};

/**
 * An id for a graph that does not exist yet.
 *
 * Import must never overwrite the graph a file came from - or any other one - so the id is
 * always freshly minted: a slug of the name plus a millisecond stamp, trimmed to the 100
 * characters `BuildGraphInput` allows.
 *
 * @param {string} graphName imported graph name
 * @returns {string} a new graph id
 */
// Two imports in the same millisecond would otherwise mint the same id and the second would
// overwrite the first, so ties within this page session get a sequence suffix.
let lastFreshIdStamp = 0;
let freshIdSequence = 0;

export const freshGraphId = (graphName) => {
    const now = Date.now();
    if (now === lastFreshIdStamp) freshIdSequence += 1;
    else { lastFreshIdStamp = now; freshIdSequence = 0; }

    const slug = String(graphName || '')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 60)
        .replace(/-+$/, '');
    const stamp = freshIdSequence === 0 ? `${now}` : `${now}-${freshIdSequence}`;
    return `${slug || 'imported-graph'}-${stamp}`.slice(0, 100);
};

/**
 * Import a graph file as a brand new graph.
 *
 * @param {File|string} source a picked file, or the raw text of one
 * @returns {Promise<{ graphId: string, graphName: string, nodeCount: number }>}
 * @throws {Error} when the file is not a valid graph, or the server refuses it
 */
export const importGraphFile = async (source) => {
    const text = typeof source === 'string' ? source : await source.text();
    const { graphName, config, nodes, edges } = parseGraphImport(text);
    const graphId = freshGraphId(graphName);

    const data = await fetchGraphQL(BUILD_MUTATION, { input: { graphId, graphName, nodes, edges, config } });
    const result = data?.buildGraph;
    if (!result?.success) throw new Error(result?.message || 'The graph could not be imported.');

    return { graphId: result.graphId || graphId, graphName, nodeCount: nodes.length };
};
