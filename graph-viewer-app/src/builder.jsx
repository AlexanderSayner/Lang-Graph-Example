import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { createRoot } from 'react-dom/client';
import ReactFlow, {
    Controls, Background, addEdge, useNodesState, useEdgesState,
    MarkerType, Handle, Position, useReactFlow, ReactFlowProvider
} from 'reactflow';

// CSS imports (Order matters! ReactFlow first, custom second)
import 'reactflow/dist/style.css';
import './styles-builder.css';
import { Toast, useToast } from './components/Toast';

// Shared constants from your central file
import { fetchGraphQL, LIST_QUERY, VIEW_QUERY, SAVE_MUTATION, BUILD_MUTATION, NODE_TYPES, HTTP_REQUEST_METHOD_OPTIONS, LLM_PROVIDER_OPTIONS, AI_NODE_TYPES, nodeClassName, parseGraphImport, freshGraphId, buildGraphExport, downloadTextFile, sanitizeFileName } from './constants';
import ModelDatalist from './components/ModelDatalist';

// --- Params ---
const params = new URLSearchParams(window.location.search);
const IS_NEW = params.get('isNew') === 'true';
const URL_ID = params.get('graphId');
const URL_NAME = params.get('graphName');

// --- GraphQL Mutations specific to Builder ---
const HTTP_REQUEST_DEBUG_MUTATION = `mutation Debug($input: HttpRequestDebugInput!) { debugHttpRequest(input: $input) { success statusCode body errorMessage } }`;

// --- Custom Node ---
const DesignNode = ({ data, selected }) => {
    // Shared whitelist from constants.js: an unknown node type gets the visible "unknown" style
    // instead of being silently painted as ACTION.
    const bgClass = nodeClassName(data.type);

    const displayText = data.friendlyName || data.label;

    return (
        <div className={`react-flow__node ${bgClass}`} style={{ borderWidth: selected ? 3 : 2 }}>
            {data.type !== NODE_TYPES.START && <Handle type="target" position={Position.Top} style={{ background: '#555' }} />}
            <div className="node-label">
                {displayText}
                <span className="node-type">{data.type}</span>
            </div>
            {data.type !== NODE_TYPES.END && <Handle type="source" position={Position.Bottom} style={{ background: '#555' }} />}
        </div>
    );
};
const nodeTypes = { designNode: DesignNode };

// Number of rows for the auto-growing system-prompt textarea: it is derived from the text itself
// (roughly PROMPT_CHARS_PER_LINE characters per visual line, plus the explicit line breaks),
// so the whole prompt is readable at once and the field does not need to be scrolled.
const PROMPT_CHARS_PER_LINE = 110;
const PROMPT_MIN_ROWS = 4;
const PROMPT_MAX_ROWS = 40;
const promptRows = (text) => {
    const visualLines = String(text || '')
        .split('\n')
        .reduce((total, line) => total + Math.max(1, Math.ceil(line.length / PROMPT_CHARS_PER_LINE)), 0);
    return Math.min(PROMPT_MAX_ROWS, Math.max(PROMPT_MIN_ROWS, visualLines));
};

// --- Canvas <-> definition ---
/**
 * A graph definition (`getGraphView` payload, or the `nodes`/`edges` of an imported file) into
 * React Flow nodes and edges.
 *
 * Shared by the load effect and the import path, so a graph opened from the server and one
 * loaded from a downloaded file land on the canvas in exactly the same shape.
 *
 * @param {{ nodes?: Array<object>, edges?: Array<object> }} view
 * @returns {{ nodes: Array<object>, edges: Array<object> }}
 */
const viewToFlow = (view) => ({
    nodes: (view?.nodes || []).map(n => ({
        id: n.nodeId, type: 'designNode', position: n.position || { x: 0, y: 0 },
        data: {
            label: n.nodeId, type: n.nodeType,
            // Full metadata travels with the node. The save starts from this object, so keys this
            // form has no editor for (include_variables, llm_temperature, llm_max_tokens)
            // survive a re-save instead of being silently destroyed.
            metadata: n.metadata || {},
            prompt: n.metadata?.system_prompt || "",
            friendlyName: n.metadata?.label || "",
            subgraphId: n.metadata?.subgraph_id || null,
            method: n.metadata?.method || "GET",
            url: n.metadata?.url || "",
            headers: n.metadata?.headers || "{}",
            body: n.metadata?.body || "",
            llmProvider: n.metadata?.llm_provider || "",
            llmModel: n.metadata?.llm_model || "",
            contextMode: n.metadata?.context_mode || "",
            documentRefKey: n.metadata?.document_ref_key || "",
            documentOutputVariable: n.metadata?.output_variable || "",
            documentMetaVariable: n.metadata?.meta_variable || "",
            // An AI node keeps its answer slot in the same metadata key; it is a separate form
            // field because a node is either DOCUMENT or AI, never both.
            aiOutputVariable: n.metadata?.output_variable || ""
        },
    })),
    edges: (view?.edges || []).map((e, i) => ({
        id: `e-${i}`, source: e.source, target: e.target,
        label: e.condition || '', animated: true, markerEnd: { type: MarkerType.ArrowClosed }
    })),
});

