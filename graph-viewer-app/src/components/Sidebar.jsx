import React from 'react';

export default function Sidebar({ graphs, selected, collapsed, setCollapsed, loadGraph, copyGraphId, copiedGraph, handleDelete }) {
    return (
        <div className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
            <div className="sidebar-header">
                <a href="help.html" style={{textDecoration: 'none', color: 'inherit'}}><h3>LangGraph</h3></a>
                <button onClick={() => window.location.href = 'builder.html?isNew=true'} style={{ background: '#2196f3', color: 'white', border: 'none', padding: '5px 10px', borderRadius: '4px', cursor: 'pointer', fontSize: '12px', marginRight: '10px' }}>+ New</button>
                <button className="toggle-btn" onClick={() => setCollapsed(!collapsed)}>{collapsed ? '»' : '«'}</button>
            </div>
            <div className="graph-list">
                {graphs.map(g => (
                    <div key={g.graphId} className={`graph-item ${g.graphId === selected ? 'active' : ''}`} onClick={() => loadGraph(g.graphId)}>
                        <div className="graph-icon-mini"></div>
                        <div className="graph-info">
                            <div className="name">{g.graphName}</div>
                            <div className="meta">{g.nodeCount} Nodes</div>
                        </div>

                        <div style={{ display: 'flex', gap: '5px', alignItems: 'center' }}>
                            <button className="icon-btn" title="Copy ID" onClick={(e) => { e.stopPropagation(); copyGraphId(g.graphId); }}>{copiedGraph === g.graphId ? '✅' : '📋'}</button>
                            <button style={{ background: 'transparent', border: '1px solid #2196f3', color: '#2196f3', padding: '4px 8px', borderRadius: '4px', cursor: 'pointer', fontSize: '10px', fontWeight: 'bold' }} onClick={(e) => { e.stopPropagation(); window.location.href = `builder.html?graphId=${g.graphId}&graphName=${g.graphName}`; }}>Edit</button>
                            <button className="delete-btn" onClick={(e) => handleDelete(e, g.graphId)}>✕</button>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
