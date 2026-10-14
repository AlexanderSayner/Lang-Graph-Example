import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createRoot } from 'react-dom/client';
import { getUnsyncedThreads, addLocalThread } from './utils/localThreadStore';
import ReactFlow, { Background, Controls, applyNodeChanges, applyEdgeChanges, MarkerType } from 'reactflow';
import dagre from 'dagre';

import 'reactflow/dist/style.css'; // Import ReactFlow CSS directly!
import './styles.css';
import {
    fetchGraphQL, LIST_QUERY, VIEW_QUERY, HISTORY_QUERY,
    BALANCE_QUERY, EXECUTE_MUTATION, SAVE_MUTATION,
    DELETE_MUTATION, REWIND_MUTATION, COPILOT_MUTATION,
    CLAIM_THREADS_MUTATION,
    layoutGraph, NODE_TYPES, EXECUTION_EVENT_TYPES
} from './constants';
import CopilotTab from './copilot';
import Sidebar from './components/Sidebar';
import ChatPanel from './components/ChatPanel';
import StateModal from './components/StateModal';
import { Toast, useToast } from './components/Toast';


// --- Main App ---
function App() {
    const [graphs, setGraphs] = useState([]);
    const [selected, setSelected] = useState(null);

    // --- Graph rendering state ---
    // status: 'idle' | 'loading' | 'ready' | 'error'.
    // `seq` is bumped on every load and becomes part of the ReactFlow key, so every graph open
    // mounts a fresh canvas whose fitView is calculated against the nodes it really shows.
    // fitMode: 'all' shows the whole graph when it is opened, 'readable' keeps at least ~45% zoom
    // after an auto layout, because a long process graph can be thousands of pixels wide and a
    // full overview would be unreadable.
    const [renderState, setRenderState] = useState({ graphId: null, seq: 0, status: 'idle', error: null, fitMode: 'all' });
    const loadSeqRef = useRef(0);            // guards against out-of-order GraphQL answers
    const viewportsRef = useRef(new Map());  // graphId -> { x, y, zoom } of the last view the user left
    const [collapsed, setCollapsed] = useState(false);
    const [aligning, setAligning] = useState(false); // "Auto Layout" request in flight
    const [layoutDir, setLayoutDir] = useState('TB'); // 'TB' top->bottom, 'LR' left->right
    const { toast, showToast, dismissToast } = useToast();

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
    // Copilot LLM selection. A per-request override of the service COPILOT_* / LLM_* default,
    // remembered in this browser so the choice survives reloads. It is deliberately NOT
    // graph-scoped: the copilot is a UI-wide assistant, so the chosen model follows you across
    // every graph instead of being stored in each graph's definition.
    const [copilotLLM, setCopilotLLM] = useState(() => {
        try {
            const stored = JSON.parse(localStorage.getItem('copilotLLM') || '{}');
            return { provider: stored.provider || '', model: stored.model || '' };
        } catch {
            return { provider: '', model: '' };
        }
    });
    useEffect(() => {
        localStorage.setItem('copilotLLM', JSON.stringify(copilotLLM));
    }, [copilotLLM]);

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

    // Economy State
    const [realBalance, setRealBalance] = useState(null);

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

    const handleUserLoggedIn = async () => {
        const unsynced = getUnsyncedThreads();
        if (unsynced.length === 0) {
            console.log("No unsynced threads. Nothing to do.");
            return;
        }

        try {
            const data = await fetchGraphQL(CLAIM_THREADS_MUTATION, {
                threads: unsynced.map(t => ({
                    threadId: t.threadId,
                    graphId: t.graphId,
                    title: t.title
                }))
            });

            if (data.claimLocalThreads.success) {
                console.log(`✅ Successfully synced ${data.claimLocalThreads.syncedCount} threads!`);
                markThreadsAsSynced(unsynced.map(t => t.threadId));
            } else {
                console.error("❌ Sync failed:", data.claimLocalThreads.message);
            }
        } catch (err) {
            console.error("❌ Network error during thread sync:", err);
        }
    };

    // Loads a graph into the canvas. The sequence number makes sure an older GraphQL answer can
    // never overwrite a newer one, and the canvas is cleared up front - otherwise the remounting
    // ReactFlow would still hold the previous graph and fit the viewport to the wrong nodes.
    const loadGraph = useCallback((id) => {
        if (!id) return;
        const seq = ++loadSeqRef.current;

        setGraphStatus('idle');

        setActiveNodeIds(new Set());
        setRewindOriginNodeId(null);
        setSelectedNodeId(null);
        setSelectedEdge(null);
        setNodes([]);
        setEdges([]);
        setRenderState({ graphId: id, seq, status: 'loading', error: null, fitMode: 'all' });

        fetchGraphQL(VIEW_QUERY, { graphId: id })
            .then(d => {
                if (seq !== loadSeqRef.current) return; // a newer graph was opened in the meantime
                if (!d || !d.getGraphView) {
                    console.error("❌ Failed to load graph view. Backend returned:", d);
                    setRenderState({ graphId: id, seq, status: 'error', error: 'Backend returned no graph view', fitMode: 'all' });
                    return;
                }

                const view = d.getGraphView;

                // Create Label Map
                const labels = {};
                view.nodes.forEach(n => { if(n.metadata?.label) labels[n.nodeId] = n.metadata.label; });
                setNodeLabels(labels);

                // Map Backend Nodes to React Flow Nodes
                const rN = view.nodes.map(n => {
                    const hasPos = n.position && (n.position.x !== 0 || n.position.y !== 0);
                    const prompt = n.metadata?.system_prompt || "";
                    const subgraphId = n.metadata?.subgraph_id;
                    const label = n.metadata?.label;
                    const httpUrl = n.metadata?.url || "";
                    const subgraphName = graphs.find(g => g.graphId === subgraphId)?.graphName || "Unknown Graph";

                    // Raw text of the node body - used by estimateNodeSize() for the automatic layout.
                    const bodyText = n.nodeType === NODE_TYPES.GRAPH
                        ? `Subgraph: ${subgraphName}`
                        : n.nodeType === NODE_TYPES.HTTP_REQUEST
                            ? `HTTP: ${n.metadata?.method || 'GET'} ${httpUrl}`
                            : prompt;

                    return {
                        id: n.nodeId,
                        className: `node-${n.nodeType.toLowerCase().replace(/_/g, '-')}`,
                        data: {
                            label: (
                                <div>
                                    <div className="node-header">
                                        <span>{label || n.nodeId}</span>
                                        <span className="node-type-badge">{n.nodeType}</span>
                                    </div>
                                    <div className="node-body">
                                        {n.nodeType === NODE_TYPES.GRAPH ? (
                                            <div>
                                                <div style={{marginBottom: '5px'}}><strong>Subgraph:</strong><br/>{subgraphName}</div>
                                                <div className="subgraph-link" onClick={(e) => { e.stopPropagation(); if(subgraphId) loadGraph(subgraphId); }}>View Subgraph &rarr;</div>
                                            </div>
                                        ) : n.nodeType === NODE_TYPES.HTTP_REQUEST ? (
                                            <div>
                                                <strong>HTTP:</strong> {n.metadata?.method || 'GET'}<br/>
                                                <span className="node-http-request-url">{httpUrl}</span>
                                            </div>
                                        ) : (
                                            <div className="node-prompt">{prompt}</div>
                                        )}
                                    </div>
                                </div>
                            ),
                            nodeType: n.nodeType,
                            titleText: label || n.nodeId,
                            bodyText,
                            promptText: prompt
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

                console.log(`✅ Loaded ${rN.length} nodes, ${rE.length} edges. Needs layout:`, needsLayout);
                setNodes(needsLayout ? layoutGraph(rN, rE) : rN);
                setEdges(rE);
                setRenderState({ graphId: id, seq, status: 'ready', error: null, fitMode: 'all' });
            })
            .catch(err => {
                if (seq !== loadSeqRef.current) return;
                console.error("❌ Network or GraphQL error loading graph:", err);
                setRenderState({ graphId: id, seq, status: 'error', error: err.message, fitMode: 'all' });
            });
    }, [graphs]);

    // The sidebar selection and the canvas are always in sync: clicking a graph always (re)loads
    // it, which is what makes returning from a subgraph to its parent work.
    const handleSelectGraph = (id) => {
        setSelected(id);
        loadGraph(id);
    };

    // The canvas is mounted only once its nodes are ready, so fitView measures the graph that is
    // really displayed. A remembered viewport is restored instead of fitting again.
    const canvasGraphId = renderState.status === 'ready' ? renderState.graphId : null;
    const canvasKey = canvasGraphId ? `${canvasGraphId}#${renderState.seq}` : null;
    const storedViewport = canvasGraphId ? viewportsRef.current.get(canvasGraphId) : null;
    const fitViewOptions = renderState.fitMode === 'readable'
        ? { padding: 0.2, duration: 250, maxZoom: 1, minZoom: 0.45 }
        : { padding: 0.2, duration: 250, maxZoom: 1 };

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

    useEffect(() => {
        fetchGraphQL(BALANCE_QUERY)
            .then(d => {
                if (d.getYandexBalance.success) {
                    setRealBalance(d.getYandexBalance);
                } else {
                    console.warn("Yandex balance fetch failed:", d.getYandexBalance.errorMessage);
                }
            })
            .catch(err => console.error("Failed to fetch balance:", err));
    }, []);

    useEffect(() => {
        const timer = setTimeout(() => {
            window.dispatchEvent(new Event('resize'));
        }, 350);

        return () => clearTimeout(timer);
    }, [collapsed]);

    const handleExecute = async () => {
        if (!input.trim() || !selected) return;

        addLocalThread(threadId, selected, `Chat on ${selected.slice(0, 8)}...`);

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
            else setMessages(prev => [...prev, { type: 'bot', text: outputText, tokens: result.totalTokens || -1  }]);

            if (eventType === EXECUTION_EVENT_TYPES.END) {
                setGraphStatus('finished');
                if (rewindOriginNodeId) {
                    setRewindOriginNodeId(null);
                }
            } else if (eventType === EXECUTION_EVENT_TYPES.WAITING_FOR_INPUT) {
                setGraphStatus('waiting');
            } else if (eventType === EXECUTION_EVENT_TYPES.START || eventType === EXECUTION_EVENT_TYPES.NODE_END) {
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
        if (!renderState.graphId) return;
        const positions = nodes.map(n => ({ nodeId: n.id, x: n.position.x, y: n.position.y }));
        await fetchGraphQL(SAVE_MUTATION, { graphId: renderState.graphId, positions });
        // The layout changed, so the remembered viewport is outdated - refit on the next open.
        viewportsRef.current.delete(renderState.graphId);
        showToast("Layout saved", "success");
    };

    // Re-arranges the nodes of the graph on the canvas with the size-aware dagre layout and stores
    // the result. The node sizes are estimated from their text, so tall nodes no longer overlap -
    // see estimateNodeSize() in constants.js. The viewport is refitted afterwards because the
    // whole arrangement changes.
    const handleAutoLayout = async () => {
        if (!renderState.graphId || !nodes.length || aligning) return;
        setAligning(true);
        try {
            const aligned = layoutGraph(nodes, edges, layoutDir);
            setNodes(aligned);
            viewportsRef.current.delete(renderState.graphId);
            // Bumping seq remounts the canvas, which then animates a fit of the new arrangement.
            setRenderState(prev => ({ ...prev, seq: prev.seq + 1, fitMode: 'readable' }));

            const positions = aligned.map(n => ({ nodeId: n.id, x: n.position.x, y: n.position.y }));
            await fetchGraphQL(SAVE_MUTATION, { graphId: renderState.graphId, positions });
            showToast(
                layoutDir === 'LR' ? "Aligned left → right and saved" : "Aligned top → bottom and saved",
                "success"
            );
        } catch (err) {
            console.error("❌ Auto layout failed:", err);
            showToast("Auto layout failed: " + err.message, "error", 6000);
        } finally {
            setAligning(false);
        }
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
                showToast("Rewind failed: " + data.rewindGraph.message, "error", 6000);
            }
        } catch (e) {
            showToast("Error: " + e.message, "error", 6000);
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
                selectedEdgeJson: selectedEdge ? JSON.stringify(selectedEdge) : null,
                // null means "inherit": the service falls back to COPILOT_* then LLM_*.
                copilotProvider: copilotLLM.provider || null,
                copilotModel: copilotLLM.model || null
            });

            const result = data.askCopilot;
            if (result.success) {
                // Add AI response to local state
                setCopilotMessages(prev => [...prev, { role: 'ai', text: result.aiResponse, tokens: result.totalTokens || -1 }]);
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
            {/* Notifications (replaces the blocking alert() popups) */}
            <Toast toast={toast} onDismiss={dismissToast} />

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
                setCollapsed={setCollapsed} onSelectGraph={handleSelectGraph}
                copyGraphId={copyGraphId} copiedGraph={copiedGraph} handleDelete={handleDelete}
                realBalance={realBalance}
                onUserLoggedIn={handleUserLoggedIn}
            />

            {/* Main Content */}
            <div className="main-area">
                <div className="canvas-container">
                    <div className="canvas-actions">
                        <button className="save-layout-btn" onClick={handleSaveLayout} disabled={!canvasKey}>Save Layout</button>
                        <div className="layout-dir" role="group" aria-label="Auto layout direction">
                            <button
                                className={layoutDir === 'TB' ? 'active' : ''}
                                onClick={() => setLayoutDir('TB')}
                                title="Auto layout: top to bottom"
                            >↓</button>
                            <button
                                className={layoutDir === 'LR' ? 'active' : ''}
                                onClick={() => setLayoutDir('LR')}
                                title="Auto layout: left to right (horizontal)"
                            >→</button>
                        </div>
                        <button className="auto-layout-btn" onClick={handleAutoLayout} disabled={!canvasKey || aligning}>
                            {aligning ? 'Aligning…' : 'Auto Layout'}
                        </button>
                    </div>
                    {canvasKey ? (
                        <ReactFlow
                            key={canvasKey}
                            nodes={nodes}
                            edges={edges}
                            onNodesChange={onNodesChange}
                            onEdgesChange={onEdgesChange}
                            defaultViewport={storedViewport || undefined}
                            fitView={!storedViewport}
                            fitViewOptions={fitViewOptions}
                            minZoom={0.05}
                            maxZoom={2}
                            onMoveEnd={(event, viewport) => {
                                if (renderState.graphId) viewportsRef.current.set(renderState.graphId, viewport);
                            }}
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
                    ) : (
                        <div className={`canvas-message${renderState.status === 'error' ? ' error' : ''}`}>
                            {renderState.status === 'error' ? (
                                <>
                                    <strong>Failed to load the graph.</strong>
                                    <span className="canvas-message-detail">{renderState.error}</span>
                                    <button onClick={() => loadGraph(renderState.graphId)}>Retry</button>
                                </>
                            ) : (
                                <span>Loading graph…</span>
                            )}
                        </div>
                    )}
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
                    copilotLLM={copilotLLM} setCopilotLLM={setCopilotLLM}
                    formatTimestamp={formatTimestamp}
                />
            </div>
        </React.Fragment>
    );
}

// Vite/React 18 uses createRoot
const root = createRoot(document.getElementById('root'));
root.render(<App />);
