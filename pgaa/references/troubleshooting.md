# pgaa Troubleshooting Guide

This guide covers the most common failure modes when operating **EDB Postgres Analytics Accelerator (pgaa)**. Consult [guc-reference.md](guc-reference.md) for full GUC details and [function-reference.md](function-reference.md) for full SQL API signatures.

---

## 1. Query Returns 0 Rows or "Table Not Found" After Restore

**Symptom:** A PGAA table exists in `\dt` output, `SELECT count(*)` returns 0, but data is in object storage.

**Cause:** A `gprestore`-emitted `COPY pgaa.table_mapping FROM STDIN` was aborted due to a PK conflict (BEAC-1392). Since 1.10.0, the `table_mapping_restore_guard` trigger silently skips conflicting rows.

**Resolution:**
```sql
-- Check if a mapping exists:
SELECT * FROM pgaa.table_mapping
WHERE rel_namespace = 'myschema' AND rel_name = 'mytable';

-- If missing, re-register by dropping and recreating the table:
DROP TABLE myschema.mytable;
CREATE TABLE myschema.mytable (...) USING PGAA
WITH (pgaa.storage_location = 'loc', pgaa.path = 'path/to/data');
```

---

## 2. "Relation does not use the PGAA access method" / Wrong Results

**Symptom:** Queries succeed but return wrong results (e.g., heap data, stale data).

**Cause:** Table was created without `USING PGAA`, or a CTAS failed mid-way, leaving the table in heap mode.

**Verification:**
```sql
SELECT am.amname
FROM pg_class c
JOIN pg_am am ON c.relam = am.oid
WHERE c.relname = 'mytable';
-- Expected: pgaa
```

---

## 3. DirectScan Falls Back to CompatScan (Slow Queries)

**Symptom:** Queries are unexpectedly slow; `EXPLAIN` shows a custom scan path without full Seafowl delegation.

**Diagnosis:**
```sql
SET pgaa.direct_scan_fail_behavior = 'error';
-- Re-run the query; the error message will explain why DirectScan was bypassed.
```

**Common reasons for DirectScan fallback:**
- Query references tables from multiple catalogs (Spark Connect only supports single-catalog DirectScan unless schema-unqualified)
- Query uses unsupported operators or functions
- `pgaa.enable_direct_scan = off`

**Resolution:**
- Use `SET search_path = 'myschema'` and unqualify table names for Spark Connect multi-catalog queries
- Check all `enable_*_pushdown` GUCs are enabled (see [guc-reference.md](guc-reference.md))

---

## 4. Engine Not Reachable / Arrow Flight Errors

**Symptom:** Queries on PGAA tables fail with connection errors or gRPC/Flight errors.

**Checks:**
```sql
SELECT pgaa.engine_version();  -- confirms the engine is reachable
```

**For Seafowl:**
- Verify the embedded Seafowl background worker is running: check PostgreSQL logs for `seafowl` startup messages
- If using an external Seafowl: confirm `pgaa.seafowl_url` points to the correct endpoint

**For Spark Connect:**
- Confirm `pgaa.executor_engine = 'spark_connect'`
- Verify `pgaa.spark_connect_url` is reachable
- `pgaa.spark_sql()` only works when `executor_engine = 'spark_connect'`

---

## 5. Catalog Sync Not Picking Up New Tables

**Symptom:** Tables added to an Iceberg REST catalog are not appearing in PostgreSQL after `pgaa.attach_catalog()`.

**Checks:**
```sql
SELECT name, status, refreshed_at FROM pgaa.list_catalogs();
-- status should be 'attached'; refreshed_at should be recent
```

**Causes & Resolutions:**
| `status` value | Meaning | Action |
|----------------|---------|--------|
| `detached` | Sync not started | Run `SELECT pgaa.attach_catalog('mycat')` |
| `refresh_retry` | Transient error, retrying | Check logs; verify catalog credentials |
| `refresh_failed` | Persistent failure | Check catalog connection; call `pgaa.test_catalog('mycat', false)` |
| `attached` | Running normally | Check `pgaa.metastore_sync_poll_rate_s` — may not have polled yet |

```sql
-- Force a one-time import to pull tables immediately:
SELECT pgaa.import_catalog('mycat');

-- Test connectivity:
SELECT pgaa.test_catalog('mycat', false);
```

---

## 6. `pgaa.add_catalog()` Fails

**Symptom:** `Add catalog failed: <error>` when calling `pgaa.add_catalog()`.

