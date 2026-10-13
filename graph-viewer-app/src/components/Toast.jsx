import React, { useCallback, useEffect, useRef, useState } from 'react';
import '../toast.css';

// Small non-blocking notification used instead of the blocking browser alert() popups.
export function Toast({ toast, onDismiss }) {
    useEffect(() => {
        if (!toast) return undefined;
        const timer = setTimeout(onDismiss, toast.duration || 3500);
        return () => clearTimeout(timer);
    }, [toast, onDismiss]);

    if (!toast) return null;
    return (
        <div className={`toast toast-${toast.kind || 'info'}`} role="status" onClick={onDismiss}>
            {toast.text}
        </div>
    );
}

// Returns the current toast plus helpers to show/hide it.
export const useToast = () => {
    const [toast, setToast] = useState(null);
    const timer = useRef(null);

    const showToast = useCallback((text, kind = 'info', duration) => {
        clearTimeout(timer.current);
        setToast({ text: String(text), kind, duration });
    }, []);

    const dismissToast = useCallback(() => setToast(null), []);

    useEffect(() => () => clearTimeout(timer.current), []);

    return { toast, showToast, dismissToast };
};
