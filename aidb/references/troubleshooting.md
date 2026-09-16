# AIDB Troubleshooting & Debugging Reference

This document covers common failure patterns, diagnostics, and resolution steps.

---

## Installation & Extension Issues

### Extension Not Loading / Workers Not Starting

**Symptom:** `CREATE EXTENSION aidb CASCADE` fails, or background workers never appear in `pg_stat_activity`.

**Check `shared_preload_libraries`:**
```sql
SHOW shared_preload_libraries;
```
Must include `'aidb'`. Edit `postgresql.conf` and restart PostgreSQL:
```ini
shared_preload_libraries = 'aidb'
```

**Check pg dependencies are installed:**
```sql
SELECT name, installed_version FROM pg_available_extensions
WHERE name IN ('vector', 'pgfs');
```
`pgvector` is required for `KnowledgeBase` steps. `pgfs` is required for volume operations.

### Extension Version Mismatch

```sql
SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name = 'aidb';

-- Upgrade if needed:
ALTER EXTENSION aidb UPDATE;
```

---

## Pipeline Issues

### Pipeline Not Processing in Background/Live Mode

```sql
-- Check pipeline mode:
SELECT name, auto_processing FROM aidb.list_pipelines();

-- Check background workers:
SELECT pid, backend_type, state FROM pg_stat_activity
WHERE backend_type ILIKE '%aidb%';

-- Manually trigger for testing:
SELECT aidb.run_pipeline('my_pipeline');
```

If background workers are absent: verify `shared_preload_libraries = 'aidb'` and restart PostgreSQL.

### Pipeline Creation Fails — "destination table already exists"

The destination table must **not** exist at pipeline creation time.
```sql
-- Drop existing destination table first:
DROP TABLE IF EXISTS my_destination_table;
-- Then recreate the pipeline:
SELECT aidb.delete_pipeline('my_pipeline', cascade => TRUE);
SELECT aidb.create_pipeline(...);
```

### Pipeline Creation Fails — "name too long"

Pipeline names are limited to **46 characters**.

### Pipeline Step Sequence Rejected

Steps must be sequenced so output type matches input type of the next step. See [step-operations.md](step-operations.md) for the compatibility table. Common mistake: trying to feed a `Text` column into `ParsePdf` (which requires `Bytes`/BYTEA).

### Viewing Pipeline Errors

```sql
-- Get errors for a specific pipeline:
SELECT * FROM aidb.get_error_logs('my_pipeline') ORDER BY last_seen_at DESC LIMIT 20;

-- Re-queue failed items after fixing the root cause:
SELECT aidb.requeue_pipeline_errors('my_pipeline');
```

### Disabled-Mode Volume Retry Gap

For `Disabled` mode pipelines on large volumes, after `aidb.requeue_pipeline_errors()`:
1. Switch to `Background` mode: `SELECT aidb.update_pipeline('my_pipeline', auto_processing => 'Background');`
2. Wait for the worker to process marked files
3. Switch back: `SELECT aidb.update_pipeline('my_pipeline', auto_processing => 'Disabled');`

---

## Model Issues

### Model Registration Fails — Validation Error

Use `validate => false` to skip provider validation during setup:
```sql
SELECT aidb.create_model('my_model', 'openai_embeddings',
    config => aidb.embeddings_config(model => 'text-embedding-3-small', api_key => 'sk-...'),
    validate => false
);
```

### Model Inference Fails

```sql
-- Test embedding model directly:
SELECT aidb.encode_text('test sentence', 'my_model');

-- Test completion model directly:
SELECT aidb.summarize_text('short text', aidb.summarize_text_config(model => 'my_llm'));

-- Check if model is cached:
SELECT * FROM aidb.list_models();

-- Evict from cache (forces reload):
SELECT aidb.remove_cached_model('my_model');
```

For local models:
- Verify `aidb.max_threads` is set high enough (requires PostgreSQL restart)
- Ensure sufficient system memory
- Check model file path is accessible from the database host

### Credential Issues

Credentials are stored in `pg_user_mappings`. If you suspect a credential problem:
```sql
-- Re-register model with updated credentials:
-- (This updates the existing registration)
SELECT aidb.create_model('my_model', 'openai_embeddings',
    config => aidb.embeddings_config(model => 'text-embedding-3-small'),
    credentials => '{"api_key": "sk-new-key"}'::JSONB
);
```

---

## Semantic Knowledge Base Issues

### KB Not Updating After Schema Changes

```sql
-- Manually refresh:
SELECT aidb.refresh_semantic_kb('my_kb');

-- Check KB stats:
SELECT * FROM aidb.semantic_kb_stats('my_kb');

-- Check processing mode:
SELECT * FROM aidb.list_semantic_kbs();
```

### Search Returns No Results

Lower the `min_similarity` threshold:
```sql
-- Start with 0.5 for broad exploration:
SELECT * FROM aidb.get_metadata('my_kb', 'customer data', 0.5, 20, 0);
```

---

## Agent Hub Issues

### Agent Conversation Fails

`agent_converse` **never raises an error** — check the `error` column:
```sql
SELECT conversation_id, message, error
FROM aidb.agent_converse('my_agent', 'What tables store orders?');
```

### Agent Tool Not Available

```sql
-- List all available tools:
SELECT * FROM aidb.tools;

-- Refresh MCP tool cache if using MCP servers:
SELECT aidb.refresh_mcp_tools('my_mcp_server');
```

### Agent Exceeds Budget

Budget strategies (set at agent creation):
- `ignore` — continues past budget with a warning
- `error` — halts when budget exceeded
- `summarize` — asks model for a closing summary
- `attempt_complete` — grants 3 grace iterations, then halts

---

## Volume Operations

### Volume File Access Denied (Local Storage)

Set the `pgfs.allowed_local_fs_paths` GUC in `postgresql.conf`:
```ini
pgfs.allowed_local_fs_paths = '/data/documents'
```

### Cloud Volume Credentials

Verify credentials are properly set in the volume's foreign server options. Check:
```sql
SELECT * FROM aidb.list_volumes();
```

---

## Performance Issues

### Local Model Inference Slow

Increase `aidb.max_threads` in `postgresql.conf` (requires restart):
```ini
aidb.max_threads = 8   # Default: half of available CPUs
```

### Connection Pool Exhaustion

Background worker connections may accumulate. Restart PostgreSQL if connections don't release.

### Similarity Search Slow

Add a vector index at pipeline creation:
```sql
-- HNSW (better for most use cases):
vector_index => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)

-- IVFFlat (faster build, may be less accurate):
vector_index => aidb.vector_index_ivfflat_config(lists => 100)
```

---

## Debug Logging

Enable verbose logging in `postgresql.conf`:
```ini
log_min_messages = debug1
client_min_messages = debug1
```

Or per session:
```sql
SET client_min_messages = debug1;
```

---

## Quick Diagnostic Checklist

Run `scripts/diagnose_aidb.sql` for a full system overview, or use these targeted checks:

```sql
-- 1. Extension installed?
SELECT installed_version FROM pg_available_extensions WHERE name = 'aidb';

-- 2. Workers running?
SELECT backend_type, state FROM pg_stat_activity WHERE backend_type ILIKE '%aidb%';

-- 3. Models registered?
SELECT * FROM aidb.list_models();

-- 4. Pipelines configured?
SELECT name, auto_processing FROM aidb.list_pipelines();

-- 5. Recent errors?
-- (For each pipeline of interest):
SELECT * FROM aidb.get_error_logs('<pipeline_name>') LIMIT 5;

-- 6. GUC settings?
SELECT name, setting FROM pg_settings WHERE name LIKE 'aidb.%';
```