/**
 * The canvas into a `BuildGraphInput` payload.
 *
 * The single definition of "what this canvas means as a graph definition": Save persists it and
 * Export downloads it, so a downloaded file is byte-for-byte what a Save would write.
 *
 * Pure - it never touches component state. The one thing it can object to is an edge condition
 * that is not `key == value`; that is reported back as `badEdge` so the caller can select the
 * offending edge before failing, rather than being thrown from here.
 *
 * @param {{ graphId: string, graphName: string, nodes: Array<object>, edges: Array<object>,
 *           graphConfig: object, graphLLM: { provider: string, model: string } }} input
 * @returns {{ input: object, badEdge: ({ index: number, source: string, target: string,
 *             condition: string }|null) }}
 */
const canvasToBuildInput = ({ graphId, graphName, nodes, edges, graphConfig, graphLLM }) => {
    const payloadNodes = nodes.map(n => {
        // Start from the metadata the server returned, then let the form fields below
        // overwrite their OWN keys. Rebuilding meta from the form alone silently
        // destroyed everything this panel has no editor for - include_variables,
        // llm_temperature, llm_max_tokens - and one such save turned a working graph
        // into a chain of nodes asking for data they already had.
        const meta = { ...(n.data?.metadata || {}) };
        meta.system_prompt = n.data?.prompt || "";
        meta.label = n.data?.friendlyName || "";
        if (n.data?.type === NODE_TYPES.GRAPH) {
            if (n.data.subgraphId) meta.subgraph_id = n.data.subgraphId;
            else delete meta.subgraph_id;
        }
        if (n.data?.type === NODE_TYPES.HTTP_REQUEST) { meta.method = n.data.method || "GET"; meta.url = n.data.url || ""; meta.headers = n.data.headers || "{}"; meta.body = n.data.body || ""; }
        // DOCUMENT carries no system prompt (it never calls a model). Its three slot
        // names are written back, and only when non-blank: an empty value would
        // override the engine's default with nothing.
        if (n.data?.type === NODE_TYPES.DOCUMENT) {
            const refKey = (n.data.documentRefKey || '').trim();
            const outputVariable = (n.data.documentOutputVariable || '').trim();
            const metaVariable = (n.data.documentMetaVariable || '').trim();
            if (refKey) meta.document_ref_key = refKey; else delete meta.document_ref_key;
            if (outputVariable) meta.output_variable = outputVariable; else delete meta.output_variable;
            if (metaVariable) meta.meta_variable = metaVariable; else delete meta.meta_variable;
        }
        // An AI node publishes its answer to `variables` only when it names the slot: without it
        // the response lives solely in `output`, which the next node cannot read as data (an HTTP
        // request's stateJson is `variables` alone). Blank deletes the key - with the spread above
        // a cleared field would keep the stale value the user just removed.
        if (AI_NODE_TYPES.includes(n.data?.type)) {
            const answerVariable = (n.data.aiOutputVariable || '').trim();
            if (answerVariable) meta.output_variable = answerVariable;
            else delete meta.output_variable;
        }
        // Per-node AI overrides (empty = inherit the graph/server default). A cleared
        // field must DELETE its key: with the spread above, leaving it would keep a
        // stale value the user just tried to remove.
        if (n.data?.llmProvider) meta.llm_provider = n.data.llmProvider;
        else delete meta.llm_provider;
        if (n.data?.llmModel) meta.llm_model = n.data.llmModel;
        else delete meta.llm_model;
        if (n.data?.contextMode) meta.context_mode = n.data.contextMode;
        else delete meta.context_mode;
        return { nodeId: n.id, nodeType: n.data?.type, handlerName: `${n.id}Handler`, metadata: meta, position: { x: n.position.x, y: n.position.y } };
    });

    // Trimmed so a label of stray whitespace becomes the unconditional edge it looks like;
    // the condition rule below then sees exactly what the engine will receive.
    const payloadEdges = edges.map(e => ({ source: e.source, target: e.target, condition: (e.label || '').trim() || null }));

    // The engine rejects any condition without a key == value shape when the graph is
    // built, and its message quotes the offending text. Catching it here instead names the
    // edge, selects it for the user, and stops a doomed save before it reaches the server.
    // It also flags an empty value, which the engine does parse but which can never match
    // anything - a UI-side rule only, not a server rule.
    const badIndex = payloadEdges.findIndex(e => {
        if (!e.condition) return false;                     // unconditional edge
        const [key, ...rest] = e.condition.split('==');
        return !key.trim() || rest.length === 0 || !rest.join('==').trim();
    });
    const badEdge = badIndex >= 0 ? { index: badIndex, ...payloadEdges[badIndex] } : null;

    // Graph-wide AI default - read by the Python service before each node's own override.
    // Start from the config that was loaded so unrelated graph-level keys (anything set
    // through the buildGraph API) survive a re-save, then write - or remove - the two
    // keys this UI owns. A field reset to "" means "inherit", so its stored key must be
    // dropped instead of being left stale.
    const config = { ...graphConfig };
    if (graphLLM.provider) config.llm_provider = graphLLM.provider;
    else delete config.llm_provider;
    if (graphLLM.model) config.llm_model = graphLLM.model;
    else delete config.llm_model;

    return { input: { graphId, graphName, nodes: payloadNodes, edges: payloadEdges, config }, badEdge };
};

