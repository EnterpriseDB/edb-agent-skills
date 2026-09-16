# pgaa Troubleshooting Guide

## Query Performance Issues

### Symptom: Analytical queries on PGAA tables are slow

**Diagnosis steps:**

1. Check which scan mode is being used:
   ```sql
   EXPLAIN SELECT * FROM my_analytics_table WHERE ...;
   -- Look for "Custom Scan (pgaa)" with DirectScan or CompatScan in the plan
   ```

2. Check pushdown GUCs:
   ```sql
   SHOW pgaa.enable_direct_scan;
   SHOW pgaa.enable_join_pushdown;
   SHOW pgaa.enable_groupby_pushdown;
   SHOW pgaa.enable_orderby_pushdown;
   SHOW pgaa.enable_window_pushdown;
   ```

3. Surface why DirectScan may be falling back:
   ```sql
   SET pgaa.direct_scan_fail_behavior = 'error';  -- or 'notice'
   -- Re-run query to see the reason
   ```

4. Verify engine is reachable:
   ```sql
   SELECT pgaa.engine_version();  -- NULL or error indicates engine is down
   ```

5. Check cost estimates:
   ```sql
   SHOW pgaa.scan_startup_cost;
   SHOW pgaa.scan_per_tuple_cost;
   SHOW pgaa.use_seafowl_cost_estimates;
   ```

### Symptom: DirectScan not working after search_path change

- Ensure schema-qualified table names all come from the same catalog, OR
- Unqualify table names and rely on `search_path`

---

## Catalog Connectivity

### Symptom: `pgaa.add_catalog()` fails with connection error

1. Pre-validate before registering:
   ```sql
   SELECT pgaa.validate_catalog_connection('iceberg-rest', '{"url": "...", "token": "..."}');
   -- NULL = success; error text = problem
   ```

2. Check catalog options format:
   - `iceberg-rest` requires `"url"` key
   - `iceberg-s3tables` requires `"arn"` key

3. For self-signed TLS in dev/test:
   ```json
   {"url": "...", "danger_accept_invalid_certs": "true"}
   ```

### Symptom: Attached catalog tables are stale / not refreshing

1. Check metastore sync worker is enabled:
   ```sql
   SHOW pgaa.enable_metastore_sync_worker;
   ```

2. Check catalog status:
   ```sql
   SELECT name, status, refreshed_at FROM pgaa.list_catalogs();
   -- status should be 'attached'; 'refresh_failed' means sync is failing
   ```

3. Adjust poll rate:
   ```sql
   -- In postgresql.conf or ALTER SYSTEM:
   pgaa.metastore_sync_poll_rate_s = 30
   ```

4. Force a one-time re-import:
   ```sql
   SELECT pgaa.import_catalog('my-catalog');
   ```

---

## Replication Issues

### Symptom: PGAA tables are behind / replication lag is high

1. Check replication status:
   ```sql
   SELECT table_name, replication_status FROM pgaa.list_analytics_tables();
   ```

2. Check lag settings:
   ```sql
   SHOW pgaa.max_replication_lag_s;
   SHOW pgaa.flush_task_interval_s;
   ```

3. Run a replication benchmark:
   ```sql
   SELECT pgaa.bench_replication(...);
   ```

### Symptom: `VACUUM` on a PGAA table raises a NOTICE

This is expected behavior. PGAA tables store data in object storage, not heap pages.
A NOTICE is emitted and the vacuum is skipped gracefully. This is not an error.

---

## Storage Location Issues

### Symptom: `CREATE TABLE ... USING PGAA` fails with storage location error

1. Verify the pgfs storage location exists:
   ```sql
   -- The pgfs skill/extension manages storage locations
   SELECT * FROM pgfs.list_storage_locations();
   ```

2. Test the storage location:
   ```sql
   SELECT pgaa.test_storage_location('my-location', true);  -- true = test writes too
   -- NULL = success
   ```

3. Ensure `pgaa.storage_location` in `WITH (...)` matches an existing pgfs location name.

---

## Extension / Engine Setup

### Symptom: `pgaa.spark_sql()` returns an error

- Verify `pgaa.executor_engine = 'spark_connect'`
- Verify `pgaa.spark_connect_url` points to a running Spark Connect endpoint
- `pgaa.spark_sql()` is **only** available when `executor_engine = 'spark_connect'`

### Symptom: Engine not reachable after install

1. Check that Seafowl is running (default engine):
   ```sql
   SELECT pgaa.engine_version();
   ```

2. If using autostart, confirm:
   ```sql
   SHOW pgaa.autostart_seafowl;
   SHOW pgaa.autostart_seafowl_port;
   ```

3. Review PostgreSQL logs for Seafowl startup errors.

4. For on-prem WarehousePG: Seafowl runs as a systemd service (`seafowl.service`).

---

## Table & Data Management

### Symptom: `pgaa.delete_catalog()` raises "Catalog is not empty"

By design — prevents accidental data loss. Use:
```sql
SELECT pgaa.delete_catalog('my-catalog', cascade := true);
-- or manually drop tables first:
SELECT pgaa.drop_catalog_tables('my-catalog');
SELECT pgaa.delete_catalog('my-catalog');
```

### Symptom: `pg_dump` / `gprestore` leaves PGAA tables empty

- As of 1.10.0, a `table_mapping_restore_guard` trigger prevents PK conflicts
  during `gprestore` COPY operations from causing silent data loss.
- For older versions, upgrade to 1.10.0+ before performing backup/restore.

### Symptom: INSERT into PGAA table returns an error

Direct `INSERT` into `USING PGAA` tables is not supported (returns a clear error as of 1.9.0).
Data gets into PGAA tables via:
- `CREATE TABLE ... USING PGAA AS (SELECT ...)` (CTAS)
- PGD logical replication / `pgaa.enable_analytics_replication()`
- Catalog sync (`attach_catalog` / `import_catalog`)

---

## Background Tasks

### Check task status:
```sql
SELECT id, type, status, started_at, finished_at, output
FROM pgaa.background_task
WHERE target_table = 'my_schema.my_table'::regclass
ORDER BY scheduled_at DESC;
```

### Wait for a task to complete:
```sql
SELECT pgaa._wait_for_task('<uuid>', poll_interval_seconds := 5.0);
```

### Task constraints:
- Only one `pending` task of each type per table (enforced by unique index)
- Only one `running` task per table at a time
