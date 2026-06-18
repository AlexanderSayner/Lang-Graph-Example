// Helper: Render concise diff summary for history list
const renderDiffSummary = (diff) => {
    if (!diff?.summary?.length) return null;

    return (
        <div style={{ fontSize: '11px', color: '#666', marginTop: '4px', lineHeight: '1.3' }}>
            {diff.summary.slice(0, 2).map((item, i) => {
                let icon = '';
                let color = '#666';

                if (item.includes('added')) {
                    icon = '➕ ';
                    color = '#2e7d32';
                } else if (item.includes('removed')) {
                    icon = '➖ ';
                    color = '#c62828';
                } else if (item.includes('modified')) {
                    icon = '✏️ ';
                    color = '#1565c0';
                }

                return (
                    <div key={i} style={{ color }}>
                        {icon}{item}
                    </div>
                );
            })}
            {diff.summary.length > 2 && (
                <div style={{ fontStyle: 'italic', color: '#999' }}>
                    +{diff.summary.length - 2} more
                </div>
            )}
        </div>
    );
};

export default function ChatPanel({
    activeTab, setActiveTab, selected, threadId, copiedThread, copiedGraph,
    copyThreadId, copyGraphId, resetThread, panelHeight, panelRef, startResizing,
    messages, graphStatus, loading, input, setInput, handleExecute, chatEndRef,
    historyItems, setModalData, getNodeDisplayName, handleRewind,
    copilotMessages, copilotInput, setCopilotInput, copilotLoading, handleCopilotSend,
    formatTimestamp
}) {
    return (
        <div className="chat-panel" style={{ height: `${panelHeight}px` }} ref={panelRef}>

            {/* The Resizer Handle */}
            <div className="resizer" onMouseDown={startResizing}></div>

            {/* Tabs Header */}
            <div className="chat-tabs">
                <div className={`chat-tab ${activeTab === 'chat' ? 'active' : ''}`} onClick={() => setActiveTab('chat')}>Chat</div>
                {selected && (
                        <div className={`chat-tab ${activeTab === 'copilot' ? 'active' : ''}`} onClick={() => setActiveTab('copilot')}>✨ Copilot</div>
                )}
                <div className={`chat-tab ${activeTab === 'history' ? 'active' : ''}`} onClick={() => setActiveTab('history')}>History</div>
                <div style={{flexGrow: 1}}></div>
                <div style={{padding: '10px 15px', fontSize: '11px', color: '#999', display: 'flex', alignItems: 'center'}}>
                    {selected && ( <><span title={selected}>Graph: ...{selected.slice(-6)}</span><span className="copy-btn-icon" onClick={() => copyGraphId(selected)}>{copiedGraph === selected ? '✅' : '📋'}</span><span style={{margin: '0 8px', color: '#ddd'}}>|</span></> )}
                    <span title={threadId}>Thread: ...{threadId.slice(-6)}</span>
                    <span className="copy-btn-icon" onClick={copyThreadId}>{copiedThread ? '✅' : '📋'}</span>
                    <button onClick={resetThread} style={{border:'none', background:'none', cursor:'pointer', color:'#2196f3', marginLeft: '10px', fontSize: '11px'}}>New</button>
                </div>
            </div>

            {/* Content Area */}
            {activeTab === 'chat' ? (
                <>
                    <div className="chat-messages">
                        {messages.map((m, i) => (
                            <div key={i} className={`msg msg-${m.type}`}>
                                {m.text}
                                {/* ADDED: Show token spend for bot messages */}
                                {m.type === 'bot' && m.tokens > 0 && (
                                    <div style={{ fontSize: '10px', color: '#888', marginTop: '4px', fontStyle: 'italic' }}>
                                        🪙 Tokens used: {m.tokens}
                                    </div>
                                )}
                            </div>
                        ))}
                        {graphStatus === 'finished' && (
                            <div style={{
                                padding: '8px 12px',
                                background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                                color: 'white',
                                textAlign: 'center',
                                fontSize: '12px',
                                fontWeight: '500',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                gap: '6px'
                            }}>
                                <span>✨</span>
                                <span>Conversation completed</span>
                                <button
                                    onClick={resetThread}
                                    style={{
                                        background: 'rgba(255,255,255,0.2)',
                                        border: '1px solid rgba(255,255,255,0.4)',
                                        color: 'white',
                                        padding: '4px 10px',
                                        borderRadius: '12px',
                                        fontSize: '11px',
                                        cursor: 'pointer',
                                        marginLeft: '8px',
                                        transition: 'background 0.2s'
                                    }}
                                    onMouseOver={(e) => e.target.style.background = 'rgba(255,255,255,0.3)'}
                                    onMouseOut={(e) => e.target.style.background = 'rgba(255,255,255,0.2)'}
                                >
                                    Start new thread →
                                </button>
                            </div>
                        )}
                        {loading && <div className="msg msg-bot">Thinking...</div>}
                        <div ref={chatEndRef} />
                    </div>
                    <div className="chat-input-area">
                        <input className="chat-input" placeholder={ graphStatus === 'finished' ? "💬 Graph finished — send a message to continue, or click 'New' for a fresh thread" : "Send message..." } value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleExecute()} disabled={loading} />
                        <button className="send-btn" onClick={handleExecute} disabled={loading}>Send</button>
                    </div>
                </>
            ) : activeTab === 'copilot' ? (
                <CopilotTab
                    copilotMessages={copilotMessages}
                    copilotInput={copilotInput}
                    setCopilotInput={setCopilotInput}
                    copilotLoading={copilotLoading}
                    handleCopilotSend={handleCopilotSend}
                    chatEndRef={chatEndRef}
                    onClearChat={() => setCopilotMessages([])}
                />
            ) : (
                <div className="history-list">
                    {historyItems.length === 0 ? (
                        <div style={{textAlign: 'center', color: '#999', marginTop: '20px'}}>
                            No execution history for this thread.
                        </div>
                    ) : (
                        historyItems.map((h, idx) => (
                            <div
                                key={`${h.nodeId}-${idx}`}  // Unique key for React
                                className="history-item"
                                onClick={() => setModalData({
                                    nodeId: h.nodeId,
                                    timestamp: h.timestamp,
                                    json: h.stateJson,
                                    diff: h.diff,
                                    tokensUsed: h.tokensUsed,
                                    totalTokens: h.totalTokens
                                })}
                                style={{cursor: 'pointer'}}
                            >
                                <div style={{flex: 1, minWidth: 0}}>
                                    <div className="history-node-name" style={{
                                        fontWeight: '600',
                                        fontSize: '13px',
                                        color: '#333',
                                        marginBottom: '2px'
                                    }}>
                                        {getNodeDisplayName(h.nodeId)}
                                    </div>
                                    <div className="history-timestamp" style={{
                                        fontSize: '10px',
                                        color: '#999',
                                        display: 'flex',
                                        gap: '8px',
                                        alignItems: 'center',
                                        flexWrap: 'wrap'
                                    }}>
                                        <span>{formatTimestamp(h.timestamp)}</span>
                                        {/* ADDED: Token badge for history items */}
                                        {h.tokensUsed != null && (
                                            <span style={{
                                                background: '#f3e5f5',
                                                color: '#6a1b9a',
                                                padding: '1px 6px',
                                                borderRadius: '10px',
                                                fontSize: '9px',
                                                fontWeight: 'bold',
                                                border: '1px solid #ce93d8'
                                            }}>
                                                🪙 {h.tokensUsed} / {h.totalTokens} total
                                            </span>
                                        )}
                                    </div>
                                    {/* Show diff summary */}
                                    {renderDiffSummary(h.diff)}
                                </div>
                                <div className="history-actions" style={{
                                    display: 'flex',
                                    gap: '8px',
                                    alignItems: 'center',
                                    flexShrink: 0
                                }}>
                                    <button
                                        className="btn-rewind"
                                        style={{fontSize: '10px', padding: '4px 8px'}}
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            handleRewind(h.stateJson, h.nodeId);
                                        }}
                                    >
                                        Replay
                                    </button>
                                    <div style={{fontSize: '10px', color: '#2196f3'}}>
                                        {h.diff?.summary?.length > 0 ? 'View Diff' : 'View JSON'}
                                    </div>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            )
            }
        </div>
    )
}