// --- Builder ---
function Builder() {
    const [nodes, setNodes, onNodesChange] = useNodesState([]);
    const { toast, showToast, dismissToast } = useToast();
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);
    const [loading, setLoading] = useState(false);
    const [testResult, setTestResult] = useState(null);

    // State for available graphs to link
    const [availableGraphs, setAvailableGraphs] = useState([]);

    const [graphId, setGraphId] = useState(IS_NEW ? `graph-${Date.now()}` : URL_ID);
    const [graphName, setGraphName] = useState(URL_NAME || "Untitled Graph");
    // Graph-wide AI default (config.llm_provider / config.llm_model). Individual nodes
    // may override it; an empty value means "inherit from the server default".
    const [graphLLM, setGraphLLM] = useState({ provider: '', model: '' });
    // The complete graph-level `config` object, including keys this UI does not manage
    // (anything a caller set through the buildGraph API). It is spread back into the save
    // payload so that re-saving from the builder does not silently drop them.
    const [graphConfig, setGraphConfig] = useState({});

    const reactFlowWrapper = useRef(null);
    // Hidden <input type="file"> that the Import button opens.
    const importInputRef = useRef(null);
    const { screenToFlowPosition } = useReactFlow();

    useEffect(() => {
        const style = document.createElement('style');
        style.innerHTML = `*, *::before, *::after { box-sizing: border-box; }`;
        document.head.appendChild(style);
        return () => document.head.removeChild(style);
    }, []);

    // This prevents stale data and double-renders.
    const selectedElement = useMemo(() => {
        const selectedNode = nodes.find(n => n.selected);
        if (selectedNode) {
            // Attach type for internal logic checks
            return { ...selectedNode, type: 'designNode' };
        }
        const selectedEdge = edges.find(e => e.selected);
        if (selectedEdge) {
            return selectedEdge;
        }
        return null;
    }, [nodes, edges]);

    const hasStartNode = nodes.some(n => n.data?.type === NODE_TYPES.START);

    // Whether this node can call the model, mirroring `uses_llm` in
    // python-langgraph-service/app/services/langgraph_servicer.py: every node type except
    // HTTP_REQUEST/GRAPH sends its system_prompt to the model, and a router node - any node with an
    // outgoing conditional (labelled) edge - always does, whatever its type. Without
    // the router check a HTTP_REQUEST/GRAPH router could not be given a per-node provider here.
    const nodeUsesLlm = (el) =>
        AI_NODE_TYPES.includes(el.data?.type) ||
        edges.some(e => e.source === el.id && String(e.label || '').trim());

    useEffect(() => {
        // Load available graphs for the dropdown
        fetchGraphQL(LIST_QUERY).then(data => {
            const others = data.listGraphs.graphs.filter(g => g.graphId !== graphId);
            setAvailableGraphs(others);
        }).catch(console.error);

        // Load current graph if editing
        if (IS_NEW) {
            setNodes([]);
        } else if (URL_ID) {
            fetchGraphQL(VIEW_QUERY, { graphId: URL_ID }).then(data => {
                if (!data.getGraphView) return;
                const view = data.getGraphView;
                const cfg = view.config || {};
                setGraphConfig(cfg);
                setGraphLLM({ provider: cfg.llm_provider || '', model: cfg.llm_model || '' });
                const { nodes: loaded, edges: loadedEdges } = viewToFlow(view);
                setNodes(loaded);
                setEdges(loadedEdges);
            });
        }
    }, []);

    // --- Interaction ---

    const onConnect = useCallback((params) => {
        setEdges(eds => addEdge({ ...params, type: 'default', animated: true, markerEnd: { type: MarkerType.ArrowClosed }, label: '' }, eds));
    }, []);

    const onDragStart = (e, type) => {
        if (type === NODE_TYPES.START && hasStartNode) return;
        e.dataTransfer.setData('application/reactflow', type);
        e.dataTransfer.effectAllowed = 'move';
    };

    const onDragOver = useCallback((e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }, []);

    const onDrop = useCallback((e) => {
        e.preventDefault();
        const type = e.dataTransfer.getData('application/reactflow');
        if (!type || (type === NODE_TYPES.START && hasStartNode)) return;

        const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });

        const id = `node_${Date.now()}`;
        setNodes(nds => nds.concat({
            id, type: 'designNode', position,
            data: { label: id, type: type, prompt: "", subgraphId: null, friendlyName: null, method: "GET", url: "", headers: "{}", body: "", llmProvider: "", llmModel: "", contextMode: "", documentRefKey: "", documentOutputVariable: "", documentMetaVariable: "", aiOutputVariable: "", metadata: {} }
        }));
    }, [hasStartNode, screenToFlowPosition, setNodes]);

    // We just clear test results. React Flow handles the selection state update automatically.
    const onElementClick = useCallback((e, el) => {
        setTestResult(null);
    }, []);

    const deleteSelected = () => {
        if (!selectedElement || !window.confirm(`Delete?`)) return;
        if (selectedElement.type === 'designNode') {
            setNodes(nds => nds.filter(n => n.id !== selectedElement.id));
            setEdges(eds => eds.filter(e => e.source !== selectedElement.id && e.target !== selectedElement.id));
        } else {
            setEdges(eds => eds.filter(e => e.id !== selectedElement.id));
        }
    };

    const handleSave = async () => {
        if (!graphName.trim()) { showToast("Please enter a graph name.", "error"); return; }
        setLoading(true);
        try {
            if (!nodes.find(n => n.data?.type === NODE_TYPES.START)) throw new Error("Graph needs a START node");
            if (!nodes.find(n => n.data?.type === NODE_TYPES.END)) throw new Error("Graph needs an END node");

            // The whole definition (metadata merge, edge conditions, graph config) lives in one
            // pure helper, because Export downloads exactly this payload - two copies of these
            // rules would eventually disagree about what a graph is.
            const { input: buildInput, badEdge } = canvasToBuildInput({
                graphId, graphName, nodes, edges, graphConfig, graphLLM
            });
            if (badEdge) {
                setEdges(eds => eds.map((e, i) => (i === badEdge.index ? { ...e, selected: true } : e)));
                throw new Error(
                    `Invalid condition on ${badEdge.source} -> ${badEdge.target}: "${badEdge.condition}". ` +
                    "Use key == value, e.g. output == true"
                );
            }

            // UI coordinates ride inside every node of the build input (NodeInput.position), so the
            // definition and the layout are persisted in one write - no parallel positions list that
            // could drift away from nodes. The saveGraphCoordinates call below stays as a redundant
            // confirmation write (identical payload, idempotent on the server).
            const positions = nodes.map(n => ({ nodeId: n.id, x: n.position.x, y: n.position.y }));

            // The gateway only persists a definition the engine accepted, so a rejected config
            // (unknown llm_provider, invalid node, ...) leaves nothing behind - surface the
            // server's reason instead of continuing to the coordinates write and toasting success.
            const built = await fetchGraphQL(BUILD_MUTATION, { input: buildInput });
            if (!built?.buildGraph?.success) {
                throw new Error(built?.buildGraph?.message || "The graph was rejected by the server");
            }
            await fetchGraphQL(SAVE_MUTATION, { graphId: graphId, positions: positions });

            showToast("Saved successfully!", "success");
            window.location.href = 'index.html';
        } catch (err) { showToast("Error: " + err.message, "error", 6000); } finally { setLoading(false); }
    };

    // --- Export / Import ---

    // Download the canvas as a .json file, including edits that have not been saved yet. The
    // payload comes from the same helper Save uses, so the file is what a Save would persist.
    const handleExport = () => {
        try {
            const { input, badEdge } = canvasToBuildInput({
                graphId, graphName, nodes, edges, graphConfig, graphLLM
            });
            if (!input.nodes.length) throw new Error('There is nothing on the canvas to export yet.');
            if (badEdge) {
                throw new Error(
                    `Invalid condition on ${badEdge.source} -> ${badEdge.target}: "${badEdge.condition}". `
                    + 'Use key == value, e.g. output == true'
                );
            }
            const filename = `${sanitizeFileName(graphName)}.json`;
            downloadTextFile(filename, JSON.stringify(buildGraphExport(input), null, 2));
            showToast(`Exported ${input.nodes.length} nodes to ${filename}`, 'success');
        } catch (err) {
            showToast("Export failed: " + err.message, "error", 6000);
        }
    };

    // Load a downloaded graph file onto the canvas. The graph id is replaced by a fresh one, so
    // the next Save creates a NEW graph instead of overwriting the one the file came from -
    // nothing is written until the user presses Save.
    const handleImportFile = async (e) => {
        const file = e.target.files?.[0];
        e.target.value = ''; // reset, so picking the very same file again still fires onChange
        if (!file) return;
        try {
            const imported = parseGraphImport(await file.text());
            const { nodes: flowNodes, edges: flowEdges } = viewToFlow(imported);
            setNodes(flowNodes);
            setEdges(flowEdges);
            setGraphName(imported.graphName);
            setGraphConfig(imported.config);
            setGraphLLM({ provider: imported.config.llm_provider || '', model: imported.config.llm_model || '' });
            setGraphId(freshGraphId(imported.graphName));
            setTestResult(null);
            showToast(
                `Loaded "${imported.graphName}" - ${flowNodes.length} nodes, ${flowEdges.length} edges. `
                + `Review it, then Save to create it.`,
                'success'
            );
        } catch (err) {
            showToast("Import failed: " + err.message, "error", 6000);
        }
    };

    // --- Panel Updates ---

    const updateNode = (key, value) => {
        // Find the currently selected ID from the latest state
        const selectedId = nodes.find(n => n.selected)?.id;
        if (!selectedId) return;

        if (key === 'id') {
            const oldId = selectedId;
            const newId = value;

            if (nodes.find(n => n.id === newId)) { showToast("ID already exists", "error"); return; }

            // Update Nodes
            setNodes(nds => nds.map(n => n.id === oldId ? {...n, id: newId, data: {...n.data, label: newId}} : n));
            // Update Edges references
            setEdges(eds => eds.map(e => ({ ...e, source: e.source === oldId ? newId : e.source, target: e.target === oldId ? newId : e.target })));
        } else {
            setNodes(nds => nds.map(n => n.id === selectedId ? {...n, data: {...n.data, [key]: value}} : n));
        }
    };

    const updateEdge = (value) => {
        const selectedId = edges.find(e => e.selected)?.id;
        if (!selectedId) return;
        setEdges(eds => eds.map(e => e.id === selectedId ? {...e, label: value} : e));
    };

    // --- Variable Inserter Helper ---
    const insertVariable = (varName) => {
        // Get fresh data from nodes state
        const selectedNode = nodes.find(n => n.selected);
        if(!selectedNode) return;

        const currentPrompt = selectedNode.data.prompt || "";
        const newText = currentPrompt + ` {{${varName}}}`;
        updateNode('prompt', newText);
    };

    // --- HTTP Request Test Logic ---
    const testHttpRequest = async () => {
        // Get fresh data
        const selectedNode = nodes.find(n => n.selected);
        if (!selectedNode) return;

        try {
            JSON.parse(selectedNode.data.headers || "{}");
        } catch (e) {
            showToast("Headers must be valid JSON!", "error");
            return;
        }

        setLoading(true);
        setTestResult(null);
        try {
            const mockVariables = { "user_id": "123" };
            const result = await fetchGraphQL(HTTP_REQUEST_DEBUG_MUTATION, {
                input: {
                    method: selectedNode.data.method,
                    url: selectedNode.data.url,
                    headers: selectedNode.data.headers,
                    body: selectedNode.data.body,
                    variables: JSON.stringify(mockVariables)
                }
            });
            setTestResult(result.debugHttpRequest);
        } catch (e) { setTestResult({ success: false, errorMessage: e.message }); }
        finally { setLoading(false); }
    };

    return (
        <div className="layout-wrapper">
            <div className="top-bar">
                <div style={{display: 'flex', alignItems: 'center'}}>
                    <a href="index.html" className="btn-back">← Back</a>
                    <h2>Editing:</h2>
                    <input className="name-input" value={graphName} onChange={(e) => setGraphName(e.target.value)} />
                </div>
                <div style={{display: 'flex', alignItems: 'center', gap: 8}}>
                    <span className="form-label" style={{margin: 0, whiteSpace: 'nowrap'}}>Graph AI:</span>
                    <select className="form-select" style={{width: 200}} value={graphLLM.provider} onChange={(e) => setGraphLLM(prev => ({ ...prev, provider: e.target.value }))}>
                        {LLM_PROVIDER_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                    <input className="form-input" style={{width: 240}} list="llm-models-graph" value={graphLLM.model} onChange={(e) => setGraphLLM(prev => ({ ...prev, model: e.target.value }))} placeholder="graph model (optional)" />
                    <ModelDatalist id="llm-models-graph" provider={graphLLM.provider} />
                </div>
                <div style={{display: 'flex', alignItems: 'center', gap: 8}}>
                    <button onClick={handleExport} className="btn-topbar" title="Download this graph as a .json file (including unsaved edits)">⬇ Export</button>
                    <button onClick={() => importInputRef.current?.click()} className="btn-topbar" title="Load a graph from a previously exported .json file">⬆ Import</button>
                    <input ref={importInputRef} type="file" accept=".json,application/json"
                           style={{display: 'none'}} onChange={handleImportFile} />
                    <button onClick={handleSave} className="btn-save" disabled={loading}>{loading ? "Saving..." : "Save Graph"}</button>
                </div>
            </div>

            <div className="main-content">
                <div className="palette">
                    <div className={`palette-node ${hasStartNode ? 'disabled' : ''}`} title="Start Node" draggable={!hasStartNode} onDragStart={(e) => onDragStart(e, NODE_TYPES.START)}>🚀</div>
                    <div className="palette-node" title="Action Node" draggable onDragStart={(e) => onDragStart(e, NODE_TYPES.ACTION)}>🤖</div>
                    <div className="palette-node" title="Human Node" draggable onDragStart={(e) => onDragStart(e, NODE_TYPES.HUMAN)}>👤</div>
                    <div className="palette-node" title="Subgraph Node" draggable onDragStart={(e) => onDragStart(e, NODE_TYPES.GRAPH)}>🧩</div>
                    <div className="palette-node" title="HTTP Request Node" draggable onDragStart={(e) => onDragStart(e, NODE_TYPES.HTTP_REQUEST)}>🔧</div>
                    {/* A document is an attachment, not an outbound call: the palette glyph is
                        a paperclip and the node never calls a model (see AI_NODE_TYPES). */}
                    <div className="palette-node" title="Document Node — extract text from an attached .xlsx/.docx" draggable onDragStart={(e) => onDragStart(e, NODE_TYPES.DOCUMENT)}>📎</div>
                    <div className="palette-node" title="End Node" draggable onDragStart={(e) => onDragStart(e, NODE_TYPES.END)}>🛑</div>
                </div>

                <div className="canvas-wrapper" ref={reactFlowWrapper} onDrop={onDrop} onDragOver={onDragOver}>
                    <ReactFlow
                        nodes={nodes}
                        edges={edges}
                        onNodesChange={onNodesChange}
                        onEdgesChange={onEdgesChange}
                        onConnect={onConnect}
                        onNodeClick={onElementClick}
                        onEdgeClick={onElementClick}
                        onPaneClick={() => setTestResult(null)}
                        nodeTypes={nodeTypes}
                        fitView
                        deleteKeyCode="Delete" // Allows keyboard delete
                    >
                        <Background gap={10} size={1} />
                        <Controls />
                    </ReactFlow>
                </div>
            </div>

            <div className="bottom-panel">
                {selectedElement ? (
                    <>
                        <div className="panel-header">
                            <span>{selectedElement.type === 'designNode' ? `Node: ${selectedElement.id}` : `Edge`}</span>
                            <button className="btn-delete" onClick={deleteSelected}>Delete</button>
                        </div>

                        {selectedElement.type === 'designNode' ? (
                            <div className="form-row">
                                <div className="form-group">
                                    <label className="form-label">Node ID</label>
                                    <input className="form-input" value={selectedElement.id} onChange={(e) => updateNode('id', e.target.value)} />
                                </div>
                                <div className="form-group">
                                    <label className="form-label">Name (Label)</label>
                                    <input className="form-input" value={selectedElement.data.friendlyName || ""} onChange={(e) => updateNode('friendlyName', e.target.value)} placeholder="e.g. 'Greeting Agent'"/>
                                </div>
                                <div className="form-group">
                                    <label className="form-label">Type</label>
                                    <input className="form-input" value={selectedElement.data.type} disabled />
                                </div>
                            </div>
                        ) : (
                            <div className="form-row">
                                <div className="form-group">
                                    <label className="form-label">Condition</label>
                                    <input className="form-input" value={selectedElement.label || ""} onChange={(e) => updateEdge(e.target.value)} placeholder="e.g. intent == 'build' | output == true | count == 3" />
                                </div>
                                <div className="form-group">
                                    <label className="form-label">Connection</label>
                                    <input className="form-input" value={`${selectedElement.source} ➔ ${selectedElement.target}`} disabled />
                                </div>
                            </div>
                        )}

                        {/* Conditional Render for Node Types */}
                        {selectedElement.type === 'designNode' && (
                            <>
                                {nodeUsesLlm(selectedElement) && (
                                    <div className="form-row">
                                        <div className="form-group">
                                            <label className="form-label">LLM Provider</label>
                                            <select className="form-select" value={selectedElement.data.llmProvider || ""} onChange={(e) => updateNode('llmProvider', e.target.value)}>
                                                {LLM_PROVIDER_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                                            </select>
                                        </div>
                                        <div className="form-group" style={{flex: 2}}>
                                            <label className="form-label">Model (optional)</label>
                                            <input className="form-input" list="llm-models-node" value={selectedElement.data.llmModel || ""} onChange={(e) => updateNode('llmModel', e.target.value)} placeholder="empty = provider default" />
                                            {/* A node that inherits falls back to the graph-wide provider, so suggest from that. */}
                                            <ModelDatalist id="llm-models-node" provider={selectedElement.data.llmProvider || graphLLM.provider} />
                                        </div>
                                    </div>
                                )}
                                {selectedElement.data.type === NODE_TYPES.GRAPH ? (
                                    <div className="form-row">
                                        <div className="form-group" style={{flex: 2}}>
                                            <label className="form-label">Link to Subgraph</label>
                                            <select className="form-select" value={selectedElement.data.subgraphId || ""} onChange={(e) => updateNode('subgraphId', e.target.value)}>
                                                <option value="" disabled>-- Select a Graph --</option>
                                                {availableGraphs.map(g => ( <option key={g.graphId} value={g.graphId}>{g.graphName} ({g.graphId})</option> ))}
                                            </select>
                                        </div>
                                    </div>
                                ) : selectedElement.data.type === NODE_TYPES.HTTP_REQUEST ? (
                                    <>
                                        <div className="form-row">
                                            <div className="form-group" style={{flex: 0.3}}>
                                                <label className="form-label">Method</label>
                                                <select className="form-select" value={selectedElement.data.method || "GET"} onChange={(e) => updateNode('method', e.target.value)}>
                                                    {HTTP_REQUEST_METHOD_OPTIONS.map(m => <option key={m} value={m}>{m}</option>)}
                                                </select>
                                            </div>
                                            <div className="form-group" style={{flex: 1.7}}>
                                                <label className="form-label">URL</label>
                                                <input className="form-input" value={selectedElement.data.url || ""} onChange={(e) => updateNode('url', e.target.value)} placeholder="https://api.example.com/{{id}}"/>
                                            </div>
                                        </div>
                                        <div className="form-row">
                                            <div className="form-group">
                                                <label className="form-label">Headers (JSON)</label>
                                                <textarea className="form-input form-textarea" style={{height: '40px'}} value={selectedElement.data.headers || "{}"} onChange={(e) => updateNode('headers', e.target.value)} />
                                            </div>
                                            <div className="form-group">
                                                <label className="form-label">Body</label>
                                                <textarea className="form-input form-textarea" style={{height: '40px'}} value={selectedElement.data.body || ""} onChange={(e) => updateNode('body', e.target.value)} />
                                            </div>
                                        </div>
                                        <button className="btn-test-http-request" onClick={testHttpRequest} disabled={loading}>{loading ? "Testing..." : "🛠️ HTTP Request"}</button>
                                        {testResult && (
                                            <div className="result-box">
                                                <strong>Result (Status {testResult.statusCode}):</strong>
                                                <pre style={{whiteSpace: 'pre-wrap', maxHeight: '60px', overflowY: 'auto', margin: 0}}>
                                                    {testResult.success ? testResult.body : testResult.errorMessage}
                                                </pre>
                                            </div>
                                        )}
                                    </>
                                ) : selectedElement.data.type === NODE_TYPES.DOCUMENT ? (
                                    /* No system prompt field: the node never calls a model, so a prompt
                                       here would be a silent no-op. What matters is the SLOT: where the
                                       run's context keeps this file's reference, and where its extracted
                                       text lands. A graph that reads several files gives each DOCUMENT
                                       node a key of its own here - that is how it says which file is
                                       which, and two nodes sharing a key read the same file twice. */
                                    <>
                                    <div className="form-row">
                                        <div className="form-group" style={{flex: 2}}>
                                            <label className="form-label">Slot name (shown next to the 📎 picker)</label>
                                            <input className="form-input"
                                                   value={selectedElement.data.friendlyName || ''}
                                                   onChange={(e) => updateNode('friendlyName', e.target.value)}
                                                   placeholder="e.g. Time sheet (as submitted)" />
                                            <div style={{fontSize: 11, color: '#777', marginTop: 2}}>
                                                The label the chat panel shows for this file, and a header
                                                on the extracted text so the model can tell it apart.
                                            </div>
                                        </div>
                                    </div>
                                    <div className="form-row">
                                        <div className="form-group" style={{flex: 2}}>
                                            <label className="form-label">Context key holding the document reference</label>
                                            <input className="form-input"
                                                   value={selectedElement.data.documentRefKey || ''}
                                                   onChange={(e) => updateNode('documentRefKey', e.target.value)}
                                                   placeholder="document_ref" />
                                            <div style={{fontSize: 11, color: '#777', marginTop: 2}}>
                                                Read from the run's context. Give each DOCUMENT node its own
                                                key - the chat panel then shows one 📎 picker per key. Leave
                                                blank for the default, which the 📎 button already fills.
                                            </div>
                                        </div>
                                    </div>
                                    <div className="form-row">
                                        <div className="form-group" style={{flex: 2}}>
                                            <label className="form-label">Variable holding the extracted text</label>
                                            <input className="form-input"
                                                   value={selectedElement.data.documentOutputVariable || ''}
                                                   onChange={(e) => updateNode('documentOutputVariable', e.target.value)}
                                                   placeholder="document_text" />
                                            <div style={{fontSize: 11, color: '#777', marginTop: 2}}>
                                                Name it after the file ("timesheets_text") so a later node can
                                                refer to each one. Two nodes writing the same variable means
                                                the second overwrites the first.
                                            </div>
                                        </div>
                                    </div>
                                    <div className="form-row">
                                        <div className="form-group" style={{flex: 2}}>
                                            <label className="form-label">Variable holding the file's metadata</label>
                                            <input className="form-input"
                                                   value={selectedElement.data.documentMetaVariable || ''}
                                                   onChange={(e) => updateNode('documentMetaVariable', e.target.value)}
                                                   placeholder="document_meta" />
                                            <div style={{fontSize: 11, color: '#777', marginTop: 2}}>
                                                Filename, parse statistics and truncation warnings. Defaults to
                                                the text variable's name + "_meta" when you name the text.
                                            </div>
                                        </div>
                                    </div>
                                    </>
                                ) : (
                                    <>
                                    <div className="form-row">
                                        <div className="form-group" style={{flex: 2}}>
                                            <div className="prompt-lib-bar">
                                                <label className="form-label" style={{margin: 0}}>System Prompt</label>
                                                <button className="btn-sm" onClick={() => insertVariable('input')}>+ Input</button>
                                                <button className="btn-sm" onClick={() => insertVariable('http_result')}>+ HTTP Result</button>
                                                <button className="btn-sm" onClick={() => { const v = prompt("Variable name:"); if(v) insertVariable(v); }}>+ Custom</button>
                                            </div>
                                            <textarea
                                                className="form-input form-textarea form-textarea-auto"
                                                rows={promptRows(selectedElement.data.prompt)}
                                                value={selectedElement.data.prompt || ""}
                                                onChange={(e) => updateNode('prompt', e.target.value)}
                                                placeholder="Enter system prompt..."
                                            />
                                        </div>
                                    </div>
                                    {/* Without this slot the answer lives only in the run's `output`
                                        and the next node cannot read it as data: an HTTP request
                                        node receives `variables` as its stateJson, nothing else. */}
                                    <div className="form-row">
                                        <div className="form-group" style={{flex: 2}}>
                                            <label className="form-label">Output variable (optional)</label>
                                            <input className="form-input"
                                                   value={selectedElement.data.aiOutputVariable || ''}
                                                   onChange={(e) => updateNode('aiOutputVariable', e.target.value)}
                                                   placeholder="e.g. extracted_answer" />
                                            <div style={{fontSize: 11, color: '#777', marginTop: 2}}>
                                                Publishes this node's answer to a graph variable so a later node can
                                                read it as <code>{'{{name}}'}</code> - an HTTP request node gets it in
                                                stateJson, for example. Leave blank to keep the answer only in the
                                                run's output. A router node's answer is merged into variables
                                                automatically, so leave this empty there.
                                            </div>
                                        </div>
                                    </div>
                                    </>
                                )}
                            </>
                        )}
                    </>
                ) : (
                    <div style={{color: '#999', display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%'}}>
                        Select a node or edge to edit.
                    </div>
                )}
            </div>
            <Toast toast={toast} onDismiss={dismissToast} />
        </div>
    );
}

const root = createRoot(document.getElementById('root'));
root.render(
    <ReactFlowProvider>
        <Builder />
    </ReactFlowProvider>
);