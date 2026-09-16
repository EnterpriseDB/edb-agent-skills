# AIDB Troubleshooting & Debugging Guide

## Quick Diagnostic Script

Run `scripts/check_aidb.sql` against any database to verify extension health:

```bash
psql -d mydb -f scripts/check_aidb.sql
```

---

## Extension Installation

```sql
-- Install (requires pgvector and pgfs extensions available)
CREATE EXTENSION aidb CASCADE;

-- Verify installed version
SELECT name, default_version, installed_version
FROM pg_available_extensions WHERE name = 'aidb';

-- Upgrade
ALTER EXTENSION aidb UPDATE;
```

**If `CREATE EXTENSION` fails:**
- Ensure `shared_preload_libraries = 'aidb'` in `postgresql.conf` (restart required)
- Verify pgvector, pgfs, and VectorChord are installed in the instance
- Check PostgreSQL version is 14–18

---

## Pipeline Not Processing

### Background worker not running
```sql
-- Verify pipeline mode
SELECT name, auto_processing FROM aidb.list_pipelines();

-- Check background workers are active
SELECT pid, backend_type, state FROM pg_stat_activity
WHERE backend_type LIKE '%background worker%';

-- Force manual run for testing
SELECT aidb.run_pipeline('my_pipeline');
```

**Checklist:**
1. `shared_preload_libraries = 'aidb'` in `postgresql.conf` → restart required
2. Pipeline is set to `Background` or `Live` mode (not `Disabled`)
3. Check error log: `SELECT * FROM pipeline_<name>_errors ORDER BY last_seen_at DESC;`

### Checking error logs
Each pipeline has a dedicated error table:
```sql
-- List recent errors for a pipeline
SELECT source_id, error_message, error_category, last_seen_at, retry_count
FROM pipeline_my_pipeline_errors
ORDER BY last_seen_at DESC
LIMIT 20;

-- Requeue failed rows for retry
SELECT aidb.requeue_pipeline_errors('my_pipeline');
```

**Error table invariants (v7.6.0+):**
- One row per `(source_id, part_ids, pipeline_step, step_operation)`
- `ON CONFLICT DO UPDATE` merges concurrent failures
- `failed_at` and `retry_count` preserve across identical repeated failures

### Disabled-mode + volume retry gap
For large Disabled-mode volume pipelines, `requeue_pipeline_errors` + `run_pipeline` re-reads the **entire** volume. Workaround:
1. Switch to Background mode: `SELECT aidb.update_pipeline('my_pipeline', auto_processing => 'Background');`
2. Call `SELECT aidb.requeue_pipeline_errors('my_pipeline');`
3. Wait for background worker to process marked rows
4. Switch back to Disabled mode if desired

---

## Model Failures

### Loading errors
```sql
-- Test a model directly
SELECT aidb.encode_text('hello', 'my_embed_model');     -- for embedding models
-- (generate_text function for completion models)

-- Check model registration
SELECT name, provider FROM aidb.list_models();

-- Note: list_models() NEVER returns credentials (stored in pg_user_mappings)
```

**Common causes:**
- Wrong API key (stored in `pg_user_mappings`, not directly queryable)
- Local model: wrong path or insufficient RAM
- `aidb.max_threads` too low for concurrent local inference (requires restart to change)
- Network unreachable for remote providers

### Thread pool for local models
```sql
-- Check current setting (requires PostgreSQL restart to change)
SHOW aidb.max_threads;

-- Set in postgresql.conf or via superuser:
-- aidb.max_threads = 8
```

---

## Semantic Knowledge Base Issues

### KB not finding expected tables/columns
```sql
-- Check KB status
SELECT * FROM aidb.list_semantic_kbs();
SELECT * FROM aidb.semantic_kb_stats('my_kb');

-- Refresh embeddings after schema changes
SELECT aidb.refresh_semantic_kb('my_kb');

-- Lower similarity threshold for broader results
SELECT * FROM aidb.get_tables('my_kb', 'customers', 0.3, 20, 0);
```

### `semantic_kb_search` not found (pre-7.5.0)
Use the individual search functions (`get_tables`, `get_columns`, etc.) instead of `semantic_kb_search`.

---

## Agent Hub Issues

### `agent_converse` returns an error
`agent_converse` NEVER raises a SQL error. Always check the `error` column:
```sql
SELECT message, conversation_id, error
FROM aidb.agent_converse('my_agent', 'Hello?');
-- If error IS NOT NULL, inspect it for the failure reason
```

**Common errors:**
- Model not found: check `aidb.list_models()`
- Tool execution failure: check `aidb.tools` view
- Budget exceeded: check agent's budget configuration
- Delegation depth exceeded (> 11 levels)

### Agent not calling tools
```sql
-- Verify tool is visible to the agent
SELECT * FROM aidb.tools WHERE name = 'my_tool';

-- Test tool directly
SELECT aidb.run_tool('my_tool', '{"param": "value"}'::jsonb);

-- MCP tool cache may be stale (expires after 60 minutes)
SELECT aidb.refresh_mcp_tools('my_mcp_server');
```

### Read-only enforcement layers
1. Static: SQL command tag check (SELECT, EXPLAIN, COPY TO, PREPARE = read-only)
2. Tool allow-list: MCP tools always blocked in read-only mode
3. PostgreSQL: `SET LOCAL transaction_read_only = on` inside subtransaction

---

## Volume Operations Failing

```sql
-- Check volume registration
SELECT * FROM aidb.list_volumes();

-- Local volumes: ensure pgfs.allowed_local_fs_paths includes your path
-- Cloud volumes: verify FDW credentials in volume options
```

---

## Connection Pool Exhaustion

Background worker connections may not release in certain scenarios. If connections accumulate, restart PostgreSQL.

---

## Pipeline State Table Details

Each pipeline has a state table named `aidb.aidb_pipeline_state_<pipeline_id>`:
- Tracks pending / in-progress / completed / failed rows
- Under PGD: gains `_<pgd_node_group>` suffix
- Keyed by pipeline ID (integer), not pipeline name
- Background workers poll this table for batch processing

---

## Error Log Index Issues (dev environments on pre-7.6.0 builds)

If you see missing unique indexes after a `7.5.0 → 7.6.0` migration that didn't run:
```sql
-- Option 1: Full reset (destructive)
DROP EXTENSION aidb CASCADE;
CREATE EXTENSION aidb CASCADE;

-- Option 2: Manual index creation per pipeline
CREATE UNIQUE INDEX IF NOT EXISTS aidb_eidx_<id>_item_unique
    ON <schema>.pipeline_<name>_errors (source_id, COALESCE(part_ids, '{}'::BIGINT[]), pipeline_step, step_operation)
    WHERE source_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS aidb_eidx_<id>_pipeline_unique
    ON <schema>.pipeline_<name>_errors (pipeline_step, step_operation)
    WHERE source_id IS NULL;
```

---

## Performance Tuning

| Concern | Recommendation |
|---|---|
| Slow local model inference | Increase `aidb.max_threads` (restart required); ensure adequate RAM |
| Large batch processing delay | Use `Background` mode with tuned `batch_size` in `update_pipeline` |
| High similarity search latency | Add HNSW index via `aidb.vector_index_hnsw_config()` in pipeline |
| Many concurrent pipelines | Each pipeline's bgworker uses its own connection; monitor pg_stat_activity |

---

## Checking `aidb` Version

```sql
SELECT installed_version
FROM pg_available_extensions
WHERE name = 'aidb';
```

Current codebase: v7.7.0
