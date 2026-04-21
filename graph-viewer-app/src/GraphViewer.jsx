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
import { useQuery } from '@apollo/client';
import { GET_GRAPH_VIEW } from './apollo-client';

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

// Graph Viewer Component - Read-only view with conditional edges
const GraphViewer = () => {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedGraphId, setSelectedGraphId] = useState('sample-graph-1');
  const [graphIdInput, setGraphIdInput] = useState('sample-graph-1');

  // Fetch graph view data from Redis via GraphQL
  const { loading, data, refetch, error } = useQuery(GET_GRAPH_VIEW, {
    variables: { graphId: selectedGraphId },
    skip: !selectedGraphId,
  });

  // Transform graph view data to ReactFlow format with conditional edges
  const transformToFlowElements = useCallback((graphViewData) => {
    if (!graphViewData?.getGraphView) return;

    const graphData = graphViewData.getGraphView;
    if (!graphData.success || !graphData.nodes) {
      console.error('Failed to load graph:', graphData.message);
      return;
    }

    // Create nodes with auto-layout
    const flowNodes = graphData.nodes.map((node, index) => ({
      id: node.nodeId,
      position: node.position || { x: 250, y: index * 100 },
      type: 'custom',
      data: { 
        label: node.nodeId, 
        nodeType: node.nodeType, 
        handlerName: node.handlerName 
      },
    }));

    // Create edges with conditional styling
    const flowEdges = graphData.edges.map((edge, index) => {
      const hasCondition = edge.condition && edge.condition.trim() !== '';
      return {
        id: `e-${edge.source}-${edge.target}`,
        source: edge.source,
        target: edge.target,
        label: edge.label || edge.condition || '',
        style: { 
          stroke: hasCondition ? (edge.condition.includes('success') ? '#28a745' : '#dc3545') : '#666', 
          strokeWidth: hasCondition ? 2 : 1 
        },
        labelStyle: { 
          fill: hasCondition ? (edge.condition.includes('success') ? '#28a745' : '#dc3545') : '#666', 
          fontWeight: 'bold' 
        },
        markerEnd: { type: MarkerType.ArrowClosed },
      };
    });

    setNodes(flowNodes);
    setEdges(flowEdges);
  }, [setNodes, setEdges]);

  // Load graph when data arrives
  useEffect(() => {
    if (data) {
      transformToFlowElements(data);
    }
  }, [data, transformToFlowElements]);

  // Handle graph ID change
  const handleLoadGraph = () => {
    setSelectedGraphId(graphIdInput);
  };

  return (
    <div className="graph-container">
      <div className="controls">
        <input
          type="text"
          placeholder="Enter Graph ID"
          value={graphIdInput}
          onChange={(e) => setGraphIdInput(e.target.value)}
          style={{ flex: 1, padding: '8px', borderRadius: '4px', border: '1px solid #ccc' }}
        />
        <button onClick={handleLoadGraph} disabled={loading}>
          {loading ? 'Loading...' : 'Load Graph'}
        </button>
        {selectedGraphId && (
          <span style={{ marginLeft: 'auto', color: '#666' }}>
            Viewing: {selectedGraphId}
          </span>
        )}
      </div>

      <div className="flow-container">
        {loading ? (
          <div className="loading">Loading graph...</div>
        ) : error ? (
          <div className="error">Error: {error.message}</div>
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
        {data?.getGraphView ? (
          <span>Graph: {data.getGraphView.graphName || selectedGraphId} - Status: {data.getGraphView.status || 'Unknown'}</span>
        ) : (
          <span>Ready - Enter a graph ID to view</span>
        )}
      </div>
    </div>
  );
};

export default GraphViewer;
