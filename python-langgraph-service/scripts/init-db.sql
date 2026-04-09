-- Initialize database schema for LangGraph Service

-- Create graphs table
CREATE TABLE IF NOT EXISTS graphs (
    graph_id TEXT PRIMARY KEY,
    graph_name TEXT NOT NULL,
    definition JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    status TEXT DEFAULT 'active'
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_graphs_status ON graphs(status);
CREATE INDEX IF NOT EXISTS idx_graphs_created_at ON graphs(created_at DESC);

-- Create graph_states table
CREATE TABLE IF NOT EXISTS graph_states (
    id SERIAL PRIMARY KEY,
    graph_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    state JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(graph_id, thread_id)
);

-- Create indexes for faster lookups
CREATE INDEX IF NOT EXISTS idx_graph_states_graph_id ON graph_states(graph_id);
CREATE INDEX IF NOT EXISTS idx_graph_states_thread_id ON graph_states(thread_id);
CREATE INDEX IF NOT EXISTS idx_graph_states_graph_thread ON graph_states(graph_id, thread_id);

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers to auto-update updated_at
DROP TRIGGER IF EXISTS update_graphs_updated_at ON graphs;
CREATE TRIGGER update_graphs_updated_at
    BEFORE UPDATE ON graphs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_graph_states_updated_at ON graph_states;
CREATE TRIGGER update_graph_states_updated_at
    BEFORE UPDATE ON graph_states
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Grant permissions (adjust if using different user)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO langgraph;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO langgraph;
