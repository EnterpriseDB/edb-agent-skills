# pgaa Troubleshooting Guide

This reference is structured for an AI agent resolving common pgaa issues. For each symptom, read the diagnosis steps and apply the remediation.

---

## 1. Query Returns Error: "relation is not accessible via PGAA"

**Cause:** The offload engine (Seafowl or Spark Connect) is not running or not reachable.

**Diagnosis:**
```sql
-- Check engine version/connectivity
SELECT pgaa.engine_version();

-- Check which engine is configured
SHOW pgaa.executor_engine;
SHOW pgaa.seafowl_url;  -- if using external Seafowl
SHOW pgaa.spark_connect_url;  -- if using Spark Connect
```

**Remediation:**
- If using auto-start: ensure `pgaa.autostart_seafowl = true` in `postgresql.conf` and restart PostgreSQL.
- If using external Seafowl: verify the process is running at `pgaa.seafowl_url`.
- If using Spark Connect: verify the cluster is reachable at `pgaa.spark_connect_url`.

---

## 2. `pgaa.spark_sql()` Fails with "spark_connect engine not configured"

**Cause:** `pgaa.executor_engine` is not set to `'spark_connect'`.

**Remediation:**
```sql
-- Check current setting
SHOW pgaa.executor_engine;

-- Set it (requires superuser, and a Spark Connect endpoint)
ALTER SYSTEM SET pgaa.executor_engine = 'spark_connect';
ALTER SYSTEM SET pgaa.spark_connect_url = 'sc://your-spark-host:15002';
SELECT pg_reload_conf();
```

---

## 3. Slow Queries on PGAA Tables

**Diagnosis order:**

1. Check if DirectScan is being used:
```sql
-- Enable fail behavior to see why DirectScan is not used
SET pgaa.direct_scan_fail_behavior = 'error';
SELECT * FROM my_analytics_table WHERE ...;  -- will ERROR with reason if DirectScan fails
-- Reset after diagnosis
SET pgaa.direct_scan_fail_behavior = 'fallback';
```

2. Check which pushdowns are active:
```sql
SHOW pgaa.enable_direct_scan;
SHOW pgaa.enable_join_pushdown;
SHOW pgaa.enable_groupby_pushdown;
SHOW pgaa.enable_orderby_pushdown;
```

3. Check cost estimates:
```sql
SHOW pgaa.scan_startup_cost;
SHOW pgaa.scan_per_tuple_cost;
SHOW pgaa.use_seafowl_cost_estimates;
```

4. Check metadata statistics injection:
```sql
SHOW pgaa.enable_metadata_stats;
```

**Common fixes:**
- Enable `pgaa.use_seafowl_cost_estimates = true` to let the engine provide accurate row estimates.
- Enable `pgaa.enable_metadata_stats = true` for Iceberg/Delta/Parquet statistics injection.
- Reduce `pgaa.scan_startup_cost` if the planner avoids PGAA scans in favor of heap scans.
- Check if a pushdown is producing wrong results and disable that specific pushdown GUC.

---

## 4. `pgaa.add_catalog()` Fails with Connection Error

**Cause:** Network issue, wrong credentials, or bad URL.

**Safe pre-flight check (before registering):**
```sql
-- Test BEFORE registering (won't save anything)
SELECT pgaa.validate_catalog_connection(
    'iceberg-rest',
    '{"url": "https://catalog.example.com", "warehouse": "mywh", "token": "mytoken"}'::json
);
-- Returns NULL on success, error string on failure
```

**For an already-registered catalog:**
```sql
SELECT pgaa.test_catalog('my_catalog', false);
-- Returns NULL on success, error string on failure
```

---

## 5. Catalog Tables Not Appearing After `attach_catalog()`

**Cause:** The metastore sync worker is not running or has not yet polled.

