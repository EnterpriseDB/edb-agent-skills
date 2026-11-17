# pgaa Troubleshooting Reference

Quick lookup guide for common errors, unexpected behaviors, and debugging steps.

---

## Installation & Startup

### "extension pgfs does not exist"
`pgaa` requires `pgfs`. Always install with `CASCADE`:
```sql
CREATE EXTENSION pgaa CASCADE;
```

### pgaa not loading on WarehousePG (Greenplum)
`pgaa` must appear in `shared_preload_libraries` before PostgreSQL starts.
Add to `postgresql.conf`:
```
shared_preload_libraries = 'pgaa'
```
Then restart the server. Creating the extension without this results in a silent failure where the TAM is not registered.

### "Seafowl not reachable" / queries fail with connection refused
If `pgaa.autostart_seafowl = on` (default), pgaa manages Seafowl as a background worker. If the worker crashed:
1. Check `pg_log` for Seafowl startup errors.
2. Verify the binary is on `PATH` or set `pgaa.autostart_seafowl_path`.
3. Check whether port `47470` is already in use by another process.
4. Temporarily set `pgaa.autostart_seafowl = off` and point `pgaa.seafowl_url` at a manually started Seafowl instance.

---

## Table Creation

### "ERROR: cannot have an empty target list" or unexpected empty table
An empty column list in `CREATE TABLE t () USING PGAA` is intentional — pgaa auto-discovers the schema from the lakehouse table. This is not an error.

### CTAS fails with "data already exists at path"
Add `pgaa.purge_data_if_exists = true` to the `WITH (...)` clause:
```sql
CREATE TABLE ... USING PGAA WITH (
  ...,
  pgaa.purge_data_if_exists = true
) AS SELECT ...;
```

### ALTER TABLE to pgaa access method fails
Ensure you include the full set of required options. The minimal form is:
```sql
ALTER TABLE t SET ACCESS METHOD pgaa,
  SET (pgaa.auto_truncate = 'true', pgaa.format = 'iceberg', pgaa.managed_by = '...', ...);
```
The `pgaa.auto_truncate = 'true'` option is required when converting from heap to prevent data duplication.

---

## Query Execution

### Query returns no rows / table appears empty
1. Verify the storage path or catalog/namespace/table names are correct.
2. Run `SELECT pgaa.test_storage_location('loc_name', false)` or `SELECT pgaa.test_catalog('cat_name', false)` — `NULL` means reachable, any other value is an error message.
3. Check `EXPLAIN` output for the scan node type:
   - `SeafowlDirectScan` — query routed to Seafowl correctly.
   - `SeafowlCompatScan` — fallback path (slower, but still valid; expected on WarehousePG).
   - No Seafowl node at all — pgaa may not be active.

### EXPLAIN shows no Seafowl node
- Confirm `CREATE EXTENSION pgaa CASCADE` has been run.
- On WarehousePG, confirm `pgaa` is in `shared_preload_libraries`.
- Check `pgaa.enable_direct_scan` — if `off`, queries always use CompatScan.
- Check `pgaa.direct_scan_fail_behavior` — if `ignore`, DirectScan failures are silent.

