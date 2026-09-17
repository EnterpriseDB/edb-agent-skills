# pgaa Function Reference

All functions live in the `pgaa` schema. Cross-checked against pgaa's SQL migrations and internal product documentation.

---

## Creating Tables

```sql
-- Pattern 1: catalog-managed (recommended). Empty column list = auto-discover schema.
CREATE TABLE schema.name () USING PGAA WITH (
    pgaa.managed_by         = 'catalog_name',
    pgaa.catalog_namespace  = 'namespace',
    pgaa.catalog_table      = 'table_name',
    pgaa.format             = 'iceberg'   -- 'iceberg' | 'delta' | 'parquet' (parquet is read-only)
);

-- Pattern 2: direct storage location (created via the pgfs extension)
CREATE TABLE schema.name () USING PGAA WITH (
    pgaa.storage_location = 'location_name',
    pgaa.path             = 'path/within/location',
    pgaa.format           = 'delta'
);

-- CTAS: write query results into either pattern
CREATE TABLE schema.name USING PGAA WITH (..., pgaa.purge_data_if_exists = true) AS
SELECT ... ;
-- Source query does NOT use DirectScan; fails if data exists at the target path unless
-- pgaa.purge_data_if_exists = true.

-- Convert an existing heap table in place
ALTER TABLE schema.name
  SET ACCESS METHOD pgaa,
  SET (pgaa.auto_truncate = 'true', pgaa.format = 'iceberg', pgaa.managed_by = '...', ...);

-- Revert to heap
ALTER TABLE schema.name SET ACCESS METHOD heap;
```

**Table options (`WITH (...)`):**

| Option | Type | Description |
|---|---|---|
| `pgaa.managed_by` | text | Catalog name (catalog-managed tables) |
| `pgaa.catalog_namespace` | text | Namespace/schema within the catalog |
| `pgaa.catalog_table` | text | Table name within the catalog |
| `pgaa.storage_location` | text | pgfs storage location name (direct access) |
| `pgaa.path` | text | Path within the storage location |
| `pgaa.format` | enum | `iceberg`, `delta`, or `parquet` (read-only) |
| `pgaa.auto_truncate` | boolean | Truncate heap data when switching to the pgaa access method (PGD) |
| `pgaa.purge_data_if_exists` | boolean | Purge existing lakehouse data on CTAS |
| `pgaa.tiered_table` | boolean | Mark as a tiered table (PGD) |
| `pgd.replicate_to_analytics` | boolean | Enable PGD → Iceberg replication |
| `pgd.purge_analytics_target` | boolean | Purge before enabling replication (PGD 6.3+) |

---

## Catalog Management

```sql
pgaa.add_catalog(
    catalog_name     VARCHAR,
    catalog_type     pgaa.catalog_type,  -- 'iceberg-rest' | 'iceberg-s3tables'
    catalog_options  JSON
) -- returns TEXT (catalog name); validates the connection before registering

pgaa.update_catalog(catalog_name VARCHAR, new_options JSON)              -- returns TABLE(name TEXT)
pgaa.delete_catalog(catalog_name VARCHAR, cascade BOOLEAN DEFAULT false) -- returns TABLE(name TEXT)

pgaa.attach_catalog(catalog_name VARCHAR) -- returns VOID; starts continuous sync
pgaa.detach_catalog(catalog_name VARCHAR, cascade BOOLEAN DEFAULT false)
    -- returns TABLE(LIKE pgaa.catalog); cascade=true drops the catalog's tables first
pgaa.import_catalog(catalog_name VARCHAR, namespace_filter VARCHAR DEFAULT NULL)
    -- returns VOID; one-time import (vs. attach_catalog's continuous sync)

pgaa.list_catalogs() -- returns name, type (pgaa.catalog_type), options (json), created_at, refreshed_at, status (pgaa.catalog_status)
pgaa.list_catalog_tables(catalog_name TEXT, namespace_filter TEXT DEFAULT NULL) -- returns TABLE(schema_name, table_name)
pgaa.drop_catalog_tables(catalog_name VARCHAR, cascade BOOLEAN DEFAULT false)   -- returns TABLE(schema_name, table_name)

pgaa.test_catalog(name TEXT, test_writes BOOLEAN)                    -- returns TEXT (NULL on success)
pgaa.validate_catalog_connection(catalog_type VARCHAR, catalog_options JSON) -- returns TEXT (NULL on success); pre-registration check
```

**Iceberg REST catalog options JSON** — `url` (required), `warehouse`, `warehouse_name`, `token`, `danger_accept_invalid_certs`, `oauth2.grant_type`, `oauth2.client_id`, `oauth2.client_secret`, `oauth2.token_uri`, `oauth2.scope`.

**Iceberg S3 Tables catalog options JSON** — `arn` (required), `region` (required). S3 Tables catalogs do not support PGD features (tiered tables, replication, offload).

---

## Table & Storage Introspection

