// Helper: Simple Markdown-like formatter for AI responses
const formatMessage = (text) => {
    if (!text) return "";
    // 1. Escape HTML to prevent XSS
    let html = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    // 2. Code blocks (``` ... ```)
    html = html.replace(/```([\s\S]*?)```/g, '<pre style="background:#f4f4f4; padding:10px; border-radius:6px; overflow-x:auto; font-size:12px; margin: 8px 0; border: 1px solid #e0e0e0;"><code>$1</code></pre>');
    // 3. Inline code (` ... `)
    html = html.replace(/`([^`]+)`/g, '<code style="background:#f4f4f4; padding:2px 5px; border-radius:4px; font-size:12px; font-family: monospace;">$1</code>');
    // 4. Bold (** ... **)
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // 5. Line breaks
    html = html.replace(/\n/g, '<br/>');
    return html;
};

// Quick action suggestions for the empty state
const copilotSuggestions = [
    "Explain how this graph works",
    "Suggest edge conditions for my router",
    "How can I improve the prompt of the selected node?",
    "Why did the last execution fail?"
];

function CopilotTab({
    copilotMessages, copilotInput, setCopilotInput, copilotLoading,
    handleCopilotSend, chatEndRef, onClearChat
}) {
    return (
        <>
            <div className="chat-messages" style={{ paddingBottom: '10px' }}>
                {/* Empty State with Quick Actions */}
                {copilotMessages.length === 0 && (
                    <div style={{textAlign: 'center', color: '#666', marginTop: '40px', fontSize: '14px', padding: '0 20px'}}>
                        <div style={{fontSize: '32px', marginBottom: '10px'}}>🤖</div>
                        <p style={{fontWeight: '600', color: '#333'}}>I'm your Graph Copilot!</p>
                        <p style={{fontSize: '13px', color: '#888', marginBottom: '20px'}}>
                            I can see your current canvas structure and execution history.
                        </p>
                        <div style={{display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center'}}>
                            {copilotSuggestions.map((action, i) => (
                                <button
                                    key={i}
                                    onClick={() => setCopilotInput(action)}
                                    style={{
                                        background: '#f0f4f8', border: '1px solid #d1d9e6', borderRadius: '16px',
                                        padding: '8px 14px', fontSize: '12px', color: '#4a5568', cursor: 'pointer',
                                        transition: 'all 0.2s', fontWeight: '500'
                                    }}
                                    onMouseOver={(e) => { e.target.style.background = '#e2e8f0'; e.target.style.borderColor = '#cbd5e1'; }}
                                    onMouseOut={(e) => { e.target.style.background = '#f0f4f8'; e.target.style.borderColor = '#d1d9e6'; }}
                                >
                                    {action}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {/* Message List */}
                {copilotMessages.map((m, i) => (
                    <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: m.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: '12px' }}>
                        <div style={{
                            maxWidth: '85%', padding: '10px 14px', borderRadius: '12px', fontSize: '13px', lineHeight: '1.5',
                            background: m.role === 'user' ? '#2196f3' : '#ffffff', color: m.role === 'user' ? '#ffffff' : '#333333',
                            border: m.role === 'user' ? 'none' : '1px solid #e0e0e0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)'
                        }}>
                            {m.role === 'error' ? (
                                <span style={{color: '#c62828', fontWeight: '500'}}>⚠️ {m.text}</span>
                            ) : (
                                <div dangerouslySetInnerHTML={{ __html: formatMessage(m.text) }} />
                            )}
                        </div>
                    </div>
                ))}

                {/* Typing Indicator */}
                {copilotLoading && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '4px', padding: '10px', color: '#666', fontSize: '12px' }}>
                        <style>{`@keyframes pulse { 0%, 100% { opacity: 0.4; } 50% { opacity: 1; } }`}</style>
                        <span style={{ animation: 'pulse 1.5s infinite' }}>●</span>
                        <span style={{ animation: 'pulse 1.5s infinite 0.2s' }}>●</span>
                        <span style={{ animation: 'pulse 1.5s infinite 0.4s' }}>●</span>
                        <span style={{marginLeft: '8px'}}>Copilot is analyzing your graph...</span>
                    </div>
                )}
                <div ref={chatEndRef} />
            </div>

            {/* Input Area */}
            <div className="chat-input-area" style={{ borderTop: '1px solid #eee', padding: '12px' }}>
                <input
                    className="chat-input"
                    placeholder="Ask about your graph, prompts, or errors..."
                    value={copilotInput}
                    onChange={e => setCopilotInput(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleCopilotSend()}
                    disabled={copilotLoading}
                />
                <button
                    className="send-btn"
                    onClick={handleCopilotSend}
                    disabled={copilotLoading || !copilotInput.trim()}
                    style={{ opacity: (copilotLoading || !copilotInput.trim()) ? 0.6 : 1 }}
                >
                    {copilotLoading ? '...' : 'Send'}
                </button>
            </div>
        </>
    );
}
