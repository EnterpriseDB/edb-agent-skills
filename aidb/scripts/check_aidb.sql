-- check_aidb.sql
-- Diagnostic script for verifying aidb extension health.
-- Run with: psql -d <dbname> -f check_aidb.sql

\echo '=== AIDB Diagnostic Check ==='

\echo ''
\echo '--- 1. Extension version ---'
SELECT name, default_version, installed_version
FROM pg_available_extensions
WHERE name = 'aidb';

\echo ''
\echo '--- 2. Registered models ---'
SELECT name, provider FROM aidb.list_models();

\echo ''
\echo '--- 3. Registered pipelines ---'
SELECT name, auto_processing FROM aidb.list_pipelines();

\echo ''
\echo '--- 4. Registered semantic KBs ---'
SELECT * FROM aidb.list_semantic_kbs();

\echo ''
\echo '--- 5. GUC parameters ---'
SHOW aidb.max_threads;
SHOW aidb.pipeline_error_warnings;

\echo ''
\echo '--- 6. Quick smoke test (dummy model) ---'
DO $$
BEGIN
    -- Create a temp dummy model if not present
    BEGIN
        PERFORM aidb.create_model('_diag_dummy', 'dummy');
    EXCEPTION WHEN OTHERS THEN NULL;
    END;
    -- Test encode_text
    PERFORM aidb.encode_text('hello world', '_diag_dummy');
    RAISE NOTICE 'encode_text: OK';
    -- Test chunk_text
    PERFORM aidb.chunk_text('This is a test sentence for chunking.', aidb.chunk_text_config(10));
    RAISE NOTICE 'chunk_text: OK';
END $$;

\echo ''
\echo '=== Diagnostic complete ==='