```sql
pgaa.list_analytics_tables()
-- returns: nspoid, reloid, schema_name, table_name, format (pgaa.table_format),
--   object_storage_snapshot_size_bytes, object_storage_total_size_bytes,
--   replication_status (pgaa.replication_status), storage_location_name, storage_location_path,
--   catalog_name, catalog_namespace, catalog_table

pgaa.lakehouse_table_stats(relation regclass) -- returns latest_snapshot_size, total_size
pgaa.test_storage_location(name TEXT, test_writes BOOLEAN) -- returns TEXT (NULL on success); tests a pgfs storage location

pgaa.pgaa_version()    -- returns TEXT: version + git SHA, build info, target platform, opt level, features
pgaa.seafowl_version()
pgaa.engine_version()  -- version info from whichever engine pgaa.executor_engine currently selects
```

---

## Tiered Tables

```sql
CALL pgaa.convert_to_tiered_table(
    relation                     regclass,
    range_partition_column       TEXT,      -- DATE/TIMESTAMP column, must be in the primary key
    partition_increment          TEXT,      -- e.g. '1 month', '1 day'
    analytics_offload_period     INTERVAL,  -- age threshold for moving to the cold tier
    initial_lower_bound          TEXT,
    retention_period             INTERVAL DEFAULT NULL,  -- purge threshold
    enable_replication           BOOLEAN  DEFAULT false, -- HTAP for hot partitions
    minimum_advance_partitions   INTEGER  DEFAULT 2,
    maximum_advance_partitions   INTEGER  DEFAULT 5,
    drop_after_retention_period  BOOLEAN  DEFAULT true,
    purge_analytics_target       BOOLEAN  DEFAULT false  -- PGD 6.3+
);
-- Constraints: partition column must be DATE/TIMESTAMP and part of the primary key;
-- no sequences or foreign keys allowed on the table.

pgaa.list_tiered_tables()
-- returns: schema_oid, table_oid, schema_name, table_name, partition_increment, retention,
--   analytics_offload, replication_enabled, tiered_data_size, untiered_data_size

CALL pgaa.convert_to_analytics(relation regclass);   -- HTAP table -> cold analytics table, removes local disk data
CALL pgaa.restore_from_analytics(relation regclass); -- restores to local heap; does NOT clean up object storage
```

---

## PGD Replication

```sql
CALL pgaa.enable_analytics_replication(relation regclass, purge_analytics_target BOOLEAN DEFAULT false);
CALL pgaa.disable_analytics_replication(relation regclass);
-- Equivalent: ALTER TABLE t SET (pgd.replicate_to_analytics = true/false)
-- Re-enabling performs a FULL RELOAD: removes existing analytics data, re-uploads current local data.

-- Read path:
-- SET bdr.prefer_analytics_engine = true;  -- route reads to Iceberg instead of heap

-- Wait for replication to catch up (ONLY when writes have stopped -- blocks indefinitely otherwise):
SELECT bdr.wait_slot_confirm_lsn(bdr.local_analytics_slot_name(), NULL);

-- Monitoring:
SELECT * FROM bdr.analytics_table;
SELECT schema_name, table_name, replication_status FROM pgaa.list_analytics_tables();
```

---

## Query Execution & Engines

```sql
-- Engine selection
SET pgaa.executor_engine = 'seafowl';        -- default, embedded DataFusion
SET pgaa.executor_engine = 'spark_connect';  -- external Spark cluster, read-only
SET pgaa.spark_connect_url = 'sc://host:15002';
SET pgaa.spark_connect_extra_config = '{"spark.sql.shuffle.partitions": "200"}';

pgaa.spark_sql(query TEXT, catalog TEXT)     -- single-catalog; requires executor_engine = 'spark_connect'
pgaa.spark_sql(query TEXT, catalogs TEXT[])  -- multi-catalog overload
-- Returns a JSON object with query results.
```

---

## Background Tasks

```sql
pgaa.launch_task(
    table_name    regclass,
    task_type     TEXT,               -- 'compaction' | 'zorder' | 'vacuum' | 'purge'
    task_options  JSONB     DEFAULT '{}'::jsonb,
    scheduled_at  TIMESTAMP DEFAULT NULL
) -- returns UUID (task id). Requires pgaa.enable_maintenance_worker = on for tasks to run.
```

| Task type | Formats | Notes |
|---|---|---|
| `compaction` | delta, iceberg | Options: `target_size`, `preserve_insertion_order`, `max_concurrent_tasks`, `max_spill_size`, `min_commit_interval`, `dry_run` |
| `zorder` | delta only | Options: `columns` (array), `target_size`, `preserve_insertion_order`, `max_concurrent_tasks`, `max_spill_size`, `min_commit_interval` |
| `vacuum` | delta only | Options: `retention_period`, `dry_run`, `enforce_retention_duration` |
| `purge` | delta only | Options: `storage_location`, `path` |