### DirectScan falls back to CompatScan unexpectedly
DirectScan falls back when:
- The query contains constructs Seafowl cannot handle (e.g., certain window functions).
- `pgaa.enable_join_pushdown = off` or `pgaa.enable_groupby_pushdown = off`.
- The source query in a CTAS (always uses CompatScan by design).
- Running on WarehousePG (Greenplum's distributed executor prevents DirectScan).

To make DirectScan failures visible: `SET pgaa.direct_scan_fail_behavior = 'warn';` or `'error'`.

### Spark Connect queries fail with "read-only"
Spark Connect is read-only. Use `SET pgaa.executor_engine = 'seafowl'` for queries that need to write, or write through PostgreSQL directly.

---

## Catalog Management

### `pgaa.add_catalog()` fails with certificate errors
For self-signed certs (e.g., local Lakekeeper), pass `"danger_accept_invalid_certs": "true"` in the options JSON.

### Catalog shows `refresh_failed` status
1. Check connectivity: `SELECT pgaa.test_catalog('catalog_name', false)`.
2. Verify the token or OAuth2 credentials haven't expired.
3. Use `pgaa.update_catalog('catalog_name', '{"token": "new-token"}'::json)` to rotate credentials.

### `pgaa.delete_catalog()` with `cascade := false` doesn't remove tables
By design. Pass `cascade := true` to also drop dependent pgaa tables in PostgreSQL, or use `pgaa.drop_catalog_tables('catalog_name')` first, then delete the catalog.

### Schema changes in catalog not reflected in Postgres
Schema evolution in catalog-managed tables is not automatic. After an upstream schema change, run:
```sql
ALTER TABLE schema.table ADD COLUMN new_col <type>;
-- or drop/recreate the table to re-trigger auto-discovery
```

---

## PGD Replication

### Re-enabling replication wipes analytics data
This is by design — re-enabling performs a **full reload**. If you need to avoid data loss:
- Use `purge_analytics_target := false` and ensure no duplicate data issue exists.
- Or plan the re-enable during a maintenance window.

### `bdr.wait_slot_confirm_lsn()` hangs indefinitely
This function blocks if writes are still flowing. Only call it **after** all writes to the replicated table have been quiesced. Never use `pg_sleep()` as a substitute.

### Replication status stuck at `initial_offload`
The initial full reload is in progress. For large tables this can take minutes to hours. Monitor via:
```sql
SELECT schema_name, table_name, replication_status FROM pgaa.list_analytics_tables();
```

### S3 Tables catalog with PGD features fails
Iceberg S3 Tables catalogs do not support PGD replication, tiered tables, or partition offload. Use an Iceberg REST catalog (such as Lakekeeper) for any PGD-integrated workflow.

---

## Background Maintenance Tasks

### Tasks scheduled but never execute
`pgaa.enable_maintenance_worker` defaults to `off`. Enable it:
```sql
ALTER SYSTEM SET pgaa.enable_maintenance_worker = on;
SELECT pg_reload_conf();
```

### `pgaa.launch_task()` fails with "unsupported task type for iceberg"
For Iceberg tables, `launch_task` only supports `compaction`. Use `pgaa.spark_sql()` for `zorder`, `vacuum`, and `purge` on Iceberg tables.

### `pgaa.execute_compaction()` warning about deprecation
Replace with `pgaa.launch_task(table, 'compaction', options)`.

---

## Tiered Tables

### `convert_to_tiered_table()` errors on constraints
Check:
- Partition column is `DATE` or `TIMESTAMP` — `TIMESTAMPTZ` may need casting.
- Partition column is part of the primary key.
- Table has no sequences (use `BIGINT` with an application-generated ID, not `BIGSERIAL`).
- Table has no foreign key constraints (drop them first).

### `pgaa.restore_from_analytics()` leaves orphaned files
This is a known behavior: the procedure restores data to heap but does not clean up object storage. Clean up files manually using the cloud provider's storage tools, or via PyIceberg for catalog-managed tables.

---

## Storage Locations (pgfs)

### Orphaned pgaa table after deleting storage location
If you delete a pgfs storage location before dropping the dependent pgaa table, the table entry remains in `pg_class` but queries against it fail. Fix:
```sql
-- Force drop the orphaned table
DROP TABLE schema.orphaned_table CASCADE;
```

### Public bucket access denied
For public buckets (like the EDB TPC-H demo data), ensure you pass `{"aws_skip_signature": "true"}` in the options:
```sql
SELECT pgfs.create_storage_location(
  'my-loc', 's3://public-bucket/', '{"aws_skip_signature": "true"}'
);
```

---

## Version & Diagnostics

```sql
-- Full version info
SELECT pgaa.pgaa_version();

-- Active engine version
SELECT pgaa.engine_version();

-- List all pgaa GUCs
SELECT name, setting, unit, short_desc
FROM pg_settings
WHERE name LIKE 'pgaa%'
ORDER BY name;

-- List all DataFusion GUCs
SELECT name, setting, short_desc
FROM pg_settings
WHERE name LIKE 'pgaa.datafusion%'
ORDER BY name;
```
