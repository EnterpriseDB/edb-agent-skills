-- diagnose_aidb.sql
-- Run this script against your aidb-enabled database to check the current state
-- of AIDB objects: extension presence, pipelines, models, semantic KBs, agents,
-- tools, and purposes. Safe to run at any time (read-only queries only).
--
-- Usage:
--   psql -d <your_database> -f diagnose_aidb.sql

\echo '=== AIDB Diagnostic Report ==='
\echo ''

-- 1. Check extension presence
\echo '--- Extension ---'
SELECT name, default_version, installed_version
FROM pg_available_extensions
WHERE name = 'aidb';

\echo ''
\echo '--- Registered Models ---'
SELECT * FROM aidb.list_models();

\echo ''
\echo '--- Pipelines ---'
SELECT * FROM aidb.list_pipelines();

\echo ''
\echo '--- Semantic Knowledge Bases ---'
SELECT * FROM aidb.list_semantic_kbs();

\echo ''
\echo '--- Agents ---'
SELECT name, model, purpose FROM aidb.agents;

\echo ''
\echo '--- Tools (native + SQL + MCP) ---'
SELECT name, description, tool_type FROM aidb.tools ORDER BY tool_type, name;

\echo ''
\echo '--- Volumes ---'
SELECT * FROM aidb.list_volumes();

\echo ''
\echo '--- Purpose Registry ---'
SELECT name, role, description, deleted_at FROM aidb.purpose_registry ORDER BY name;

\echo ''
\echo '--- GUC Settings (AIDB-relevant) ---'
SELECT name, setting, unit, context
FROM pg_settings
WHERE name LIKE 'aidb.%' OR name LIKE 'edb.endpoints%'
ORDER BY name;

\echo ''
\echo '=== End of AIDB Diagnostic Report ==='