For Iceberg, `launch_task` only supports `compaction` — use `pgaa.spark_sql()` for zorder/vacuum/purge on Iceberg. `pgaa.execute_compaction()` is **deprecated**; use `launch_task` instead.

```sql
SELECT * FROM pgaa.background_task WHERE id = 'task-uuid';
-- status: pending | running | success | failure
```

---

## Administrative Enums

| Type | Values |
|---|---|
| `pgaa.table_format` | `delta`, `iceberg`, `parquet` |
| `pgaa.catalog_type` | `iceberg-rest`, `iceberg-s3tables` |
| `pgaa.catalog_status` | `detached`, `attached`, `refresh_retry`, `refresh_failed` |
| `pgaa.replication_status` | `disabled`, `initial_offload`, `enabled` |
| `pgaa.task_status` | `pending`, `running`, `success`, `failure` |

---

## GUC Parameters

**Executor engine**

| Parameter | Default | Description |
|---|---|---|
| `pgaa.executor_engine` | `seafowl` | `seafowl` or `spark_connect` |
| `pgaa.seafowl_url` | `http://localhost:47470` | Seafowl endpoint |
| `pgaa.spark_connect_url` | (none) | Spark Connect endpoint (`sc://host:port`) |
| `pgaa.spark_connect_extra_config` | (none) | JSON config for the Spark session |

**Query behavior / cost estimation**

| Parameter | Default | Description |
|---|---|---|
| `pgaa.enable_direct_scan` | `on` | Offload entire query to Seafowl (DirectScan) vs. PostgreSQL executor fallback (CompatScan) |
| `pgaa.direct_scan_fail_behavior` | `warn` | `ignore` \| `warn` \| `error` |
| `pgaa.enable_join_pushdown` | `on` | Push joins to the remote executor |
| `pgaa.enable_groupby_pushdown` | `on` | Push GROUP BY to the remote executor |
| `pgaa.use_seafowl_cost_estimates` | `on` | Use real-time Seafowl cost estimates |
| `pgaa.scan_startup_cost` | `25` | Fixed startup cost multiplier |
| `pgaa.scan_per_tuple_cost` | `0.02` | Per-row scan cost multiplier |
| `pgaa.scan_aggregation_cost_factor` | `0.005` | Aggregation cost multiplier |

**Embedded Seafowl autostart**

| Parameter | Default | Description |
|---|---|---|
| `pgaa.autostart_seafowl` | `on` | Auto-start the Seafowl worker |
| `pgaa.autostart_seafowl_port` | `47470` | Listen port |
| `pgaa.autostart_seafowl_enable_metrics` | `on` | Enable Prometheus metrics |
| `pgaa.autostart_seafowl_metrics_host` / `_port` | `0.0.0.0` / `9090` | Metrics bind address/port |
| `pgaa.autostart_seafowl_enable_object_store_cache` | `on` | Enable object cache |
| `pgaa.autostart_seafowl_object_store_cache_ttl_s` | `3600` | Cache TTL |
| `pgaa.autostart_seafowl_object_store_cache_capacity_mb` | `1024` | Cache size |

**Writer & replication**

| Parameter | Default | Description |
|---|---|---|
| `pgaa.max_replication_lag_s` | `5` | Max lag before flush |
| `pgaa.max_in_memory_mb` | `3072` | Writer memory buffer |
| `pgaa.ctas_intermediate_flush_size` | `8192` | Batch size for CTAS |

**Maintenance & catalog sync**

| Parameter | Default | Description |
|---|---|---|
| `pgaa.enable_maintenance_worker` | `off` | Must be `on` for `launch_task` tasks to execute |
| `pgaa.maintenance_worker_sleep_interval` | `30` | Check interval (seconds) |
| `pgaa.enable_metastore_sync_worker` | `on` | Background catalog metadata sync |
| `pgaa.metastore_sync_poll_rate_s` | `60` | Catalog polling interval |
| `pgaa.lakehouse_table_stats_cache_ttl_s` | `300` | Table stats cache TTL |

**DataFusion pass-through**

Exposed under `pgaa.datafusion.*` — list available options with `SELECT name, setting, short_desc FROM pg_settings WHERE name LIKE 'pgaa.datafusion%'`. Key ones: `pgaa.datafusion.execution.target_partitions`, `pgaa.datafusion.execution.batch_size`, `pgaa.datafusion.execution.parquet.*`.

---

## Data Type Mapping

**Reading:** natively supported (direct binary): `boolean`, `bytea`, `char`, `smallint`, `integer`, `bigint`, `oid`, `real`, `double precision`, `numeric`, `date`, `timestamp`, `timestamptz`, `interval`, and arrays of the numeric/boolean types. Coerced via text conversion: `text`, `varchar`, `char(n)`, `json`, `jsonb`, `uuid`, enums, text/uuid arrays, everything else.

**Writing:** same native/coerced split, with `numeric` natively supported on the write path too.
