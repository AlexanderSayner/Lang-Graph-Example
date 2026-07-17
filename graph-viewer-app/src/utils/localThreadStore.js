const STORAGE_KEY = 'langgraph_local_threads';

// Get all thread metadata the user has interacted with locally
export const getLocalThreads = () => {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        return stored ? JSON.parse(stored) : [];
    } catch (e) {
        console.error("Failed to read local threads", e);
        return [];
    }
};

// Add a thread to the local list (called when user sends first message)
export const addLocalThread = (threadId, graphId, title = "New Chat") => {
    try {
        const threads = getLocalThreads();
        // Use .some() for cleaner boolean check
        if (!threads.some(t => t.threadId === threadId)) {
            threads.push({ threadId, graphId, title, synced: false });
            localStorage.setItem(STORAGE_KEY, JSON.stringify(threads));
        }
    } catch (e) {
        console.error("Failed to save local thread", e);
    }
};

// Mark specific threads as synced after successful backend claim
export const markThreadsAsSynced = (syncedThreadIds) => {
    try {
        const threads = getLocalThreads();
        const updated = threads.map(t => 
            syncedThreadIds.includes(t.threadId) ? { ...t, synced: true } : t
        );
        localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
    } catch (e) {
        console.error("Failed to update sync status", e);
    }
};

// Get only the threads that haven't been synced to the cloud yet
export const getUnsyncedThreads = () => {
    return getLocalThreads().filter(t => !t.synced);
};
