import React, { useEffect, useState } from 'react';
import { fetchLlmModels } from '../constants';

// Renders a <datalist> of model suggestions for the given provider, referenced by an
// <input list={id}>. Fetches lazily and caches (see fetchLlmModels); a failed or empty lookup
// leaves the list empty, so the input stays free-text and any custom model id still works.
// Pass the *effective* provider (override ?? graph/server default) rather than the raw field,
// so a node that "inherits" still gets the right suggestions.
export default function ModelDatalist({ id, provider }) {
    const [models, setModels] = useState([]);

    useEffect(() => {
        let alive = true;
        fetchLlmModels(provider).then(list => { if (alive) setModels(list); });
        return () => { alive = false; };
    }, [provider]);

    return (
        <datalist id={id}>
            {models.map(m => <option key={m.id} value={m.id} label={m.label} />)}
        </datalist>
    );
}
