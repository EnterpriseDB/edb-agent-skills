# pgaa Troubleshooting & Gotchas

A curated reference for diagnosing the most common pgaa errors, misconfigurations, and behavioral surprises. Organized by symptom.

---

## Installation & Setup

### `CREATE EXTENSION pgaa` fails with "required extension pgfs is not installed"

Use `CASCADE` to auto-install the `pgfs` dependency:
```sql
CREATE EXTENSION pgaa CASCADE;
```

### pgaa tables return no results or queries fail immediately after installation on WarehousePG

**Cause:** `pgaa` is not listed in `shared_preload_libraries`.  
**Fix:** Add `pgaa` to `postgresql.conf` on the coordinator and all segment hosts, then restart the cluster:
```
shared_preload_libraries = 'pgaa'
```

### Seafowl does not start / queries fail with "connection refused" to localhost:47470

Check:
1. `SHOW pgaa.autostart_seafowl;` — if `off`, Seafowl won't auto-start. Set to `on` in `postgresql.conf`.
2. `SHOW pgaa.autostart_seafowl_port;` — confirms which port Seafowl listens on (default `47470`).
3. On WarehousePG, Seafowl runs as a systemd service (`seafowl.service`) rather than a background worker — check its status with `systemctl status seafowl`.
4. If using a manually-started external Seafowl: `SHOW pgaa.seafowl_url;` — ensure it points to the correct host/port.

---

## Table Creation

### `CREATE TABLE ... USING PGAA` with empty column list `()` — is that an error?

No. An **empty column list** is the signal for pgaa to **auto-discover the schema** from the lakehouse table (Iceberg catalog or storage location). This is the recommended pattern for catalog-managed tables. Providing explicit columns is also valid when schema pinning is desired.

### CTAS (`CREATE TABLE ... AS SELECT`) fails with "data already exists at target path"

Add `pgaa.purge_data_if_exists = true` to the `WITH (...)` clause:
```sql
CREATE TABLE analytics.result USING PGAA WITH (
    pgaa.storage_location      = 'my-store',
    pgaa.path                  = 'results/output/',
    pgaa.format                = 'delta',
    pgaa.purge_data_if_exists  = true
) AS SELECT * FROM source;
```

### CTAS is slow or the EXPLAIN plan shows PostgreSQL executor operations on the source

Expected behavior. **CTAS source queries always use CompatScan** (the PostgreSQL executor), never DirectScan. This is a known limitation — only the write side uses the lakehouse engine directly.

### `ALTER TABLE ... SET ACCESS METHOD pgaa` — what options are required?

Minimum required options depend on the storage pattern:

```sql
-- Catalog-managed conversion
ALTER TABLE public.my_table
    SET ACCESS METHOD pgaa,
    SET (
        pgaa.auto_truncate     = 'true',
        pgaa.format            = 'iceberg',
        pgaa.managed_by        = 'my_catalog',
        pgaa.catalog_namespace = 'public',
        pgaa.catalog_table     = 'my_table'
    );

-- Direct storage location conversion
ALTER TABLE public.my_table
    SET ACCESS METHOD pgaa,
    SET (
        pgaa.auto_truncate    = 'true',
        pgaa.format           = 'delta',
        pgaa.storage_location = 'my-location',
        pgaa.path             = 'public.my_table'
    );
```

`pgaa.auto_truncate = 'true'` is required on PGD to flush heap data when switching access methods.

---

## Catalog Management

### `pgaa.add_catalog()` fails immediately

`pgaa.add_catalog()` validates the connection before registering. Common causes:
- **Iceberg REST:** incorrect `url`, expired `token`, or wrong `warehouse` UUID. Use `pgaa.validate_catalog_connection()` to pre-check without registering.
- **Iceberg S3 Tables:** missing or incorrect `arn` or `region`.
- Self-signed TLS certificates: add `"danger_accept_invalid_certs": "true"` to catalog options (dev/test environments only).

```sql
-- Pre-validation (returns NULL on success, error string on failure)
SELECT pgaa.validate_catalog_connection(
    'iceberg-rest',
    '{"url":"https://my-catalog/api/iceberg", "token":"..."}'
);
```

### `pgaa.delete_catalog()` doesn't drop the catalog's tables

By default, `cascade` is `false`. To also drop managed tables:
```sql
SELECT * FROM pgaa.delete_catalog('my_catalog', cascade := true);
```

### Schema changes in the upstream catalog are not reflected in PostgreSQL

pgaa does **not** automatically apply schema evolution to existing PostgreSQL tables. After an upstream catalog table schema change:
```sql
ALTER TABLE analytics.my_table
    ADD COLUMN new_col TEXT,
    DROP COLUMN old_col;
```
You must manually `ALTER TABLE` in PostgreSQL to match the lakehouse schema.

### Catalog status shows `refresh_failed` or `refresh_retry`

Check the PostgreSQL log for the underlying error. Common causes:
- Network connectivity to the catalog endpoint.
- Expired or rotated token/credentials.
- Use `pgaa.test_catalog('my_catalog', false)` to probe without writes (returns NULL on success, error string on failure).
- Re-register with updated credentials via `pgaa.update_catalog('my_catalog', '{"token":"new-token"}'::json)`.

---

## Background Maintenance Tasks

### `pgaa.launch_task()` returns a UUID but the task never runs

**Most common cause:** `pgaa.enable_maintenance_worker` is `off` (the default).

