import React from 'react';

export default function Sidebar({ graphs, selected, collapsed, setCollapsed, loadGraph, copyGraphId, copiedGraph, handleDelete, realBalance }) {
    return (
        <div className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
            <div className="sidebar-header">
                <a href="help.html" style={{textDecoration: 'none', color: 'inherit'}}><h3>LangGraph</h3></a>
                <button onClick={() => window.location.href = 'builder.html?isNew=true'} style={{ background: '#2196f3', color: 'white', border: 'none', padding: '5px 10px', borderRadius: '4px', cursor: 'pointer', fontSize: '12px', marginRight: '10px' }}>+ New</button>
                <button className="toggle-btn" onClick={() => setCollapsed(!collapsed)}>{collapsed ? '»' : '«'}</button>
            </div>

            {realBalance && (
                <div style={{
                    padding: '10px 15px',
                    background: realBalance.success
                        ? 'linear-gradient(135deg, #fff3e0 0%, #ffe0b2 100%)'
                        : 'linear-gradient(135deg, #ffebee 0%, #ffcdd2 100%)',
                    borderBottom: realBalance.success ? '1px solid #ffb74d' : '1px solid #ef9a9a',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    fontSize: '12px',
                    fontWeight: '600',
                    color: realBalance.success ? '#e65100' : '#b71c1c'
                }}>
                    <span>{realBalance.success ? '💰 Yandex Balance:' : '⚠️ Billing Error'}</span>
                    <span style={{
                        background: '#fff',
                        padding: '2px 8px',
                        borderRadius: '12px',
                        border: realBalance.success ? '1px solid #ffb74d' : '1px solid #ef9a9a',
                        boxShadow: '0 1px 2px rgba(0,0,0,0.05)'
                    }}>
                        {realBalance.success
                            ? `${realBalance.balance.toFixed(2)} ${realBalance.currency}`
                            : 'Check API Key'}
                    </span>
                </div>
            )}

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
