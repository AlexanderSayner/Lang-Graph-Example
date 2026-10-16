-- Fixture for scripts/b2b_smoke.py
-- Idempotent: safe to re-run.  Inserts the four graphs the smoke test expects
-- into the Flyway-managed core.graphs table.  Run AFTER the app has booted once
-- (Flyway creates the core schema):
--
--   psql -h localhost -p 7432 -U langgraph -d langgraph_db \
--        -f scripts/seed_b2b_smoke.sql
--
-- Or via docker:
--
--   docker exec -i langgraph-postgres psql -U langgraph -d langgraph_db \
--        < scripts/seed_b2b_smoke.sql

INSERT INTO core.graphs (graph_id, name, status, definition)
VALUES
  ('graph-1', 'Smoke: graph 1', 'ACTIVE',
   '{"nodes":[{"nodeId":"start","nodeType":"START"},{"nodeId":"end","nodeType":"END"}],"edges":[]}'::jsonb),
  ('graph-2', 'Smoke: graph 2', 'ACTIVE',
   '{"nodes":[],"edges":[]}'::jsonb),
  ('graph-3', 'Smoke: graph 3', 'ACTIVE',
   '{"nodes":[],"edges":[]}'::jsonb),
  ('supplier-selection-demo', 'Smoke: other tenant', 'ACTIVE',
   '{"nodes":[],"edges":[]}'::jsonb)
ON CONFLICT (graph_id) DO UPDATE
SET name        = EXCLUDED.name,
    definition  = EXCLUDED.definition,
    updated_at  = NOW();