**Cause:** pgaa validates the connection before inserting the catalog row. The error message will indicate the connectivity problem.

**Pre-validate without registering:**
```sql
SELECT pgaa.validate_catalog_connection(
    'iceberg-rest',
    '{"url": "https://my-catalog/api/catalog", "warehouse": "my-warehouse", "token": "my-token"}'
);
-- NULL = success; error string = failure reason
```

**Common issues:**
- Wrong URL or ARN
- Missing `"url"` for `iceberg-rest` or `"arn"` for `iceberg-s3tables`
- Invalid SSL certificate (add `"danger_accept_invalid_certs": "true"` for test environments only)
- Missing AWS credentials in environment for S3Tables

---

## 7. `pgaa.delete_catalog()` Fails with "Catalog is not empty"

**Symptom:** `ERROR: Catalog 'mycat' is not empty.`

**Cause:** The catalog has managed tables; `cascade` defaults to `false` to prevent accidental data loss.

**Resolution (choose one):**
```sql
-- Option A: Drop managed tables then delete catalog
SELECT pgaa.drop_catalog_tables('mycat', cascade := true);
SELECT pgaa.delete_catalog('mycat');

-- Option B: Delete catalog and drop managed tables in one step
SELECT pgaa.delete_catalog('mycat', cascade := true);
```

> ⚠️ `cascade := true` drops the **PostgreSQL** PGAA table definitions. It does **not** delete the underlying data files in object storage.

---

## 8. VACUUM Raises Error on PGAA Tables

**Symptom (pre-1.9.0):** `ERROR: cannot vacuum a PGAA table`

**Resolution:** Upgrade to pgaa ≥ 1.9.0. Since 1.9.0, `VACUUM` and `VACUUM FULL` emit a `NOTICE` and skip gracefully — PGAA data lives in object storage, not heap pages, so Postgres vacuum has nothing to do.

---

## 9. Replication Lag / Data Not Appearing in Analytics

**Symptom:** Newly inserted OLTP rows are not visible in analytics queries for an extended period.

**Diagnosis:**
```sql
SELECT table_name, replication_status, object_storage_snapshot_size_bytes
FROM pgaa.list_analytics_tables()
WHERE replication_status != 'disabled';
```

**Tuning:**
```sql
-- Check current lag threshold:
SHOW pgaa.max_replication_lag_s;
-- Reduce to flush more frequently (default: 5s):
SET pgaa.max_replication_lag_s = 2;

-- Check flush interval:
SHOW pgaa.flush_task_interval_s;
```

**Benchmark:**
```sql
-- Run a synthetic replication benchmark:
SELECT * FROM pgaa._bench_replication(
    storage_location => 'my-loc',
    format => 'iceberg',
    scale => 10,
    transactions => 1000
);
```

---

## 10. Background Task Stuck in `pending` or `running`

**Symptom:** `SELECT * FROM pgaa.background_task WHERE status IN ('pending', 'running')` shows stale entries.

**Cause:** The maintenance background worker may be disabled or crashed.

**Checks:**
```sql
SHOW pgaa.enable_maintenance_worker;  -- should be 'on'
```

Check PostgreSQL logs for maintenance worker errors. If the worker crashed:
- Restart PostgreSQL (the background worker will restart automatically)
- Or manually mark the task failed:

```sql
UPDATE pgaa.background_task
SET status = 'failure', output = '"manually cancelled"', finished_at = now()
WHERE id = '<task-uuid>' AND status = 'running';
```

---

## 11. `pgaa.spark_sql()` Returns Error

**Symptom:** `ERROR: pgaa.spark_sql is not supported with the current executor engine`

**Cause:** `pgaa.executor_engine` is not set to `spark_connect`.

```sql
SET pgaa.executor_engine = 'spark_connect';
SET pgaa.spark_connect_url = 'sc://my-spark-host:15002';
SELECT pgaa.spark_sql('SELECT 1', 'my-catalog');
```

---

## 12. Storage Location Write Errors

**Symptom:** `CTAS` or replication fails with S3/GCS/Azure access denied or bucket-not-found errors.

**Checks:**
```sql
SELECT pgaa.test_storage_location('my-loc', true);  -- true = also test writes
```

**Common causes:**
- Missing AWS/GCP environment variables for auth (see `README.md` environment variables section)
- Storage location path not allowed by `pgfs` security config
- Bucket/container does not exist or IAM permissions insufficient
