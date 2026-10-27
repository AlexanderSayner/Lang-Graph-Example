import React from 'react';
import CopilotTab from '../copilot';

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
    messages, graphStatus, loading, input, setInput, handleExecute, handleCancel, chatEndRef,
    historyItems, setModalData, getNodeDisplayName, handleRewind,
    copilotMessages, copilotInput, setCopilotInput, copilotLoading, handleCopilotSend,
    copilotLLM, setCopilotLLM,
    formatTimestamp,
    // Providers this graph asks for that the signed-in account holds no key for (empty for
    // anonymous visitors, who cannot have keys at all).
    missingKeyProviders = [],
    // The deployment refuses a run that has no personal key (policy=PERSONAL_ONLY) instead
    // of billing the shared service key - so the hint below must promise a refusal, not a
    // shared-key run that will never happen.
    serviceKeyForbidden = false,
    // The engine's kill switch (LLM_SERVICE_KEY_ENABLED): false = no path may read the shared
    // key, so a run without a matching personal key fails even when the policy says FALLBACK.
    serviceKeyEnabled = true,
    // The engine's LLM_PROVIDER: what a graph that selects no provider actually runs on.
    // Empty when the engine could not be reached - then no provider is named, only described.
    defaultProvider = '',
    // The account's own providers (for the "server default" notes below).
    savedKeyProviders = [],
    // True when the LOADED graph names no provider anywhere (config and every node inherit):
    // the engine's server default then decides, and nothing here can tell whether it matches
    // a saved key.
    graphUsesServerDefault = false,
    onOpenLlmSettings,
    // --- Attachments ---
    // The document this run will carry, once the upload has finished. Null means "nothing
    // attached"; a run with no attachment behaves exactly as before.
    attachment = null,
    // True while the chosen file is uploading, so the chip can say so instead of a stale name.
    attachmentUploading = false,
    // Set when the upload was refused (too large, wrong type): shown, not swallowed.
    attachmentError = null,
    // Chosen a file / pressed the remove button. Both live in app.jsx, which owns the request.
    onPickAttachment = () => {},
    onClearAttachment = () => {},
}) {
    // Either half being set means the shared key will NOT pay for this run.
    const sharedKeyBlocked = serviceKeyForbidden || !serviceKeyEnabled;
    const showSharedKeyHint = activeTab === 'chat' && !loading && missingKeyProviders.length > 0;
    // The trap the "server default" notes exist for: an account saves a key, the graph
    // declares no provider, so `missingKeyProviders` is empty, the "no personal key for X"
    // hint can never fire - and the run still bills the shared service key because the
    // default is a provider the account has no key for. Say so BEFORE the run instead of
    // leaving only the after-the-fact billing note to explain it.
    const defaultMatchesSavedKey = Boolean(defaultProvider)
        && savedKeyProviders.includes(defaultProvider);
    const showServerDefaultHint = activeTab === 'chat' && !loading
        && graphUsesServerDefault
        && savedKeyProviders.length > 0
        && missingKeyProviders.length === 0
        && !defaultMatchesSavedKey;
    // The pleasant mirror of the same case: nothing to warn about, the key WILL be spent.
    const showServerDefaultMatch = activeTab === 'chat' && !loading
        && graphUsesServerDefault
        && defaultMatchesSavedKey
        && missingKeyProviders.length === 0;
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
                                {/* Who paid for that run. Only shown when the shared service key
                                    was involved - a fully personal run needs no footnote - so a
                                    subsidised answer is never invisible. */}
                                {m.type === 'bot' && (m.llmBilling === 'service' || m.llmBilling === 'mixed') && (
                                    <div style={{ fontSize: '10px', color: '#e65100', marginTop: '4px' }}>
                                        ⚠️ {m.llmBilling === 'mixed'
                                            ? 'Partly served by the shared service key (you have no key for every provider this graph used).'
                                            : 'Served by the shared service key — no personal key for this provider.'}
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
                        {loading && <div className="msg msg-bot">Thinking… <span style={{ color: '#999', fontStyle: 'italic', fontSize: '11px' }}>press Cancel to stop</span></div>}
                        <div ref={chatEndRef} />
                    </div>
                    {/* Pre-run warning about providers the account holds no key for. Under
                        FALLBACK/REQUIRE nothing stops the run, so it says the shared service
                        key will pay; when the key cannot pay at all (PERSONAL_ONLY policy or
                        the engine's LLM_SERVICE_KEY_ENABLED=false) the run is refused instead,
                        and "will run on the shared key" would promise something that cannot
                        happen. Either way it opens the key manager, the one action that fixes
                        both. */}
                    {showSharedKeyHint && (
                        <div style={{
                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                            gap: '8px', padding: '6px 12px', fontSize: '11px',
                            background: sharedKeyBlocked ? '#ffebee' : '#fff8e1',
                            borderTop: '1px solid', borderColor: sharedKeyBlocked ? '#ffcdd2' : '#ffe0b2',
                            color: sharedKeyBlocked ? '#b71c1c' : '#e65100',
                        }}>
                            <span>
                                {sharedKeyBlocked
                                    ? <>🚫 No personal key for <b>{missingKeyProviders.join(', ')}</b> —
                                        this run will be refused (only personal keys can be spent here).</>
                                    : <>⚠️ No personal key for <b>{missingKeyProviders.join(', ')}</b> —
                                        these nodes will run on the shared service key.</>}
                            </span>
                            <button
                                onClick={onOpenLlmSettings}
                                style={{
                                    padding: '4px 9px', fontSize: '11px', cursor: 'pointer', whiteSpace: 'nowrap',
                                    border: '1px solid #ffb74d', background: '#ffe0b2', color: '#e65100',
                                    borderRadius: '4px', fontWeight: '600',
                                }}
                            >
                                Add keys
                            </button>
                        </div>
                    )}
                    {/* The graph declares no provider at all, so the ENGINE's server default
                        decides - amber/red above cannot fire (a graph that names no provider
                        has no missing provider to name). Two outcomes, both stated before the
                        run: the default is named when the engine reported it, and the account's
                        key either misses it (shared key pays / refusal) or covers it. */}
                    {showServerDefaultHint && (
                        <div style={{
                            display: 'flex', alignItems: 'center', gap: '8px',
                            padding: '6px 12px', fontSize: '11px',
                            background: '#e3f2fd', borderTop: '1px solid #bbdefb', color: '#0d47a1',
                        }}>
                            <span>
                                {defaultProvider
                                    ? <>ℹ️ This graph runs on the server default provider <b>{defaultProvider}</b>.
                                        Your saved keys (<b>{savedKeyProviders.join(', ')}</b>) don't cover it —
                                        {' '}{sharedKeyBlocked ? 'the run will be refused' : 'the shared service key pays'}.</>
                                    : <>ℹ️ This graph selects no LLM provider — it runs on the server
                                        default. Your saved keys (<b>{savedKeyProviders.join(', ')}</b>)
                                        are used only if that default matches; otherwise {sharedKeyBlocked
                                            ? 'the run is refused'
                                            : 'the shared service key pays'}.</>}
                                {' '}Pick a provider in the builder (graph settings or the node) to be
                                sure — the note under the answer says who actually paid.
                            </span>
                        </div>
                    )}
                    {showServerDefaultMatch && (
                        <div style={{
                            display: 'flex', alignItems: 'center', gap: '8px',
                            padding: '6px 12px', fontSize: '11px',
                            background: '#e8f5e9', borderTop: '1px solid #c8e6c9', color: '#1b5e20',
                        }}>
                            <span>
                                ✅ This graph runs on the server default provider <b>{defaultProvider}</b>,
                                and you hold a personal key for it — expect <b>llmBilling: user</b> below.
                            </span>
                        </div>
                    )}
                    {/* An attached document rides with the NEXT run: the chip is the whole
                        affordance, so the file picker is a hidden input the button triggers. */}
                    {(attachment || attachmentUploading || attachmentError) && (
                        <div className={`attachment-row ${attachmentError ? 'attachment-row--error' : ''}`}>
                            <span className="attachment-chip" title={attachmentError || (attachment && attachment.filename)}>
                                {attachmentUploading ? '⏳ Uploading…'
                                    : attachmentError ? `⚠ ${attachmentError}`
                                    : `📎 ${attachment.filename}`}
                            </span>
                            <button
                                type="button"
                                className="attachment-remove"
                                onClick={onClearAttachment}
                                disabled={loading}
                                title="Remove the attachment"
                            >×</button>
                        </div>
                    )}
                    <div className="chat-input-area">
                        <input
                            type="file"
                            className="attachment-file-input"
                            accept=".xlsx,.docx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                            onChange={onPickAttachment}
                            disabled={loading}
                        />
                        <input className="chat-input" placeholder={ graphStatus === 'finished' ? "💬 Graph finished — send a message to continue, or click 'New' for a fresh thread" : "Send message..." } value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleExecute()} disabled={loading} />
                        {loading ? (
                            /* While a run is in flight the Send button becomes a Cancel button, so
                               a runaway execution can be stopped immediately (mid-node, mid-LLM). */
                            <button className="cancel-btn" onClick={handleCancel}>Cancel</button>
                        ) : (
                            <button className="send-btn" onClick={handleExecute} disabled={!input.trim() || !selected}>Send</button>
                        )}
                    </div>
                </>
            ) : activeTab === 'copilot' ? (
                <CopilotTab
                    copilotMessages={copilotMessages}
                    copilotInput={copilotInput}
                    setCopilotInput={setCopilotInput}
                    copilotLoading={copilotLoading}
                    handleCopilotSend={handleCopilotSend}
                    copilotLLM={copilotLLM}
                    setCopilotLLM={setCopilotLLM}
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