**Diagnosis:**
```sql
-- Check catalog status
SELECT * FROM pgaa.list_catalogs();

-- Check if the sync worker is enabled
SHOW pgaa.enable_metastore_sync_worker;
SHOW pgaa.metastore_sync_poll_rate_s;
```

**Remediation:**
- Enable the sync worker: `ALTER SYSTEM SET pgaa.enable_metastore_sync_worker = true; SELECT pg_reload_conf();`
- Or do a one-time manual import instead: `SELECT pgaa.import_catalog('my_catalog');`
- Check PG logs for metastore-agent worker errors.

---

## 6. `pgaa.delete_catalog()` Fails with "Catalog is not empty"

**Cause:** Tables managed by the catalog still exist. By design, cascade is `false` by default to prevent accidental data loss.

**Options:**

```sql
-- Option 1: Drop managed tables first, then delete catalog
SELECT pgaa.drop_catalog_tables('my_catalog', false);
SELECT pgaa.delete_catalog('my_catalog');

-- Option 2: Delete catalog and drop all managed tables atomically
SELECT pgaa.delete_catalog('my_catalog', cascade => true);
```

> ⚠️ **Warning:** `cascade => true` drops all PGAA tables managed by the catalog. This does NOT delete data from the Iceberg catalog itself, only the local Postgres table definitions.

---

## 7. VACUUM Fails / Shows Warning on PGAA Tables

**Expected behavior since 1.9.0:** `VACUUM` and `VACUUM FULL` on PGAA tables emit a `NOTICE` and skip gracefully (data is in object storage, not heap). This is not an error.

For Iceberg compaction (equivalent of vacuum for object storage), use:
```sql
-- For Iceberg tables via Spark Connect:
CALL pgaa.execute_compaction('my_table');

-- Or schedule via background task:
SELECT pgaa.launch_task('my_table', 'compaction', '{"target_size": 134217728}'::jsonb);
```

---

## 8. Replication Lag / Data Freshness Issues

**Diagnosis:**
```sql
-- Check replication lag setting
SHOW pgaa.max_replication_lag_s;
SHOW pgaa.flush_task_interval_s;

-- Inspect analytics tables and their replication status
SELECT table_name, replication_status, object_storage_snapshot_size_bytes
FROM pgaa.list_analytics_tables();
```

**Remediation:**
- Lower `pgaa.max_replication_lag_s` (default 5s) if fresher data is needed.
- Check PG logs for replication writer errors.
- Use `pgaa._bench_replication()` to measure throughput baseline.

---

## 9. CTAS Fails / Creates Empty Table

**Known issues (as of current source):**
- CTAS with a non-existent storage location raises a clear error since 1.10.0.
- CTAS with zero rows creates an empty table (expected, not a bug since 1.8.0).
- CTAS into a catalog table is supported but not extensively tested.

**Syntax:**
```sql
CREATE TABLE new_table
USING PGAA
WITH (pgaa.storage_location = 'my-loc', pgaa.path = 'output/path')
AS (SELECT * FROM source_table);
```

---

## 10. Extension Won't Load / Missing pgfs

**Cause:** `pgaa` requires `pgfs`. Check that `pgfs` is installed first.

```sql
-- Check installed extensions
SELECT extname, extversion FROM pg_extension WHERE extname IN ('pgfs', 'pgaa');

-- Install in correct order if missing
CREATE EXTENSION IF NOT EXISTS pgfs;
CREATE EXTENSION IF NOT EXISTS pgaa;
```

---

## 11. DirectScan Not Working After search_path Change

**Known limitation (Spark Connect):** DirectScan only works for schema-qualified tables if all tables come from a single catalog AND their names in PostgreSQL match the catalog names. Workaround: set `search_path` so tables appear unqualified.

---

## 12. Check pgaa and Engine Versions

```sql
SELECT pgaa.pgaa_version();    -- PGAA build info + git commit
SELECT pgaa.engine_version();  -- Current engine (Seafowl or Spark) version info
```
