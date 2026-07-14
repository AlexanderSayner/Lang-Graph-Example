CREATE TABLE IF NOT EXISTS core.graphs
(
    graph_id    VARCHAR(255) PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    status      VARCHAR(50)              DEFAULT 'ACTIVE',
    definition  JSONB        NOT NULL,                 -- Nodes, edges, config
    coordinates JSONB                    DEFAULT '{}', -- UI positions: {"node_1": {"x": 100, "y": 200}}
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    version BIGINT DEFAULT 0
);
