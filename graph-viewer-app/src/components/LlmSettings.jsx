import React, { useEffect, useState } from 'react';
import {
    fetchGraphQL, LLM_CREDENTIALS_QUERY, SAVE_LLM_CREDENTIAL_MUTATION,
    DELETE_LLM_CREDENTIAL_MUTATION, LLM_CREDENTIAL_PROVIDERS,
} from '../constants';

const fieldStyle = {
    padding: '6px 8px', border: '1px solid #ccc', borderRadius: '6px', fontSize: '12px',
};

/**
 * Personal LLM API key manager.
 *
 * The key typed here goes browser -> server once and is encrypted there (AES-256-GCM,
 * bound to user+provider). Only a masked hint ever comes back, so this component can never
 * show a key it was not given - and reopening it does not need one.
 */
export default function LlmSettings({ open, onClose, onChanged }) {
    const [rows, setRows] = useState([]);
    const [provider, setProvider] = useState('openai');
    const [apiKey, setApiKey] = useState('');
    const [ycFolderId, setYcFolderId] = useState('');
    const [message, setMessage] = useState(null);   // { kind: 'ok' | 'error', text }
    const [busy, setBusy] = useState(false);

    const load = async () => {
        try {
            const data = await fetchGraphQL(LLM_CREDENTIALS_QUERY);
            setRows(data?.llmCredentials || []);
        } catch (err) {
            setRows([]);
            setMessage({ kind: 'error', text: err.message });
        }
    };

    useEffect(() => {
        if (open) {
            setMessage(null);
            load();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);

    if (!open) return null;

    const handleSave = async () => {
        if (!apiKey.trim()) {
            setMessage({ kind: 'error', text: 'Enter an API key first.' });
            return;
        }
        setBusy(true);
        setMessage(null);
        try {
            await fetchGraphQL(SAVE_LLM_CREDENTIAL_MUTATION, {
                input: {
                    provider,
                    apiKey: apiKey.trim(),
                    // The folder id is a non-secret identifier, but it only belongs to YandexGPT.
                    ycFolderId: provider === 'yandex' ? (ycFolderId || '') : '',
                },
            });
            // Never keep plaintext in component state any longer than it takes to send it.
            setApiKey('');
            setMessage({ kind: 'ok', text: `Saved for ${provider}. It is used by your very next run.` });
            await load();
            onChanged?.();
        } catch (err) {
            setMessage({ kind: 'error', text: err.message });
        } finally {
            setBusy(false);
        }
    };

    const handleDelete = async (target) => {
        setBusy(true);
        setMessage(null);
        try {
            await fetchGraphQL(DELETE_LLM_CREDENTIAL_MUTATION, { provider: target });
            setMessage({ kind: 'ok', text: `Removed ${target}. That provider now uses the shared service key.` });
            await load();
            onChanged?.();
        } catch (err) {
            setMessage({ kind: 'error', text: err.message });
        } finally {
            setBusy(false);
        }
    };

    return (
        <div
            style={{
                position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 1000,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
            onClick={onClose}
        >
            <div
                style={{
                    background: '#fff', borderRadius: '10px', padding: '18px 20px',
                    width: 'min(560px, 92vw)', maxHeight: '85vh', overflowY: 'auto',
                    boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
                }}
                onClick={(e) => e.stopPropagation()}
            >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <h3 style={{ margin: '0 0 4px' }}>🔑 AI provider keys</h3>
                    <button onClick={onClose}
                        style={{ border: 'none', background: 'none', fontSize: '16px', cursor: 'pointer' }}>
                        ✕
                    </button>
                </div>
                <p style={{ margin: '0 0 14px', fontSize: '12px', color: '#666', lineHeight: '1.4' }}>
                    Keys saved here are used for <b>your</b> graph runs and are billed to your own
                    provider account. Stored encrypted and never shown again. A provider with no key
                    falls back to the shared service key, and the run is marked as such.
                </p>

                {rows.length > 0 && (
                    <table style={{ width: '100%', fontSize: '12px', borderCollapse: 'collapse', marginBottom: '14px' }}>
                        <thead>
                            <tr style={{ textAlign: 'left', color: '#888', borderBottom: '1px solid #eee' }}>
                                <th style={{ padding: '6px 0' }}>Provider</th>
                                <th style={{ padding: '6px 0' }}>Key</th>
                                <th style={{ padding: '6px 0' }}>Folder</th>
                                <th />
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map(row => (
                                <tr key={row.provider} style={{ borderBottom: '1px solid #f3f3f3' }}>
                                    <td style={{ padding: '6px 0' }}>{row.provider}</td>
                                    <td style={{ padding: '6px 0', fontFamily: 'monospace' }}>{row.maskedKey}</td>
                                    <td style={{ padding: '6px 0', color: '#666' }}>{row.ycFolderId || '—'}</td>
                                    <td style={{ padding: '6px 0', textAlign: 'right' }}>
                                        <button
                                            onClick={() => handleDelete(row.provider)}
                                            disabled={busy}
                                            style={{
                                                padding: '3px 8px', fontSize: '11px', cursor: 'pointer',
                                                border: '1px solid #ef9a9a', background: '#ffebee',
                                                color: '#c62828', borderRadius: '4px',
                                            }}
                                        >
                                            Remove
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}

                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    <select value={provider} onChange={e => setProvider(e.target.value)} style={fieldStyle}>
                        {LLM_CREDENTIAL_PROVIDERS.map(option => (
                            <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                    </select>
                    <input
                        type="password"
                        autoComplete="off"
                        placeholder={`API key for ${provider}`}
                        value={apiKey}
                        onChange={e => setApiKey(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && handleSave()}
                        style={fieldStyle}
                    />
                    {provider === 'yandex' && (
                        <input
                            placeholder="Yandex Cloud folder id (YC_FOLDER_ID)"
                            value={ycFolderId}
                            onChange={e => setYcFolderId(e.target.value)}
                            style={fieldStyle}
                        />
                    )}
                    <button
                        onClick={handleSave}
                        disabled={busy || !apiKey.trim()}
                        style={{
                            padding: '8px', borderRadius: '6px', border: 'none', cursor: 'pointer',
                            background: busy ? '#90caf9' : '#2196f3', color: '#fff', fontWeight: '600',
                        }}
                    >
                        {busy ? 'Saving…' : 'Save key'}
                    </button>
                </div>

                {message && (
                    <div style={{
                        marginTop: '10px', padding: '8px 10px', borderRadius: '6px', fontSize: '12px',
                        background: message.kind === 'error' ? '#ffebee' : '#e8f5e9',
                        color: message.kind === 'error' ? '#c62828' : '#2e7d32',
                    }}>
                        {message.text}
                    </div>
                )}
            </div>
        </div>
    );
}
