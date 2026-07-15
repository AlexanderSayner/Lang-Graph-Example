DROP INDEX IF EXISTS idx_graphs_definition_gin;
CREATE INDEX idx_graphs_definition_gin ON core.graphs USING GIN (definition);
