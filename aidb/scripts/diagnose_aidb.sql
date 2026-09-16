-- AIDB Diagnostic Script
-- Run this in psql to quickly diagnose the state of your AIDB installation.
-- Usage: psql -d <your_db> -f diagnose_aidb.sql

\echo '=== AIDB Diagnostic Report ==='
\echo ''

-- 1. Check extension version
\echo '--- Extension Version ---'
SELECT name, default_version, installed_version
FROM pg_available_extensions
WHERE name = 'aidb';

-- 2. Check GUC settings
\echo ''
\echo '--- GUC Configuration ---'
SELECT name, setting, unit, context
FROM pg_settings
WHERE name IN ('aidb.max_threads', 'aidb.pipeline_error_warnings')
ORDER BY name;

-- 3. List registered models
\echo ''
\echo '--- Registered Models ---'
SELECT * FROM aidb.list_models();

-- 4. List all pipelines
\echo ''
\echo '--- Pipelines ---'
SELECT * FROM aidb.list_pipelines();

-- 5. Check background workers
\echo ''
\echo '--- Background Workers (aidb) ---'
SELECT pid, backend_type, state, wait_event_type, wait_event
FROM pg_stat_activity
WHERE backend_type ILIKE '%aidb%' OR backend_type ILIKE '%bgworker%'
ORDER BY backend_type;

-- 6. List semantic knowledge bases
\echo ''
\echo '--- Semantic Knowledge Bases ---'
SELECT * FROM aidb.list_semantic_kbs();

-- 7. Check for pipeline errors (top 10 most recent across all pipelines)
\echo ''
\echo '--- Recent Pipeline Errors (sample query) ---'
\echo 'To check errors for a specific pipeline, run:'
\echo '  SELECT * FROM aidb.get_error_logs(''<pipeline_name>'') LIMIT 10;'

-- 8. Check shared_preload_libraries
\echo ''
\echo '--- shared_preload_libraries ---'
SHOW shared_preload_libraries;

-- 9. Check pgvector availability (required for KnowledgeBase step)
\echo ''
\echo '--- pgvector Extension ---'
SELECT name, installed_version
FROM pg_available_extensions
WHERE name = 'vector';

\echo ''
\echo '=== End of Diagnostic Report ==='
