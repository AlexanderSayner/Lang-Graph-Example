const { useState, useEffect, useRef, useCallback } = React;
const { ReactFlow, Background, Controls, applyNodeChanges, applyEdgeChanges } = window.ReactFlow;
const MarkerType = window.ReactFlow.MarkerType || { ArrowClosed: 'arrowclosed' };

// --- Main App ---
function App() {
    const [graphs, setGraphs] = useState([]);
    const [selected, setSelected] = useState(null);
    const [collapsed, setCollapsed] = useState(false);

    const [nodes, setNodes] = useState([]);
    const onNodesChange = useCallback(
        (changes) => setNodes((nds) => applyNodeChanges(changes, nds)),
        []
    );
    const [edges, setEdges] = useState([]);
    const onEdgesChange = useCallback(
        (changes) => setEdges((eds) => applyEdgeChanges(changes, eds)),
        []
    );

    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState("");
    const [threadId, setThreadId] = useState(Date.now().toString());
    const [loading, setLoading] = useState(false);
    const chatEndRef = useRef(null);

    const [activeTab, setActiveTab] = useState('chat');
    const [historyItems, setHistoryItems] = useState([]);

    const [modalViewMode, setModalViewMode] = useState('diff'); // 'diff' | 'full'

    const [graphStatus, setGraphStatus] = useState('idle'); // Values: 'idle' | 'running' | 'waiting' | 'finished'

    // --- Copilot State ---
    const [copilotMessages, setCopilotMessages] = useState([]);
    const [copilotInput, setCopilotInput] = useState("");
    const [copilotLoading, setCopilotLoading] = useState(false);

    const [selectedNodeId, setSelectedNodeId] = useState(null);
    const [selectedEdge, setSelectedEdge] = useState(null);

    // Map for NodeID -> Friendly Name
    const [nodeLabels, setNodeLabels] = useState({});

    // Copy States
    const [copiedThread, setCopiedThread] = useState(false);
    const [copiedGraph, setCopiedGraph] = useState(null);

    // Modal State
    const [modalData, setModalData] = useState(null);

    // Visual Path Tracing State
    const [activeNodeIds, setActiveNodeIds] = useState(new Set());
    // Track which node we rewound from (for purple highlighting)
    const [rewindOriginNodeId, setRewindOriginNodeId] = useState(null);

    // --- Custom Resizer State ---
    const [panelHeight, setPanelHeight] = useState(300);
    const panelRef = useRef(null);
    const isResizing = useRef(false);

    // Resizer Handlers
    const stopResizing = useCallback((e) => {
        if (!isResizing.current) return;

        // Calculate new height based on mouse position relative to window bottom
        const newHeight = window.innerHeight - e.clientY;

        // Clamp height between min and max
        if (newHeight >= 150 && newHeight <= 800) {
            setPanelHeight(newHeight);
        } else if (newHeight < 150) {
            setPanelHeight(150);
        } else {
            setPanelHeight(800);
        }

        // Cleanup
        if (e.type === 'mouseup') {
            isResizing.current = false;
            document.removeEventListener('mousemove', stopResizing);
            document.removeEventListener('mouseup', stopResizing);
        }
    }, []);

    const startResizing = useCallback((e) => {
        e.preventDefault();
        isResizing.current = true;
        document.addEventListener('mousemove', stopResizing);
        document.addEventListener('mouseup', stopResizing);
    }, []);

    // Load Graph List
    useEffect(() => {
        fetchGraphQL(LIST_QUERY).then(d => setGraphs(d.listGraphs.graphs)).catch(console.error);
    }, []);

    // Scroll chat to bottom
    useEffect(() => {
        if (activeTab === 'chat' || activeTab === 'copilot') {
            chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
        }
    }, [messages, copilotMessages, copilotLoading, activeTab]);

    const loadHistory = useCallback(async () => {
        try {
            const data = await fetchGraphQL(HISTORY_QUERY, {
                graphId: selected,
                threadId
            });

            if (data.getExecutionHistory.success) {
                // Backend now returns history items with optional 'diff' field
                setHistoryItems(data.getExecutionHistory.history);

                // Visual Path Tracing: Calculate Active Nodes
                const executedIds = new Set(
                    data.getExecutionHistory.history
                        .map(h => h.nodeId)
                        .filter(id => id && !id.startsWith("Pending"))
                );
                setActiveNodeIds(executedIds);

            } else {
                console.error("History error:", data.getExecutionHistory.errorMessage);
                setHistoryItems([]);
                setActiveNodeIds(new Set());
            }
        } catch (err) {
            console.error("Failed to load history:", err);
            setHistoryItems([]);
            setActiveNodeIds(new Set());
        }
    }, [selected, threadId]);

    // Load History
    useEffect(() => {
        if (activeTab === 'history' && selected && threadId) loadHistory();
    }, [activeTab, selected, threadId, loadHistory]);

    useEffect(() => {
        if (selected && graphs.length > 0) {
            loadGraph(selected);
        }
    }, [graphs, selected]); // Re-run when graphs are loaded

    const loadGraph = (id) => {
        setSelected(id);
        setGraphStatus('idle');

        setActiveNodeIds(new Set());
        setRewindOriginNodeId(null);
        setSelectedNodeId(null);
        setSelectedEdge(null);

        fetchGraphQL(VIEW_QUERY, { graphId: id }).then(d => {
            const view = d.getGraphView;

            // Create Label Map
            const labels = {};
            view.nodes.forEach(n => { if(n.metadata?.label) labels[n.nodeId] = n.metadata.label; });
            setNodeLabels(labels);

            // Map Backend Nodes to React Flow Nodes
            const rN = view.nodes.map(n => {
                const hasPos = n.position && (n.position.x || n.position.y);
                const prompt = n.metadata?.system_prompt || "";
                const subgraphId = n.metadata?.subgraph_id;
                const label = n.metadata?.label;
                const toolUrl = n.metadata?.url || "";

                const subgraphName = graphs.find(g => g.graphId === subgraphId)?.graphName || "Unknown Graph";

                return {
                    id: n.nodeId,
                    className: `node-${n.nodeType.toLowerCase()}`,
                    data: {
                        label: (
                            <div>
                                <div className="node-header">
                                    {/* Use Label if exists, else ID */}
                                    <span>{label || n.nodeId}</span>
                                    <span className="node-type-badge">{n.nodeType}</span>
                                </div>

                                <div className="node-body">
                                    {n.nodeType === 'GRAPH' ? (
                                        <div>
                                            <div style={{marginBottom: '5px'}}><strong>Subgraph:</strong><br/>{subgraphName}</div>
                                            <div className="subgraph-link" onClick={(e) => { e.stopPropagation(); if(subgraphId) loadGraph(subgraphId); }}>View Subgraph &rarr;</div>
                                        </div>
                                    ) : n.nodeType === 'TOOL' ? (
                                        <div>
                                            <strong>Tool:</strong> {n.metadata?.method || 'GET'}<br/>
                                            <span style={{fontSize: '9px', color: '#666'}}>{toolUrl}</span>
                                        </div>
                                    ) : (
                                        <div className="node-prompt">{prompt}</div>
                                    )}
                                </div>
                            </div>
                        ),
                        nodeType: n.nodeType
                    },
                    position: hasPos ? n.position : { x: 0, y: 0 }
                };
            });

            const rE = view.edges.map((e, i) => ({
                id: `e${i}`, source: e.source, target: e.target,
                label: e.condition,
                animated: true,
                markerEnd: { type: MarkerType.ArrowClosed }
            }));

            const needsLayout = rN.every(n => n.position.x === 0 && n.position.y === 0);
            setNodes(needsLayout ? layoutGraph(rN, rE) : rN);
            setEdges(rE);
        });
    };

    // Re-apply node and edge styles when selection or execution state changes
    useEffect(() => {
        if (!nodes.length) return;

        // 1. Update Nodes
        setNodes(nds => nds.map(n => {
            const wasExecuted = activeNodeIds.has(n.id);
            const isRewindOrigin = (rewindOriginNodeId && n.id === rewindOriginNodeId);
            const isSelected = (selectedNodeId && n.id === selectedNodeId);

            // Safely extract base class (strips any old highlights including the new blue one)
            const baseClass = (n.className || '')
                .replace(/node-active-path|node-rewind-origin|node-selected/g, '')
                .trim() || 'node-action';

            let highlightClass = '';

            // Priority: Selected (Blue) > Rewind Origin (Purple) > Executed (Green)
            if (isSelected) {
                highlightClass = 'node-selected';
            } else if (wasExecuted || isRewindOrigin) {
                highlightClass = isRewindOrigin ? 'node-rewind-origin' : 'node-active-path';
            }

            const newClass = `${baseClass} ${highlightClass}`.trim();
            return n.className !== newClass ? { ...n, className: newClass } : n;
        }));

        // 2. Update Edges
        setEdges(eds => eds.map(e => {
            const isActive = activeNodeIds.has(e.source) && activeNodeIds.has(e.target);
            const isFromRewindOrigin = (rewindOriginNodeId && e.source === rewindOriginNodeId);

            // Check if this specific edge is selected
            const isSelected = selectedEdge &&
                               e.source === selectedEdge.source &&
                               e.target === selectedEdge.target;

            let strokeColor = '#b1b1b7';
            let strokeWidth = 1;
            let markerColor = '#b1b1b7';

            if (isActive) {
                strokeColor = isFromRewindOrigin ? '#9c27b0' : '#4caf50';
                strokeWidth = 2;
                markerColor = strokeColor;
            }

            // Override marker color if the edge is selected
            if (isSelected) {
                markerColor = '#2196f3'; // Material Blue
            }

            const newStyle = { stroke: strokeColor, strokeWidth: strokeWidth };
            const newMarker = { ...e.markerEnd, color: markerColor };

            // Apply custom class for selection
            const newClassName = isSelected ? 'edge-selected' : '';

            // Return updated edge if ANYTHING changed (style, marker, or className)
            if (e.style?.stroke !== newStyle.stroke ||
                e.markerEnd?.color !== newMarker.color ||
                e.className !== newClassName) {
                return {
                    ...e,
                    style: newStyle,
                    markerEnd: newMarker,
                    className: newClassName
                };
            }
            return e;
        }));

    }, [activeNodeIds, rewindOriginNodeId, selectedNodeId, selectedEdge]);

    const handleExecute = async () => {
        if (!input.trim() || !selected) return;

        const userText = input;
        setMessages(prev => [...prev, { type: 'user', text: userText }]);
        setInput("");
        setLoading(true);

        try {
            const data = await fetchGraphQL(EXECUTE_MUTATION, {
                input: { graphId: selected, threadId: threadId, input: userText }
            });

            const result = data.executeGraph;
            let outputText = "Graph executed successfully.";
            let errorText = null;
            let eventType = null;

            if (Array.isArray(result)) {
                const lastEvent = result[result.length - 1] || {};
                outputText = lastEvent.state?.output || lastEvent.output || outputText;
                errorText = lastEvent.errorMessage;
                eventType = lastEvent.eventType;
            } else if (result) {
                if (result.state && result.state.output) outputText = result.state.output;
                else if (result.output) outputText = result.output;
                errorText = result.errorMessage;
                eventType = result.eventType;
            }

            if (errorText) setMessages(prev => [...prev, { type: 'error', text: errorText }]);
            else setMessages(prev => [...prev, { type: 'bot', text: outputText }]);

            if (eventType === 'END') {
                setGraphStatus('finished');
                if (rewindOriginNodeId) {
                    setRewindOriginNodeId(null);
                }
            } else if (eventType === 'WAITING_FOR_INPUT') {
                setGraphStatus('waiting');
            } else if (eventType === 'START' || eventType === 'NODE_END') {
                setGraphStatus('running');
            }

            // Refresh history to update tracing
            await loadHistory();

        } catch (err) {
            setMessages(prev => [...prev, { type: 'error', text: "Error: " + err.message }]);
            setGraphStatus('idle');
        } finally {
            setLoading(false);
        }
    };

    const handleSaveLayout = async () => {
        if(!selected) return;
        const positions = nodes.map(n => ({ nodeId: n.id, x: n.position.x, y: n.position.y }));
        await fetchGraphQL(SAVE_MUTATION, { graphId: selected, positions });
        alert("Layout Saved!");
    };

    const handleDelete = async (e, id) => {
        e.stopPropagation();
        if(!window.confirm("Delete?")) return;
        await fetchGraphQL(DELETE_MUTATION, { graphId: id });
        setGraphs(prev => prev.filter(g => g.graphId !== id));
    };

    const resetThread = () => {
        setThreadId(Date.now().toString());
        setMessages([]);
        setHistoryItems([]);
        setActiveNodeIds(new Set());
        setGraphStatus('idle');
        setRewindOriginNodeId(null);
        setCopilotMessages([]);
    };

    const copyThreadId = () => {
        navigator.clipboard.writeText(threadId).then(() => { setCopiedThread(true); setTimeout(() => setCopiedThread(false), 2000); });
    };

    const copyGraphId = (id) => {
        navigator.clipboard.writeText(id).then(() => { setCopiedGraph(id); setTimeout(() => setCopiedGraph(null), 2000); });
    };

    const formatTimestamp = (ts) => { try { return new Date(ts).toLocaleString(); } catch { return ts; } };

    // Use Labels in History
    const getNodeDisplayName = (id) => {
        if (!id) return "Start";
        if (id.startsWith("Pending:")) {
            const match = id.match(/'([^']+)'/);
            const nodeId = match ? match[1] : id;
            return `Next: ${nodeLabels[nodeId] || nodeId}`;
        }
        return nodeLabels[id] || id;
    };

    // --- Rewind Logic ---
    const handleRewind = async (stateJson, nodeId) => {
        if (!window.confirm("Rewind to this state? You can then send a new message to continue from here.")) return;

        try {
            const data = await fetchGraphQL(REWIND_MUTATION, {
                graphId: selected,
                threadId: threadId,
                stateJson: JSON.stringify(stateJson),
                targetNodeId: nodeId
            });

            if(data.rewindGraph.success) {
                console.log("State rewound. Switching to Chat.");
                setModalData(null);
                setActiveTab('chat');
                setInput("Continue from previous step.");

                // 1. Mark the origin node to receive the Purple highlight
                setRewindOriginNodeId(nodeId);

                // 2. Fetch the NEW history from the backend.
                // This naturally updates activeNodeIds to ONLY include nodes up to the rewind point (keeping them green),
                // and automatically drops nodes that happened after (reverting them to default color).
                await loadHistory();

            } else {
                alert("Rewind failed: " + data.rewindGraph.message);
            }
        } catch (e) {
            alert("Error: " + e.message);
        }
    };

    const handleCopilotSend = async () => {
        if (!copilotInput.trim() || !selected) return;

        const userText = copilotInput;

        // Add user message to local state immediately for UI responsiveness
        const updatedHistory = [...copilotMessages, { role: 'user', text: userText }];
        setCopilotMessages(updatedHistory);
        setCopilotInput("");
        setCopilotLoading(true);

        try {
            const data = await fetchGraphQL(COPILOT_MUTATION, {
                graphId: selected,
                threadId: threadId,
                message: userText,
                chatHistory: JSON.stringify(updatedHistory),
                selectedNodeId: selectedNodeId,
                selectedEdgeJson: selectedEdge ? JSON.stringify(selectedEdge) : null
            });

            const result = data.askCopilot;
            if (result.success) {
                // Add AI response to local state
                setCopilotMessages(prev => [...prev, { role: 'ai', text: result.aiResponse }]);
            } else {
                setCopilotMessages(prev => [...prev, { role: 'error', text: result.errorMessage }]);
            }
        } catch (err) {
            setCopilotMessages(prev => [...prev, { role: 'error', text: err.message }]);
        } finally {
            setCopilotLoading(false);
        }
    };

    return (
        <React.Fragment>
            {/* Modal */}
            <StateModal
                modalData={modalData} modalViewMode={modalViewMode}
                setModalData={setModalData} setModalViewMode={setModalViewMode}
                handleRewind={handleRewind} getNodeDisplayName={getNodeDisplayName}
                formatTimestamp={formatTimestamp}
            />

            {/* Sidebar */}
            <Sidebar
                graphs={graphs} selected={selected} collapsed={collapsed}
                setCollapsed={setCollapsed} loadGraph={loadGraph}
                copyGraphId={copyGraphId} copiedGraph={copiedGraph} handleDelete={handleDelete}
            />

            {/* Main Content */}
            <div className="main-area">
                <div className="canvas-container">
                    <button className="save-layout-btn" onClick={handleSaveLayout}>Save Layout</button>
                    <ReactFlow
                        nodes={nodes}
                        edges={edges}
                        onNodesChange={onNodesChange}
                        onEdgesChange={onEdgesChange}
                        fitView
                        onNodeClick={(event, node) => {
                            setSelectedNodeId(node.id);
                            setSelectedEdge(null);
                        }}
                        onEdgeClick={(event, edge) => {
                            setSelectedEdge({
                                source: edge.source,
                                target: edge.target,
                                condition: edge.label || "None (unconditional)"
                            });
                            setSelectedNodeId(null);
                        }}
                        onPaneClick={() => {
                            setSelectedNodeId(null);
                            setSelectedEdge(null);
                        }}
                    >
                        <Background />
                        <Controls />
                    </ReactFlow>
                </div>

                {/* Chat Area */}
                <ChatPanel
                    activeTab={activeTab} setActiveTab={setActiveTab} selected={selected}
                    threadId={threadId} copiedThread={copiedThread} copiedGraph={copiedGraph}
                    copyThreadId={copyThreadId} copyGraphId={copyGraphId} resetThread={resetThread}
                    panelHeight={panelHeight} panelRef={panelRef} startResizing={startResizing}
                    messages={messages} graphStatus={graphStatus} loading={loading}
                    input={input} setInput={setInput} handleExecute={handleExecute} chatEndRef={chatEndRef}
                    historyItems={historyItems} setModalData={setModalData}
                    getNodeDisplayName={getNodeDisplayName} handleRewind={handleRewind}
                    copilotMessages={copilotMessages} copilotInput={copilotInput}
                    setCopilotInput={setCopilotInput} copilotLoading={copilotLoading}
                    handleCopilotSend={handleCopilotSend}
                    formatTimestamp={formatTimestamp}
                />
            </div>
        </React.Fragment>
    );
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
