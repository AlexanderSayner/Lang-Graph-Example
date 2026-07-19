import React, { useState, useEffect } from 'react';
import { fetchGraphQL, GET_CURRENT_USER_QUERY, LOGIN_MUTATION, REGISTER_MUTATION, LOGOUT_MUTATION } from '../constants';

export default function Sidebar({
    graphs, selected, collapsed, setCollapsed, onSelectGraph,
    copyGraphId, copiedGraph, handleDelete, realBalance,
    onUserLoggedIn
}) {
    const [isLoggedIn, setIsLoggedIn] = useState(false);
    const [currentUser, setCurrentUser] = useState('');
    const [showAuthForm, setShowAuthForm] = useState(false);
    const [formUser, setFormUser] = useState('');
    const [formPass, setFormPass] = useState('');
    const [loading, setLoading] = useState(false);
    const [message, setMessage] = useState('');

    // Check session on mount
    useEffect(() => {
        fetchGraphQL(GET_CURRENT_USER_QUERY)
            .then(data => {
                if (data?.getCurrentUser?.username) {
                    setIsLoggedIn(true);
                    setCurrentUser(data.getCurrentUser.username);
                }
            })
            .catch(err => {
                console.warn("Session check failed:", err.message);
                setIsLoggedIn(false);
                setCurrentUser('');
            });
    }, []);

    const handleAuth = async (isRegister) => {
        if (!formUser.trim() || !formPass.trim()) return;
        setLoading(true);
        setMessage('');
        try {
            const query = isRegister ? REGISTER_MUTATION : LOGIN_MUTATION;
            const data = await fetchGraphQL(query, { username: formUser, password: formPass });
            const result = isRegister ? data.register : data.login;

            if (result.success) {
                setIsLoggedIn(true);
                setCurrentUser(result.username);
                setShowAuthForm(false);
                setFormUser('');
                setFormPass('');
                if (onUserLoggedIn) onUserLoggedIn(); // Trigger sync
            } else {
                setMessage(result.message || 'Authentication failed');
            }
        } catch (err) {
            setMessage(err.message);
        } finally {
            setLoading(false);
        }
    };

    const handleLogout = async () => {
        try {
            await fetchGraphQL(LOGOUT_MUTATION);
            setIsLoggedIn(false);
            setCurrentUser('');
        } catch (err) {
            console.error("Logout failed", err);
        }
    };

    return (
        <div className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
            <div className="sidebar-header">
                <a href="help.html" style={{textDecoration: 'none', color: 'inherit'}}><h3>LangGraph</h3></a>
                <div className="sidebar-actions">
                    <button className="new-graph-btn" onClick={() => window.location.href = 'builder.html?isNew=true'}>
                        {collapsed ? '+' : '+ New'}
                    </button>
                    <button className="toggle-btn" onClick={() => setCollapsed(!collapsed)}>
                        {collapsed ? '»' : '«'}
                    </button>
                </div>
            </div>

            {realBalance && (
                <div className="balance-section" style={{
                    padding: '10px 15px',
                    background: realBalance.success ? 'linear-gradient(135deg, #fff3e0 0%, #ffe0b2 100%)' : 'linear-gradient(135deg, #ffebee 0%, #ffcdd2 100%)',
                    borderBottom: realBalance.success ? '1px solid #ffb74d' : '1px solid #ef9a9a',
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    fontSize: '12px', fontWeight: '600', color: realBalance.success ? '#e65100' : '#b71c1c'
                }}>
                    <span className="balance-label">{realBalance.success ? '💰 Balance:' : '⚠️ Error'}</span>
                    <span className="balance-value" style={{
                        background: '#fff', padding: '2px 8px', borderRadius: '12px',
                        border: realBalance.success ? '1px solid #ffb74d' : '1px solid #ef9a9a',
                        boxShadow: '0 1px 2px rgba(0,0,0,0.05)'
                    }}>
                        {realBalance.success ? `${realBalance.balance.toFixed(2)} ${realBalance.currency}` : 'Check Key'}
                    </span>
                </div>
            )}

            <div className="graph-list">
                {graphs.map(g => (
                    <div key={g.graphId} className={`graph-item ${g.graphId === selected ? 'active' : ''}`} onClick={() => onSelectGraph(g.graphId)}>
                        <div className="graph-icon-mini"></div>
                        <div className="graph-info">
                            <div className="name">{g.graphName}</div>
                            <div className="meta">{g.nodeCount} Nodes</div>
                        </div>
                        <div className="graph-actions" style={{ display: 'flex', gap: '5px', alignItems: 'center' }}>
                            <button className="icon-btn" title="Copy ID" onClick={(e) => { e.stopPropagation(); copyGraphId(g.graphId); }}>{copiedGraph === g.graphId ? '✅' : '📋'}</button>
                            <button className="edit-btn" onClick={(e) => { e.stopPropagation(); window.location.href = `builder.html?graphId=${g.graphId}&graphName=${g.graphName}`; }}>Edit</button>
                            <button className="delete-btn" onClick={(e) => handleDelete(e, g.graphId)}>✕</button>
                        </div>
                    </div>
                ))}
            </div>

            {/* 🔥 Compact Auth Footer */}
            <div className="sidebar-footer" style={{
                padding: '10px', borderTop: '1px solid #e0e0e0', background: '#f9fafb',
                fontSize: '12px', transition: 'all 0.2s ease'
            }}>
                {!isLoggedIn ? (
                    !showAuthForm ? (
                        <button
                            onClick={() => setShowAuthForm(true)}
                            style={{ width: '100%', padding: '8px', background: '#2196f3', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: '600', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}
                        >
                            🔒 Sign In to Save Chats
                        </button>
                    ) : (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                            <input
                                placeholder="Username" value={formUser} onChange={e => setFormUser(e.target.value)}
                                style={{ padding: '6px', border: '1px solid #ccc', borderRadius: '4px', fontSize: '12px' }}
                            />
                            <input
                                type="password" placeholder="Password" value={formPass} onChange={e => setFormPass(e.target.value)}
                                style={{ padding: '6px', border: '1px solid #ccc', borderRadius: '4px', fontSize: '12px' }}
                                onKeyDown={e => e.key === 'Enter' && handleAuth(false)}
                            />
                            {message && <div style={{ color: '#c62828', fontSize: '11px' }}>{message}</div>}
                            <div style={{ display: 'flex', gap: '4px' }}>
                                <button onClick={() => handleAuth(false)} disabled={loading} style={{ flex: 1, padding: '6px', background: '#2196f3', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '11px' }}>
                                    {loading ? '...' : 'Login'}
                                </button>
                                <button onClick={() => handleAuth(true)} disabled={loading} style={{ flex: 1, padding: '6px', background: '#4caf50', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '11px' }}>
                                    {loading ? '...' : 'Register'}
                                </button>
                                <button onClick={() => { setShowAuthForm(false); setMessage(''); }} style={{ padding: '6px 10px', background: '#e0e0e0', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>✕</button>
                            </div>
                        </div>
                    )
                ) : (
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <span style={{ fontWeight: '600', color: '#333', display: 'flex', alignItems: 'center', gap: '6px' }}>
                            👤 {currentUser}
                        </span>
                        <button onClick={handleLogout} style={{ padding: '4px 10px', background: '#ffebee', color: '#c62828', border: '1px solid #ef9a9a', borderRadius: '4px', cursor: 'pointer', fontSize: '11px', fontWeight: '600' }}>
                            Sign Out
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
