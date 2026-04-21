import React, { useState, useCallback, useEffect } from 'react';
import ReactFlow, { 
  Background, 
  Controls as FlowControls,
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType
} from 'reactflow';
import 'reactflow/dist/style.css';
import { useQuery, useMutation, useSubscription } from '@apollo/client';
import { LIST_GRAPHS, BUILD_GRAPH, EXECUTE_GRAPH_STREAM, DELETE_GRAPH } from './apollo-client';

// Custom Node Component
const CustomNode = ({ data }) => {
  return (
    <div className={`node-card ${data.status || ''}`}>
      <div className="node-title">{data.label}</div>
      <div className="node-type">{data.nodeType}</div>
      {data.handlerName && (
        <div className="node-type">Handler: {data.handlerName}</div>
      )}
    </div>
  );
};

const nodeTypes = {
  custom: CustomNode,
};

// Graph Viewer Component
const GraphViewer = () => {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedGraph, setSelectedGraph] = useState(null);
  const [graphInput, setGraphInput] = useState({
    graphId: '',
    graphName: '',
    input: '',
  });
  const [executionState, setExecutionState] = useState(null);
  const [activeNodeId, setActiveNodeId] = useState(null);

  // Fetch available graphs
  const { loading: graphsLoading, data: graphsData, refetch } = useQuery(LIST_GRAPHS, {
    variables: { pageSize: 10 },
    skip: false,
  });

  // Build graph mutation
  const [buildGraph] = useMutation(BUILD_GRAPH);

  // Execute graph stream subscription
  const { data: streamData, subscribeToMore } = useSubscription(EXECUTE_GRAPH_STREAM, {
    variables: { 
      input: { 
        graphId: selectedGraph?.graphId || '', 
        input: graphInput.input || 'test input',
        threadId: null,
        context: null
      } 
    },
    skip: !selectedGraph,
    onData: ({ data }) => {
      if (data?.executeGraphStream) {
        const event = data.executeGraphStream;
        setExecutionState(event.state);
        if (event.nodeId) {
          setActiveNodeId(event.nodeId);
          // Update node status based on event
          setNodes((nds) =>
            nds.map((node) => ({
              ...node,
              data: {
                ...node.data,
                status: node.id === event.nodeId ? 'active' : '',
              },
            }))
          );
        }
        if (event.eventType === 'ERROR') {
          console.error('Graph execution error:', event.errorMessage);
        }
      }
    },
  });

  // Delete graph mutation
  const [deleteGraph] = useMutation(DELETE_GRAPH);

  // Create sample graph with conditional edges
  const createSampleGraph = useCallback(async () => {
    const sampleInput = {
      graphId: `graph-${Date.now()}`,
      graphName: 'Sample Conditional Graph',
      nodes: [
        { nodeId: 'start', nodeType: 'START', handlerName: 'startHandler', metadata: {} },
        { nodeId: 'process', nodeType: 'PROCESS', handlerName: 'processHandler', metadata: {} },
        { nodeId: 'decision', nodeType: 'DECISION', handlerName: 'decisionHandler', metadata: {} },
        { nodeId: 'success', nodeType: 'SUCCESS', handlerName: 'successHandler', metadata: {} },
        { nodeId: 'failure', nodeType: 'FAILURE', handlerName: 'failureHandler', metadata: {} },
        { nodeId: 'end', nodeType: 'END', handlerName: 'endHandler', metadata: {} },
      ],
      edges: [
        { source: 'start', target: 'process', condition: null },
        { source: 'process', target: 'decision', condition: null },
        { source: 'decision', target: 'success', condition: 'result == "success"' },
        { source: 'decision', target: 'failure', condition: 'result == "failure"' },
        { source: 'success', target: 'end', condition: null },
        { source: 'failure', target: 'end', condition: null },
      ],
      config: {},
    };

    try {
      const result = await buildGraph({ variables: { input: sampleInput } });
      if (result.data.buildGraph.success) {
        alert('Graph created successfully!');
        refetch();
      } else {
        alert('Failed to create graph: ' + result.data.buildGraph.message);
      }
    } catch (error) {
      console.error('Error building graph:', error);
      alert('Error creating graph');
    }
  }, [buildGraph, refetch]);

  // Transform graph data to ReactFlow format
  const transformToFlowElements = useCallback((graphs) => {
    if (!graphs?.listGraphs?.graphs) return;

    const selected = graphs.listGraphs.graphs[0];
    if (!selected) return;

    setSelectedGraph(selected);

    // Create nodes
    const flowNodes = [
      { id: 'start', position: { x: 250, y: 0 }, type: 'custom', data: { label: 'Start', nodeType: 'START', handlerName: 'startHandler' } },
      { id: 'process', position: { x: 250, y: 100 }, type: 'custom', data: { label: 'Process', nodeType: 'PROCESS', handlerName: 'processHandler' } },
      { id: 'decision', position: { x: 250, y: 200 }, type: 'custom', data: { label: 'Decision', nodeType: 'DECISION', handlerName: 'decisionHandler' } },
      { id: 'success', position: { x: 100, y: 300 }, type: 'custom', data: { label: 'Success', nodeType: 'SUCCESS', handlerName: 'successHandler' } },
      { id: 'failure', position: { x: 400, y: 300 }, type: 'custom', data: { label: 'Failure', nodeType: 'FAILURE', handlerName: 'failureHandler' } },
      { id: 'end', position: { x: 250, y: 400 }, type: 'custom', data: { label: 'End', nodeType: 'END', handlerName: 'endHandler' } },
    ];

    // Create edges with conditional styling
    const flowEdges = [
      { id: 'e1-2', source: 'start', target: 'process', markerEnd: { type: MarkerType.ArrowClosed } },
      { id: 'e2-3', source: 'process', target: 'decision', markerEnd: { type: MarkerType.ArrowClosed } },
      { 
        id: 'e3-4', 
        source: 'decision', 
        target: 'success', 
        label: 'result == "success"',
        style: { stroke: '#28a745', strokeWidth: 2 },
        labelStyle: { fill: '#28a745', fontWeight: 'bold' },
        markerEnd: { type: MarkerType.ArrowClosed }
      },
      { 
        id: 'e3-5', 
        source: 'decision', 
        target: 'failure', 
        label: 'result == "failure"',
        style: { stroke: '#dc3545', strokeWidth: 2 },
        labelStyle: { fill: '#dc3545', fontWeight: 'bold' },
        markerEnd: { type: MarkerType.ArrowClosed }
      },
      { id: 'e4-6', source: 'success', target: 'end', markerEnd: { type: MarkerType.ArrowClosed } },
      { id: 'e5-6', source: 'failure', target: 'end', markerEnd: { type: MarkerType.ArrowClosed } },
    ];

    setNodes(flowNodes);
    setEdges(flowEdges);
  }, [setNodes, setEdges]);

  // Load graph when data arrives
  useEffect(() => {
    if (graphsData) {
      transformToFlowElements(graphsData);
    }
  }, [graphsData, transformToFlowElements]);

  // Execute graph
  const executeGraph = async () => {
    if (!selectedGraph) return;
    
    try {
      const result = await buildGraph({
        variables: {
          input: {
            graphId: selectedGraph.graphId,
            graphName: selectedGraph.graphName,
            nodes: [
              { nodeId: 'start', nodeType: 'START', handlerName: 'startHandler', metadata: {} },
              { nodeId: 'process', nodeType: 'PROCESS', handlerName: 'processHandler', metadata: {} },
              { nodeId: 'decision', nodeType: 'DECISION', handlerName: 'decisionHandler', metadata: {} },
              { nodeId: 'success', nodeType: 'SUCCESS', handlerName: 'successHandler', metadata: {} },
              { nodeId: 'failure', nodeType: 'FAILURE', handlerName: 'failureHandler', metadata: {} },
              { nodeId: 'end', nodeType: 'END', handlerName: 'endHandler', metadata: {} },
            ],
            edges: [
              { source: 'start', target: 'process', condition: null },
              { source: 'process', target: 'decision', condition: null },
              { source: 'decision', target: 'success', condition: 'result == "success"' },
              { source: 'decision', target: 'failure', condition: 'result == "failure"' },
              { source: 'success', target: 'end', condition: null },
              { source: 'failure', target: 'end', condition: null },
            ],
            config: {},
          }
        }
      });
      
      if (result.data.buildGraph.success) {
        // Subscribe to execution stream
        subscribeToMore({
          document: EXECUTE_GRAPH_STREAM,
          variables: {
            input: {
              graphId: selectedGraph.graphId,
              input: graphInput.input || 'test input',
              threadId: null,
              context: null
            }
          }
        });
      }
    } catch (error) {
      console.error('Error executing graph:', error);
    }
  };

  // Delete current graph
  const handleDeleteGraph = async () => {
    if (!selectedGraph) return;
    
    try {
      const result = await deleteGraph({ variables: { graphId: selectedGraph.graphId } });
      if (result.data.deleteGraph.success) {
        setSelectedGraph(null);
        setNodes([]);
        setEdges([]);
        refetch();
      }
    } catch (error) {
      console.error('Error deleting graph:', error);
    }
  };

  return (
    <div className="graph-container">
      <div className="controls">
        <input
          type="text"
          placeholder="Graph ID"
          value={graphInput.graphId}
          onChange={(e) => setGraphInput({ ...graphInput, graphId: e.target.value })}
        />
        <input
          type="text"
          placeholder="Graph Name"
          value={graphInput.graphName}
          onChange={(e) => setGraphInput({ ...graphInput, graphName: e.target.value })}
        />
        <input
          type="text"
          placeholder="Input data"
          value={graphInput.input}
          onChange={(e) => setGraphInput({ ...graphInput, input: e.target.value })}
        />
        <button onClick={createSampleGraph} disabled={graphsLoading}>
          Create Sample Graph
        </button>
        <button onClick={executeGraph} disabled={!selectedGraph}>
          Execute Graph
        </button>
        <button onClick={handleDeleteGraph} disabled={!selectedGraph} style={{ background: '#dc3545' }}>
          Delete Graph
        </button>
        {selectedGraph && (
          <span style={{ marginLeft: 'auto', color: '#666' }}>
            Selected: {selectedGraph.graphName} ({selectedGraph.status})
          </span>
        )}
      </div>

      <div className="flow-container">
        {graphsLoading ? (
          <div className="loading">Loading graphs...</div>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={nodeTypes}
            fitView
            attributionPosition="bottom-left"
          >
            <Background />
            <FlowControls />
            <MiniMap 
              nodeColor={(node) => {
                if (node.data.status === 'active') return '#28a745';
                if (node.data.status === 'error') return '#dc3545';
                return '#007bff';
              }}
              zoomable
              pannable
            />
          </ReactFlow>
        )}
      </div>

      <div className="status-bar">
        {executionState ? (
          <span>Current State: {JSON.stringify(executionState)}</span>
        ) : (
          <span>Ready - Create a graph to get started</span>
        )}
        {activeNodeId && <span style={{ marginLeft: '16px' }}>Active Node: {activeNodeId}</span>}
      </div>
    </div>
  );
};

export default GraphViewer;