```sql
-- Check current state
SHOW pgaa.enable_maintenance_worker;

-- Enable (requires postgresql.conf change and reload or restart)
ALTER SYSTEM SET pgaa.enable_maintenance_worker = on;
SELECT pg_reload_conf();
```

Without this GUC set to `on`, tasks queue indefinitely with `pending` status.

### `pgaa.launch_task()` with `task_type = 'zorder'` fails on an Iceberg table

`zorder`, `vacuum`, and `purge` task types are **Delta format only**. For Iceberg tables, only `compaction` is supported via `launch_task`. Use `pgaa.spark_sql()` (requires `spark_connect` engine) for zorder/vacuum/purge on Iceberg:

```sql
SET pgaa.executor_engine = 'spark_connect';
SET pgaa.spark_connect_url = 'sc://spark-host:15002';

SELECT pgaa.spark_sql(
    'CALL system.rewrite_data_files(table => "catalog.namespace.table_name")',
    'my_iceberg_catalog'
);
```

### Task status stuck at `running`

Check for worker crashes in the PostgreSQL log. A task may be stuck if the maintenance worker process was killed while running. Safe to re-launch; the previous task record stays in `pgaa.background_task` for audit purposes.

---

## PGD Replication

### Re-enabling replication wiped the analytics data

This is **expected behavior**: re-enabling replication triggers a full reload — all existing analytics data is removed, and the current local table data is re-uploaded. Warn users before calling `pgaa.enable_analytics_replication()` on a table that previously had replication enabled.

### `bdr.wait_slot_confirm_lsn()` is blocking indefinitely

This function blocks until all WAL records have been replicated. It **must only be called after all writes to the table have stopped**. If writes are ongoing when you call it, it will never return. There is no timeout — cancel with `pg_cancel_backend()` or `Ctrl-C` if called accidentally while writes are active.

### `pgaa.restore_from_analytics()` completed but storage costs are still high

`pgaa.restore_from_analytics()` **does not clean up files in object storage** — it only restores data to the local heap. To free object storage, manually delete the orphaned files using your cloud provider's console, CLI, or a tool like PyIceberg.

### PGD replication works but reads still come from heap

Ensure the session GUC is set:
```sql
SET bdr.prefer_analytics_engine = true;
```
This is a session-level setting. It can be set as a default in `postgresql.conf` or `ALTER ROLE ... SET` if always-on analytics reads are desired.

---

## Iceberg S3 Tables Limitations

Iceberg S3 Tables catalogs have the following hard restrictions in pgaa:
- **No PGD replication** (`pgd.replicate_to_analytics`)
- **No tiered tables** (`pgaa.convert_to_tiered_table`)
- **No analytics offload**

For any of these features, switch to an **Iceberg REST catalog**.

---

## Spark Connect Integration

### Spark queries return errors or stale results

- Confirm `pgaa.executor_engine = 'spark_connect'` and `pgaa.spark_connect_url` is set.
- Spark Connect is **read-only** — all writes must go through PostgreSQL (the Seafowl/pgaa write path).
- Spark returns results as a JSON object — parse appropriately in the application layer.

### Spark Iceberg equality deletes may be skipped

A known upstream behavior: equality deletes can be missed during concurrent reads with Spark. Workaround:
```sql
SELECT pgaa.spark_sql(
    'SET spark.sql.iceberg.executor-cache.enabled=false; SELECT ...',
    'my_catalog'
);
```

---

## Performance & Query Planning

### Queries are slow / not using DirectScan

Check:
```sql
SHOW pgaa.enable_direct_scan;         -- should be 'on'
SHOW pgaa.enable_join_pushdown;       -- should be 'on' for joins
SHOW pgaa.enable_groupby_pushdown;    -- should be 'on' for aggregations
```

If DirectScan is on but still using CompatScan, check `pgaa.direct_scan_fail_behavior`:
- `'warn'` (default): CompatScan silently used with a warning in logs
- `'error'`: Forces an error if DirectScan fails (useful for debugging)

### DataFusion tuning

Explore and set DataFusion-level parameters:
```sql
-- List all DataFusion options
SELECT name, setting, short_desc
FROM pg_settings
WHERE name LIKE 'pgaa.datafusion%';

-- Common tuning knobs
SET pgaa.datafusion.execution.target_partitions = 8;
SET pgaa.datafusion.execution.batch_size = 8192;
```

---

## Diagnostic Quick Reference

```sql
-- Version info
SELECT pgaa.pgaa_version();
SELECT pgaa.seafowl_version();
SELECT pgaa.engine_version();  -- whichever engine is currently selected

-- List all pgaa tables with storage/replication info
SELECT * FROM pgaa.list_analytics_tables();

-- Storage size for a specific table
SELECT * FROM pgaa.lakehouse_table_stats('schema.table_name'::regclass);

-- List all catalogs and their sync status
SELECT * FROM pgaa.list_catalogs();

-- Test a catalog connection (NULL = OK, string = error message)
SELECT pgaa.test_catalog('my_catalog', false);

-- Test a storage location
SELECT pgaa.test_storage_location('my-location', false);

-- Inspect background task status
SELECT id, task_type, status, created_at, finished_at
FROM pgaa.background_task
ORDER BY created_at DESC
LIMIT 20;

-- Check all relevant GUCs
SELECT name, setting
FROM pg_settings
WHERE name LIKE 'pgaa.%'
ORDER BY name;
```
