export default function StateModal({ modalData, modalViewMode, setModalData, setModalViewMode, handleRewind, getNodeDisplayName, formatTimestamp }) {
    if (!modalData) return null;

    // Helper: Render detailed diff view in modal
    const renderDiffDetails = (diff, fullJson) => {
        // Fallback to full JSON if no diff or in full view mode
        const hasNoChanges =
            Object.keys(diff.added || {}).length === 0 &&
            (diff.removed || []).length === 0 &&
            Object.keys(diff.modified || {}).length === 0;

        // Fallback to full JSON if no diff or in full view mode
        if (!diff || hasNoChanges) {
            return <pre className="json-viewer">{JSON.stringify(fullJson, null, 2)}</pre>;
        }

        return (
            <div style={{ fontFamily: 'Consolas, Monaco, monospace', fontSize: '11px', lineHeight: '1.4' }}>
                {/* Added fields */}
                {diff.added && Object.keys(diff.added).length > 0 && (
                    <div style={{ marginBottom: '12px' }}>
                        <strong style={{ color: '#2e7d32', display: 'block', marginBottom: '4px' }}>
                            ➕ Added
                        </strong>
                        <pre style={{
                            background: '#e8f5e9',
                            padding: '8px',
                            borderRadius: '4px',
                            margin: 0,
                            whiteSpace: 'pre-wrap',
                            overflow: 'auto',
                            maxHeight: '150px'
                        }}>
                    {JSON.stringify(diff.added, null, 2)}
                </pre>
                    </div>
                )}

                {/* Removed fields */}
                {diff.removed && diff.removed.length > 0 && (
                    <div style={{ marginBottom: '12px' }}>
                        <strong style={{ color: '#c62828', display: 'block', marginBottom: '4px' }}>
                            ➖ Removed
                        </strong>
                        <ul style={{ margin: '4px 0 0 20px', padding: 0 }}>
                            {diff.removed.map((path, i) => (
                                <li key={i} style={{ color: '#c62828', marginBottom: '2px' }}>
                                    {path}
                                </li>
                            ))}
                        </ul>
                    </div>
                )}

                {/* Modified fields */}
                {diff.modified && Object.keys(diff.modified).length > 0 && (
                    <div style={{ marginBottom: '12px' }}>
                        <strong style={{ color: '#1565c0', display: 'block', marginBottom: '4px' }}>
                            ✏️ Modified
                        </strong>
                        {Object.entries(diff.modified).map(([path, values]) => (
                            <div key={path} style={{
                                background: '#e3f2fd',
                                padding: '6px 8px',
                                margin: '4px 0',
                                borderRadius: '3px',
                                borderLeft: '3px solid #2196f3'
                            }}>
                                <div style={{ fontWeight: '600', marginBottom: '4px' }}>{path}</div>
                                <div style={{ fontSize: '10px' }}>
                                    <span style={{ color: '#999' }}>Before: </span>
                                    <code style={{ background: '#ffebee', padding: '2px 4px', borderRadius: '2px' }}>
                                        {JSON.stringify(values?.old)}
                                    </code>
                                </div>
                                <div style={{ fontSize: '10px', marginTop: '2px' }}>
                                    <span style={{ color: '#999' }}>After: </span>
                                    <code style={{ background: '#e8f5e9', padding: '2px 4px', borderRadius: '2px' }}>
                                        {JSON.stringify(values?.new)}
                                    </code>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        );
    };

    return (
        <div className="modal-overlay" onClick={() => {
            setModalData(null);
            setModalViewMode('diff'); // Reset on close
        }}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <div className="modal-title" style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
                        <span>State: <strong>{getNodeDisplayName(modalData.nodeId)}</strong></span>
                        <span style={{fontWeight: 'normal', fontSize: '12px', color: '#666'}}>{formatTimestamp(modalData.timestamp)}</span>

                        {/* ADDED: Token badge in modal header */}
                        {modalData.tokensUsed != null && (
                            <span style={{
                                background: '#e8f5e9',
                                color: '#2e7d32',
                                padding: '2px 8px',
                                borderRadius: '12px',
                                fontSize: '11px',
                                fontWeight: '600',
                                border: '1px solid #a5d6a7'
                            }}>
                                🪙 {modalData.tokensUsed} tokens (Total: {modalData.totalTokens})
                            </span>
                        )}
                    </div>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        {/* Toggle buttons - only show if diff exists */}
                        {modalData.diff && (
                            <>
                                <button
                                    onClick={() => setModalViewMode('diff')}
                                    style={{
                                        padding: '4px 10px',
                                        fontSize: '11px',
                                        border: modalViewMode === 'diff' ? '2px solid #2196f3' : '1px solid #ccc',
                                        background: modalViewMode === 'diff' ? '#e3f2fd' : '#fff',
                                        borderRadius: '4px',
                                        cursor: 'pointer',
                                        color: modalViewMode === 'diff' ? '#1565c0' : '#666',
                                        fontWeight: modalViewMode === 'diff' ? '600' : 'normal'
                                    }}
                                >
                                    Diff
                                </button>
                                <button
                                    onClick={() => setModalViewMode('full')}
                                    style={{
                                        padding: '4px 10px',
                                        fontSize: '11px',
                                        border: modalViewMode === 'full' ? '2px solid #2196f3' : '1px solid #ccc',
                                        background: modalViewMode === 'full' ? '#e3f2fd' : '#fff',
                                        borderRadius: '4px',
                                        cursor: 'pointer',
                                        color: modalViewMode === 'full' ? '#1565c0' : '#666',
                                        fontWeight: modalViewMode === 'full' ? '600' : 'normal'
                                    }}
                                >
                                    Full JSON
                                </button>
                            </>
                        )}
                        <button className="modal-close" onClick={() => {
                            setModalData(null);
                            setModalViewMode('diff');
                        }}>&times;</button>
                    </div>
                </div>
                <div className="modal-body">
                    {modalViewMode === 'diff' && modalData.diff
                        ? renderDiffDetails(modalData.diff, modalData.json)
                        : <pre className="json-viewer">{JSON.stringify(modalData.json, null, 2)}</pre>
                    }
                </div>
                <div style={{padding: '10px', borderTop: '1px solid #eee', textAlign: 'right', display: 'flex', justifyContent: 'flex-end', gap: '8px'}}>
                    <button
                        className="btn-rewind"
                        onClick={() => handleRewind(modalData.json, modalData.nodeId)}
                        style={{ fontSize: '12px', padding: '6px 12px' }}
                    >
                        ↩️ Replay from here
                    </button>
                </div>
            </div>
        </div>
    );
}
