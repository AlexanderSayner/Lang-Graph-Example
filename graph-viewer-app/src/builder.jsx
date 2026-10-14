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
import { fetchGraphQL, LIST_QUERY, VIEW_QUERY, SAVE_MUTATION, NODE_TYPES, TOOL_METHOD_OPTIONS } from './constants';

// --- Params ---
const params = new URLSearchParams(window.location.search);
const IS_NEW = params.get('isNew') === 'true';
const URL_ID = params.get('graphId');
const URL_NAME = params.get('graphName');

// --- GraphQL Mutations specific to Builder ---
const BUILD_MUTATION = `mutation Build($input: BuildGraphInput!) { buildGraph(input: $input) { success } }`;
const TOOL_DEBUG_MUTATION = `mutation Debug($input: ToolDebugInput!) { debugTool(input: $input) { success statusCode body errorMessage } }`;

// --- Custom Node ---
const DesignNode = ({ data, selected }) => {
    let bgClass = 'node-action';
    if (data.type === NODE_TYPES.START) bgClass = 'node-start';
    if (data.type === NODE_TYPES.END) bgClass = 'node-end';
    if (data.type === NODE_TYPES.HUMAN) bgClass = 'node-human';
    if (data.type === NODE_TYPES.GRAPH) bgClass = 'node-graph';
    if (data.type === NODE_TYPES.TOOL) bgClass = 'node-tool';

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

    const reactFlowWrapper = useRef(null);
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
                setNodes(data.getGraphView.nodes.map(n => ({
                    id: n.nodeId, type: 'designNode', position: n.position || { x: 0, y: 0 },
                    data: {
                        label: n.nodeId, type: n.nodeType,
                        prompt: n.metadata?.system_prompt || "",
                        friendlyName: n.metadata?.label || "",
                        subgraphId: n.metadata?.subgraph_id || null,
                        method: n.metadata?.method || "GET",
                        url: n.metadata?.url || "",
                        headers: n.metadata?.headers || "{}",
                        body: n.metadata?.body || ""
                    }
                })));
                setEdges(data.getGraphView.edges.map((e, i) => ({
                    id: `e-${i}`, source: e.source, target: e.target,
                    label: e.condition || '', animated: true, markerEnd: { type: MarkerType.ArrowClosed }
                })));
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
            data: { label: id, type: type, prompt: "", subgraphId: null, friendlyName: null, method: "GET", url: "", headers: "{}", body: "" }
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

            const payloadNodes = nodes.map(n => {
                const meta = { system_prompt: n.data?.prompt || "", label: n.data?.friendlyName || "" };
                if (n.data?.type === NODE_TYPES.GRAPH && n.data.subgraphId) meta.subgraph_id = n.data.subgraphId;
                if (n.data?.type === NODE_TYPES.TOOL) { meta.method = n.data.method || "GET"; meta.url = n.data.url || ""; meta.headers = n.data.headers || "{}"; meta.body = n.data.body || ""; }
                return { nodeId: n.id, nodeType: n.data?.type, handlerName: `${n.id}Handler`, metadata: meta, position: { x: n.position.x, y: n.position.y } };
            });

            const payloadEdges = edges.map(e => ({ source: e.source, target: e.target, condition: e.label || null }));

            // UI coordinates ride inside every node of the build input (NodeInput.position), so the
            // definition and the layout are persisted in one write - no parallel positions list that
            // could drift away from nodes. The saveGraphCoordinates call below stays as a redundant
            // confirmation write (identical payload, idempotent on the server).
            const positions = nodes.map(n => ({ nodeId: n.id, x: n.position.x, y: n.position.y }));

            await fetchGraphQL(BUILD_MUTATION, { input: { graphId: graphId, graphName: graphName, nodes: payloadNodes, edges: payloadEdges } });
            await fetchGraphQL(SAVE_MUTATION, { graphId: graphId, positions: positions });

            showToast("Saved successfully!", "success");
            window.location.href = 'index.html';
        } catch (err) { showToast("Error: " + err.message, "error", 6000); } finally { setLoading(false); }
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

    // --- Tool Test Logic ---
    const testTool = async () => {
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
            const result = await fetchGraphQL(TOOL_DEBUG_MUTATION, {
                input: {
                    method: selectedNode.data.method,
                    url: selectedNode.data.url,
                    headers: selectedNode.data.headers,
                    body: selectedNode.data.body,
                    variables: JSON.stringify(mockVariables)
                }
            });
            setTestResult(result.debugTool);
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
                <button onClick={handleSave} className="btn-save" disabled={loading}>{loading ? "Saving..." : "Save Graph"}</button>
            </div>

            <div className="main-content">
                <div className="palette">
                    <div className={`palette-node ${hasStartNode ? 'disabled' : ''}`} title="Start Node" draggable={!hasStartNode} onDragStart={(e) => onDragStart(e, NODE_TYPES.START)}>🚀</div>
                    <div className="palette-node" title="Action Node" draggable onDragStart={(e) => onDragStart(e, NODE_TYPES.ACTION)}>🤖</div>
                    <div className="palette-node" title="Human Node" draggable onDragStart={(e) => onDragStart(e, NODE_TYPES.HUMAN)}>👤</div>
                    <div className="palette-node" title="Subgraph Node" draggable onDragStart={(e) => onDragStart(e, NODE_TYPES.GRAPH)}>🧩</div>
                    <div className="palette-node" title="Tool Node (HTTP)" draggable onDragStart={(e) => onDragStart(e, NODE_TYPES.TOOL)}>🔧</div>
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
                                    <input className="form-input" value={selectedElement.label || ""} onChange={(e) => updateEdge(e.target.value)} placeholder="e.g. intent == 'build'" />
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
                                ) : selectedElement.data.type === NODE_TYPES.TOOL ? (
                                    <>
                                        <div className="form-row">
                                            <div className="form-group" style={{flex: 0.3}}>
                                                <label className="form-label">Method</label>
                                                <select className="form-select" value={selectedElement.data.method || "GET"} onChange={(e) => updateNode('method', e.target.value)}>
                                                    {TOOL_METHOD_OPTIONS.map(m => <option key={m} value={m}>{m}</option>)}
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
                                        <button className="btn-test-tool" onClick={testTool} disabled={loading}>{loading ? "Testing..." : "🛠️ Test Tool"}</button>
                                        {testResult && (
                                            <div className="result-box">
                                                <strong>Result (Status {testResult.statusCode}):</strong>
                                                <pre style={{whiteSpace: 'pre-wrap', maxHeight: '60px', overflowY: 'auto', margin: 0}}>
                                                    {testResult.success ? testResult.body : testResult.errorMessage}
                                                </pre>
                                            </div>
                                        )}
                                    </>
                                ) : (
                                    <div className="form-row">
                                        <div className="form-group" style={{flex: 2}}>
                                            <div className="prompt-lib-bar">
                                                <label className="form-label" style={{margin: 0}}>System Prompt</label>
                                                <button className="btn-sm" onClick={() => insertVariable('input')}>+ Input</button>
                                                <button className="btn-sm" onClick={() => insertVariable('tool_result')}>+ Tool Result</button>
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